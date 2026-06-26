#!/usr/bin/env python3
"""Maintain a SQLite log of successful WabiSabi coinjoin txs for fee stats.

Source of truth for txids: the coordinator log "Successfully broadcast" lines.
Per-tx numbers come from bitcoind (authoritative), via JSON-RPC using the creds
already in the coordinator Config.json. No third-party deps, stdlib only.

Configuration (environment variables, all optional):
  COINJOIN_COORD_CONFIG  path to the coordinator Config.json
                         (default: ./coordinator/Config.json)
  COINJOIN_COORD_LOG     path to the coordinator Logs.txt
                         (default: ./coordinator/Logs.txt)
  COINJOIN_DB            path to the SQLite database file
                         (default: ./coinjoin.db)

Commands:
  sync   create table if needed, scan the log, insert any txids not yet stored
         (idempotent; only new txids hit bitcoind).
  stats  print aggregate mining-fee / coordinator-fee numbers.
  latest show the latest N coinjoin rounds.
  lowest show the 5 lowest fee-rate coinjoin rounds.

coordinator_fee_sat = sum of outputs paying a coordinator scrap address, i.e.
wpkh(CoordinatorExtPubKey/0/i). These are the "scraps" (anonset-1 unique-amount
output) the coordinator wallet collects per round.
"""
import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import urllib.request

# ponytail: paths come from env so this is publishable; defaults are relative
CONF = os.environ.get("COINJOIN_COORD_CONFIG", "./coordinator/Config.json")
LOG = os.environ.get("COINJOIN_COORD_LOG", "./coordinator/Logs.txt")
DB = os.environ.get("COINJOIN_DB", "./coinjoin.db")

BROADCAST_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*Successfully broadcast the coinjoin: "
    r"(?P<txid>[0-9a-f]{64})"
)


def load_config():
    with open(CONF, encoding="utf-8-sig") as f:
        c = json.load(f)
    user, _, pw = c["BitcoinRpcConnectionString"].partition(":")
    return {
        "rpc_url": c.get("MainNetBitcoinRpcUri", "http://localhost:8332"),
        "rpc_user": user,
        "rpc_pass": pw,
        "xpub": c["CoordinatorExtPubKey"],
        "depth": int(c.get("CoordinatorExtPubKeyCurrentDepth", 0)),
    }


def rpc(cfg, method, params):
    body = json.dumps({"jsonrpc": "1.0", "id": "cjstats", "method": method,
                       "params": params}).encode()
    auth = base64.b64encode(f"{cfg['rpc_user']}:{cfg['rpc_pass']}".encode()).decode()
    req = urllib.request.Request(cfg["rpc_url"], data=body,
                                 headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if out.get("error"):
        raise RuntimeError(f"{method}: {out['error']}")
    return out["result"]


def coordinator_addresses(cfg):
    """Set of coordinator scrap addresses wpkh(xpub/0/*) up to current depth."""
    desc = f"wpkh({cfg['xpub']}/0/*)"
    checksummed = rpc(cfg, "getdescriptorinfo", [desc])["descriptor"]
    end = cfg["depth"] + 50  # buffer past the last used index
    return set(rpc(cfg, "deriveaddresses", [checksummed, [0, end]]))


def tx_stats(cfg, txid, coord_set):
    d = rpc(cfg, "getrawtransaction", [txid, 2])
    coord = 0
    for v in d["vout"]:
        spk = v["scriptPubKey"]
        if spk.get("address") in coord_set:
            coord += round(v["value"] * 1e8)
    mining_fee_sat = round(d["fee"] * 1e8)
    vsize = d["vsize"]  # verbosity 2 always includes vsize
    return {
        "n_inputs": len(d["vin"]),
        "n_outputs": len(d["vout"]),
        "mining_fee_sat": mining_fee_sat,
        "coordinator_fee_sat": coord,
        "block_time": d.get("blocktime"),
        "feerate_sat_vb": round(mining_fee_sat / vsize, 2),
    }


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS coinjoins (
        txid                TEXT PRIMARY KEY,
        broadcast_time      TEXT,
        block_time          INTEGER,
        n_inputs            INTEGER NOT NULL,
        n_outputs           INTEGER NOT NULL,
        mining_fee_sat      INTEGER NOT NULL,
        coordinator_fee_sat INTEGER NOT NULL,
        feerate_sat_vb      REAL)""")
    # migrate pre-existing tables that lack the feerate column
    cols = {r[1] for r in db.execute("PRAGMA table_info(coinjoins)")}
    if "feerate_sat_vb" not in cols:
        db.execute("ALTER TABLE coinjoins ADD COLUMN feerate_sat_vb REAL")
    return db


def parse_log():
    """Yield (txid, broadcast_time) for every broadcast line, oldest first."""
    if not os.path.exists(LOG):
        return
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = BROADCAST_RE.match(line)
            if m:
                yield m.group("txid"), m.group("ts")


def backfill_feerate(cfg, db):
    """Fill feerate_sat_vb for rows stored before the column existed."""
    missing = [r[0] for r in db.execute(
        "SELECT txid FROM coinjoins WHERE feerate_sat_vb IS NULL")]
    filled = 0
    for txid in missing:
        try:
            d = rpc(cfg, "getrawtransaction", [txid, 2])
            fr = round(round(d["fee"] * 1e8) / d["vsize"], 2)
        except Exception as e:
            print(f"skip feerate {txid}: {e}", file=sys.stderr)
            continue
        db.execute("UPDATE coinjoins SET feerate_sat_vb=? WHERE txid=?", (fr, txid))
        filled += 1
    if filled:
        db.commit()
        print(f"backfilled feerate for {filled} row(s)")


def cmd_sync(_args):
    cfg = load_config()
    db = connect()
    backfill_feerate(cfg, db)
    have = {r[0] for r in db.execute("SELECT txid FROM coinjoins")}
    todo = [(t, ts) for t, ts in parse_log() if t not in have]
    # de-dupe txids that appear on multiple log lines, keep first (oldest) time
    seen, ordered = set(), []
    for t, ts in todo:
        if t not in seen:
            seen.add(t)
            ordered.append((t, ts))
    if not ordered:
        print("up to date, nothing new")
        return
    coord_set = coordinator_addresses(cfg)
    added = 0
    for txid, ts in ordered:
        try:
            s = tx_stats(cfg, txid, coord_set)
        except Exception as e:  # one bad/missing tx must not abort the batch
            print(f"skip {txid}: {e}", file=sys.stderr)
            continue
        db.execute(
            "INSERT OR IGNORE INTO coinjoins (txid, broadcast_time, block_time, "
            "n_inputs, n_outputs, mining_fee_sat, coordinator_fee_sat, feerate_sat_vb) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (txid, ts, s["block_time"], s["n_inputs"], s["n_outputs"],
             s["mining_fee_sat"], s["coordinator_fee_sat"], s["feerate_sat_vb"]))
        added += 1
    db.commit()
    print(f"added {added} coinjoin(s)")


def cmd_stats(_args):
    db = connect()
    row = db.execute("""SELECT COUNT(*), COALESCE(SUM(mining_fee_sat),0),
        COALESCE(AVG(mining_fee_sat),0), COALESCE(SUM(coordinator_fee_sat),0),
        COALESCE(AVG(n_inputs),0), COALESCE(AVG(n_outputs),0),
        MIN(broadcast_time), MAX(broadcast_time),
        COALESCE(AVG(feerate_sat_vb),0)
        FROM coinjoins""").fetchone()
    n = row[0]
    if not n:
        print("no coinjoins recorded yet — run sync")
        return
    print(f"coinjoins recorded : {n}")
    print(f"range              : {row[6]}  ..  {row[7]}")
    print(f"total mining fee   : {row[1]:,} sat ({row[1]/1e8:.8f} BTC)")
    print(f"avg mining fee     : {row[2]:,.0f} sat")
    print(f"coordinator scraps : {row[3]:,} sat ({row[3]/1e8:.8f} BTC)")
    print(f"avg inputs/outputs : {row[4]:.1f} / {row[5]:.1f}")
    print(f"avg fee rate       : {row[8]:.2f} sat/vB")


def cmd_latest(args):
    db = connect()
    rows = db.execute(
        "SELECT broadcast_time, txid, n_inputs, n_outputs, mining_fee_sat, "
        "coordinator_fee_sat, feerate_sat_vb FROM coinjoins "
        "ORDER BY broadcast_time DESC LIMIT ?", (args.n,)).fetchall()
    if not rows:
        print("no coinjoins recorded yet — run sync")
        return
    print(f"latest {len(rows)} coinjoin(s):")
    for bt, txid, nin, nout, fee, coord, fr in rows:
        frs = f"{fr:.2f}" if fr is not None else "?"
        print(f"  {bt}  {txid}")
        print(f"    in/out {nin}/{nout}  mining {fee:,} sat  "
              f"coord {coord:,} sat  {frs} sat/vB")


def cmd_lowest(_args):
    db = connect()
    rows = db.execute(
        "SELECT broadcast_time, txid, n_inputs, n_outputs, mining_fee_sat, "
        "coordinator_fee_sat, feerate_sat_vb FROM coinjoins "
        "WHERE feerate_sat_vb IS NOT NULL "
        "ORDER BY feerate_sat_vb ASC LIMIT 5").fetchall()
    if not rows:
        print("no coinjoins recorded yet — run sync")
        return
    print("top 5 lowest fee-rate coinjoin(s):")
    for bt, txid, nin, nout, fee, coord, fr in rows:
        print(f"  {bt}  {txid}")
        print(f"    in/out {nin}/{nout}  mining {fee:,} sat  "
              f"coord {coord:,} sat  {fr:.2f} sat/vB")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="scan coordinator log, store new coinjoins")
    sub.add_parser("stats", help="print fee statistics")
    p_latest = sub.add_parser("latest", help="show the latest N coinjoin rounds")
    p_latest.add_argument("n", nargs="?", type=int, default=5,
                          help="number of rounds to show (default 5)")
    sub.add_parser("lowest", help="show the 5 lowest fee-rate coinjoin rounds")
    args = ap.parse_args()
    {"sync": cmd_sync, "stats": cmd_stats, "latest": cmd_latest,
     "lowest": cmd_lowest}[args.cmd](args)


if __name__ == "__main__":
    main()
