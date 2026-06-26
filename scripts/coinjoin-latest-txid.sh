#!/bin/bash
# Publishes the latest successfully broadcast coinjoin txid as JSON for the
# coinjoin.nl index page to render. Sourced from the WabiSabi coordinator log.
LOG=/home/wasabi/.walletwasabi/coordinator/Logs.txt
OUT=/var/www/coinjoin/latest-coinjoin.json
TXFLOW_OUT=/var/www/coinjoin/latest.html
MARKER=/var/www/coinjoin/.latest-txid

line=$(grep "Successfully broadcast the coinjoin" "$LOG" 2>/dev/null | tail -1)
txid=$(printf '%s' "$line" | grep -oE 'coinjoin: [0-9a-f]{64}' | grep -oE '[0-9a-f]{64}')
ts=$(printf '%s' "$line" | grep -oE '^[0-9-]{10} [0-9:]{8}')

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
