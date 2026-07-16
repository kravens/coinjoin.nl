#!/usr/bin/env python3
# -*- coding: utf-8 -*- ##########  F E E - B U M P  ·  coinjoin.nl  ###########
#  CPFP-bump the coordinator wallet's stuck coinjoin "scraps".                  #
#                                                                              #
#  Coordination fees land in the coordinator's Wasabi wallet as coinjoin       #
#  outputs. When a round stays unconfirmed a long time, we sweep those pending #
#  coordinator UTXOs into ONE child transaction that pays a higher fee (CPFP),  #
#  dragging the parent round(s) into a block.                                   #
#                                                                              #
#  We deliberately DON'T bump immediately: letting rounds sit in the mempool    #
#  a while stops users re-mixing the same coins with each other over and over   #
#  (no privacy gain, wasted fees). Only coins pending >= MIN_AGE_HOURS qualify.  #
#                                                                              #
#  The total fee the child pays is capped by MAX_FEE_SATS so a fee spike can't  #
#  drain the coordinator. Dry-run by default; pass --broadcast to actually send.#
#                                                                              #
#  Setup:  Wasabi Config.json "JsonRpcServerEnabled": true, run the daemon.     #
#  Run:    python3 fee-bump.py --wallet Coordinator [--broadcast]               #
#          password: --password, or env WASABI_WALLET_PASSWORD                  #
################################################################################
import sys, os, json, math, argparse, urllib.request, urllib.error
from datetime import datetime, timezone

# ---- config (all overridable on the command line) ---------------------------------
MIN_AGE_HOURS = 24        # only bump coins pending at least this long
MAX_FEE_SATS  = 2100      # hard ceiling on the child tx's total fee
DUST_SATS     = 330       # refuse if the swept output would be below this

# vsize upper bounds (P2WPKH input 68 > P2TR keypath 57.5; P2TR output 43 > P2WPKH 31).
# Overestimating keeps the real fee <= feeRate*est_vsize <= MAX_FEE_SATS.
_VB_OVERHEAD, _VB_PER_IN, _VB_OUT = 11, 68, 43


# ---- minimal Wasabi daemon JSON-RPC client (mirrors sabi.py's WasabiRpc) -----------
class RpcError(Exception): pass

class WasabiRpc:
    def __init__(self, url, user=None, password=None):
        self.url = url.rstrip("/"); self.user = user; self.password = password
    def call(self, method, params=None, wallet=None, timeout=30):
        target = self.url + ("/" + urllib.request.quote(wallet) + "/" if wallet else "")
        body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": method,
                           "params": params if params is not None else []}).encode()
        req = urllib.request.Request(target, data=body,
              headers={"Content-Type": "text/plain;", "User-Agent": "fee-bump/1.0 (coinjoin.nl)"})
        if self.user:
            import base64
            tok = base64.b64encode(f"{self.user}:{self.password or ''}".encode()).decode()
            req.add_header("Authorization", "Basic " + tok)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 401: raise RpcError("HTTP 401 (authentication required)")
            try: resp = json.loads(e.read().decode())
            except Exception: raise RpcError(f"HTTP {e.code}")
        except Exception as e:
            raise RpcError(str(e) or type(e).__name__)
        if isinstance(resp, dict) and resp.get("error"):
            raise RpcError(str(resp["error"].get("message", resp["error"])))
        return resp.get("result") if isinstance(resp, dict) else resp


# ---- helpers -----------------------------------------------------------------------
def age_hours(iso):                                   # ISO8601 -> hours since, or None
    if not iso: return None
    try:
        dt = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return None

def fastest_feerate(feerates):                        # {conf_target: sat/vB} -> highest rate
    if not isinstance(feerates, dict) or not feerates: return None
    try: return float(feerates[min(feerates, key=lambda k: int(k))])
    except Exception: return None

def plan_bump(n_in, total_sats, target_rate, max_fee, dust):
    """Pure fee math: pick a capped feeRate and estimated fee for the child sweep.
    Returns (feerate, est_vsize, est_fee) or raises RpcError with the reason."""
    est_vsize = _VB_OVERHEAD + _VB_PER_IN * n_in + _VB_OUT
    cap_rate = max_fee / est_vsize
    feerate = min(target_rate, cap_rate)
    if feerate < 1.0:
        raise RpcError(f"cap too low: {max_fee} sats over ~{est_vsize} vB ({n_in} inputs) "
                       f"can't reach 1 sat/vB — raise MAX_FEE_SATS or wait for fewer inputs")
    feerate = math.floor(feerate * 100) / 100.0       # floor to 0.01 sat/vB, never exceed the cap
    est_fee = math.ceil(feerate * est_vsize)
    assert est_fee <= max_fee, (est_fee, max_fee)     # guaranteed by construction
    if total_sats - est_fee < dust:
        raise RpcError(f"scraps too small: {total_sats} sats can't pay a ~{est_fee}-sat bump "
                       f"and leave >= {dust} sats")
    return feerate, est_vsize, est_fee


# ---- self-test (no network): fee cap + guards --------------------------------------
def _selftest():
    fr, vs, fee = plan_bump(3, 50_000, 5.0, MAX_FEE_SATS, DUST_SATS)
    assert fee <= MAX_FEE_SATS and fr >= 1.0
    # cap binds when many inputs: fee must still respect the ceiling
    fr, vs, fee = plan_bump(20, 500_000, 50.0, MAX_FEE_SATS, DUST_SATS)
    assert fee <= MAX_FEE_SATS, fee
    assert fr == math.floor((MAX_FEE_SATS / vs) * 100) / 100.0
    # too many inputs for the cap -> refuse rather than underpay
    try: plan_bump(200, 10_000_000, 10.0, MAX_FEE_SATS, DUST_SATS); assert False
    except RpcError: pass
    # scraps smaller than the bump -> refuse
    try: plan_bump(1, 400, 5.0, MAX_FEE_SATS, DUST_SATS); assert False
    except RpcError: pass
    print("selftest ok")


# ---- main --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="CPFP-bump stuck coordinator coinjoin scraps.")
    ap.add_argument("--wallet", help="coordinator wallet name (required unless --selftest)")
    ap.add_argument("--rpc", default="http://127.0.0.1:37128", help="daemon JSON-RPC url")
    ap.add_argument("--user"); ap.add_argument("--pass", dest="password_rpc")
    ap.add_argument("--password", default=os.environ.get("WASABI_WALLET_PASSWORD", ""),
                    help="wallet password (or env WASABI_WALLET_PASSWORD)")
    ap.add_argument("--min-age-hours", type=float, default=MIN_AGE_HOURS)
    ap.add_argument("--max-fee-sats", type=int, default=MAX_FEE_SATS)
    ap.add_argument("--broadcast", action="store_true", help="actually send (default: dry-run)")
    ap.add_argument("--selftest", action="store_true", help="run offline fee-math checks and exit")
    a = ap.parse_args()

    if a.selftest: _selftest(); return 0
    if not a.wallet: ap.error("--wallet is required")

    rpc = WasabiRpc(a.rpc, a.user, a.password_rpc)
    w = a.wallet

    coins = rpc.call("listunspentcoins", [], wallet=w) or []
    pending = [c for c in coins if not c.get("confirmed") and int(c.get("confirmations", 0)) == 0]
    if not pending:
        print("no pending coordinator coins — nothing to bump."); return 0

    # age each pending coin by its parent tx's first-seen time (from gethistory)
    hist = rpc.call("gethistory", [], wallet=w) or []
    seen = {str(h.get("tx")): h.get("datetime") for h in hist}
    old = []
    for c in pending:
        ah = age_hours(seen.get(str(c.get("txid"))))
        if ah is not None and ah >= a.min_age_hours:
            old.append(c)
    if not old:
        younger = len(pending)
        print(f"{younger} coin(s) pending but none older than {a.min_age_hours}h — "
              f"letting them sit (good for privacy)."); return 0

    n_in = len(old)
    total = sum(int(c.get("amount", 0)) for c in old)
    target_rate = fastest_feerate(rpc.call("getfeerates", [], wallet=w))
    if not target_rate:
        print("could not read fee rates from daemon — aborting."); return 1

    try:
        feerate, est_vsize, est_fee = plan_bump(n_in, total, target_rate,
                                                a.max_fee_sats, DUST_SATS)
    except RpcError as e:
        print(f"skip: {e}"); return 1

    print(f"bump candidates : {n_in} coin(s), {total:,} sats total, all pending >= {a.min_age_hours}h")
    print(f"target fee rate : {target_rate:.2f} sat/vB (daemon fastest)")
    print(f"child fee rate  : {feerate:.2f} sat/vB  ·  ~{est_vsize} vB  ·  ~{est_fee:,} sats "
          f"(cap {a.max_fee_sats:,})")

    # ponytail: child pays feeRate on ITSELF, not the true CPFP package rate. Good enough to
    # nudge stuck rounds within a fixed budget. To target an exact package rate, query the
    # node's getmempoolentry for each parent's fee/vsize and size the child fee to the deficit
    # (still bounded by MAX_FEE_SATS). Not worth it until a fixed budget proves too blunt.

    dest = (rpc.call("getnewaddress", ["fee-bump CPFP"], wallet=w) or {}).get("address")
    if not dest:
        print("could not get a destination address — aborting."); return 1
    params = dict(
        payments=[dict(sendto=dest, amount=total, label="fee-bump CPFP", subtractFee=True)],
        coins=[dict(transactionid=c.get("txid"), index=c.get("index")) for c in old],
        feeRate=feerate, password=a.password)

    if not a.broadcast:
        r = rpc.call("build", params, wallet=w) or {}   # build validates without broadcasting
        txid = r.get("txid") if isinstance(r, dict) else None
        print(f"DRY-RUN: built ok{(' txid ' + str(txid)) if txid else ''}. "
              f"Re-run with --broadcast to send.")
        return 0

    r = rpc.call("send", params, wallet=w) or {}
    txid = r.get("txid") if isinstance(r, dict) else str(r)
    print(f"broadcast CPFP child: {txid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
