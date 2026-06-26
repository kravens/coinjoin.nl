#!/bin/bash
# One-time: wait for electrs to finish indexing, then backfill the historical
# coinjoin rounds whose coordinator scraps were already spent (not in the UTXO
# set, so unreachable via scantxoutset). Exits after a successful run.
ping='{"id":1,"method":"server.ping","params":[]}'
while true; do
  if printf '%s\n' "$ping" | timeout 12 python3 -c 'import socket,sys;s=socket.create_connection(("127.0.0.1",50001),5);s.sendall(sys.stdin.buffer.read());s.settimeout(10);sys.exit(0 if s.recv(256) else 1)' 2>/dev/null; then
    echo "electrs ready $(date -Is), running history backfill"
    python3 /usr/local/bin/coinjoin-history-backfill.py
    exit 0
  fi
  echo "electrs not ready $(date -Is), retry in 5m"
  sleep 300
done
