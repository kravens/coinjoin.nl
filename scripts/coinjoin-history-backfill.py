#!/usr/bin/env python3
"""One-time backfill: recover ALL historical coinjoin txids from the coordinator
xpub via electrs address history, then store them in the stats DB.

Every coinjoin round pays a "scrap" to wpkh(CoordinatorExtPubKey/0/i), so every
such address's electrs history contains its coinjoin tx (as an output). We gather
those txs and keep the ones that actually have a coordinator output (coord_fee>0),
which excludes coordinator consolidation-spends. Reuses coinjoin-stats.py.
"""
import hashlib
import json
import socket
import sys

sys.path.insert(0, "/usr/local/bin")
import importlib
cj = importlib.import_module("coinjoin-stats")

ELECTRS = ("127.0.0.1", 50001)
EXTRA = 60  # derive this many indexes past the current depth, for safety


def electrum_call(sock_file, sock, rid, method, params):
    sock.sendall((json.dumps({"id": rid, "method": method, "params": params}) + "\n").encode())
    return json.loads(sock_file.readline())


def main():
    cfg = cj.load_config()
    end = cfg["depth"] + EXTRA
    print(f"deriving coordinator addresses wpkh(xpub/0/*) [0,{end}] ...")
    desc = cj.rpc(cfg, "getdescriptorinfo", [f"wpkh({cfg['xpub']}/0/*)"])["descriptor"]
    addrs = cj.rpc(cfg, "deriveaddresses", [desc, [0, end]])
    coord_set = set(addrs)

    # address -> scripthash (electrum: sha256(scriptPubKey) reversed)
    print(f"resolving scriptPubKeys for {len(addrs)} addresses ...")
    scripthashes = []
    for a in addrs:
        spk = cj.rpc(cfg, "validateaddress", [a])["scriptPubKey"]
        h = hashlib.sha256(bytes.fromhex(spk)).digest()[::-1].hex()
        scripthashes.append(h)

    print("querying electrs history ...")
    s = socket.create_connection(ELECTRS, timeout=30)
    f = s.makefile("r")
    electrum_call(f, s, 0, "server.version", ["cjstats", "1.4"])
    candidates = set()
    for i, sh in enumerate(scripthashes):
        r = electrum_call(f, s, i + 1, "blockchain.scripthash.get_history", [sh])
        for item in r.get("result", []):
            candidates.add(item["tx_hash"])
    s.close()
    print(f"{len(candidates)} candidate txs from history")

    db = cj.connect()
    have = {row[0] for row in db.execute("SELECT txid FROM coinjoins")}
    import datetime
    added = skipped = 0
    for txid in candidates:
        if txid in have:
            continue
        try:
            st = cj.tx_stats(cfg, txid, coord_set)
        except Exception as e:
            print(f"skip {txid}: {e}", file=sys.stderr)
            continue
        if st["coordinator_fee_sat"] <= 0:
            skipped += 1   # not a coinjoin (e.g. coordinator consolidation spend)
            continue
        bt = st["block_time"]
        ts = (datetime.datetime.utcfromtimestamp(bt).strftime("%Y-%m-%d %H:%M:%S")
              if bt else None)
        db.execute("INSERT OR IGNORE INTO coinjoins (txid, broadcast_time, "
                   "block_time, n_inputs, n_outputs, mining_fee_sat, "
                   "coordinator_fee_sat, feerate_sat_vb) VALUES (?,?,?,?,?,?,?,?)",
                   (txid, ts, bt, st["n_inputs"], st["n_outputs"],
                    st["mining_fee_sat"], st["coordinator_fee_sat"],
                    st["feerate_sat_vb"]))
        added += 1
    db.commit()
    print(f"added {added} coinjoins, skipped {skipped} non-coinjoin txs")


if __name__ == "__main__":
    main()
