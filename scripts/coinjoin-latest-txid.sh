#!/bin/bash
# Publishes the latest successfully broadcast coinjoin txid as JSON for the
# coinjoin.nl index page to render. Sourced from the durable stats DB that
# coinjoin-stats.py maintains — the coordinator's Logs.txt truncates too fast
# (WW 2.8.0 error spam) to read reliably, but the DB survives rotation.
# Run this AFTER `coinjoin-stats.py sync` so the DB is current.
DB=/var/lib/coinjoin-stats/coinjoin.db
OUT=/var/www/coinjoin/latest-coinjoin.json
TXFLOW_OUT=/var/www/coinjoin/latest.html
MARKER=/var/www/coinjoin/.latest-txid

row=$(sqlite3 "$DB" "SELECT txid,broadcast_time FROM coinjoins ORDER BY broadcast_time DESC LIMIT 1;" 2>/dev/null)
txid=${row%%|*}
ts=${row#*|}

if [ -n "$txid" ]; then
    printf '{"txid":"%s","time":"%s"}\n' "$txid" "$ts" > "$OUT"
    # Re-render the txflow animation only when the round actually changed
    # (each render fetches from mempool.space and writes a ~4 MB HTML).
    if [ "$txid" != "$(cat "$MARKER" 2>/dev/null)" ]; then
        if /usr/bin/python3 /home/admin/txflow.py "$txid" --mempool http://localhost:4080 --export "$TXFLOW_OUT" 2>/dev/null; then
            printf '%s' "$txid" > "$MARKER"
        fi
    fi
fi
