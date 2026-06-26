# coinjoin.nl tooling

Scripts, systemd units, nginx config and the landing page for the
[coinjoin.nl](https://coinjoin.nl) WabiSabi coordinator. Runs on a RaspiBlitz
box (aarch64). No secrets in this repo — RPC creds, SSL keys and wallet files
live outside it (see [Secrets](#secrets)).

## Layout

```
scripts/   Python/bash tooling
systemd/   service + timer that drive the scripts
nginx/     site + mempool reverse-proxy config (cert paths only, no keys)
web/       the static landing page
```

## What it does

The landing page renders the **latest successful coinjoin round** (a txflow
animation + an esplora-style viewer iframe) and a **latest-rounds table** with
per-round fee stats — refreshed every 5 minutes by a systemd timer.

```
wasabi-coinjoin-latest.timer  (every 5 min)
        └─> wasabi-coinjoin-latest.service  (oneshot, 3 steps)
              1. coinjoin-latest-txid.sh   greps the coordinator log for the last
                                           "Successfully broadcast" txid → writes
                                           web/latest-coinjoin.json, and (only when
                                           the txid changed) renders web/latest.html
                                           via txflow.py against the self-hosted
                                           mempool (http://localhost:4080).
              2. coinjoin-stats.py sync    stores new rounds in the SQLite fee DB
                                           (per-tx numbers from bitcoind JSON-RPC).
              3. coinjoin-stats.py latest 10 > web/latest-stats.txt  (table source)
```

`web/index.html` fetches `latest-coinjoin.json`, `latest.html` and
`latest-stats.txt` client-side and renders them.

## Scripts

| File | Purpose |
|------|---------|
| `txflow.py` | Animate a Bitcoin tx flow from mempool.space or a self-hosted mempool. `txflow.py <txid> --mempool http://localhost:4080 --export out.html`. |
| `coinjoin-stats.py` | SQLite log of successful coinjoins for fee stats (stdlib only). Subcommands: `sync`, `stats`, `latest [N]`, `lowest`. **Paths come from env vars** (`COINJOIN_COORD_CONFIG`, `COINJOIN_COORD_LOG`, `COINJOIN_DB`). The copy installed at `/usr/local/bin/` hardcodes box paths (`/var/lib/coinjoin-stats/coinjoin.db`, `/home/wasabi/.walletwasabi/coordinator/`); this env-driven copy is the publishable one. |
| `coinjoin-latest-txid.sh` | Publishes the latest broadcast txid as JSON and renders the txflow animation. |
| `coinjoin-history-backfill.py` | One-time historical backfill of the fee DB via electrs (`blockchain.scripthash.get_history` per coordinator scrap address). |
| `coinjoin-history-wait.sh` | Waiter that polls electrs and runs the backfill once it is indexed (used by a transient `systemd-run` unit). |

## Install on the box

```sh
sudo cp scripts/coinjoin-stats.py scripts/coinjoin-latest-txid.sh \
        scripts/coinjoin-history-*.{py,sh} /usr/local/bin/
sudo cp scripts/txflow.py /home/admin/txflow.py
sudo cp systemd/wasabi-coinjoin-latest.* /etc/systemd/system/
sudo cp nginx/coinjoin.nl.conf /etc/nginx/sites-available/
sudo cp nginx/mempool.conf /etc/nginx/snippets/
sudo cp web/index.html /var/www/coinjoin/ && sudo chown www-data:www-data /var/www/coinjoin/index.html
sudo systemctl daemon-reload && sudo systemctl enable --now wasabi-coinjoin-latest.timer
sudo nginx -t && sudo systemctl reload nginx
```

> The `coinjoin-stats.py` installed at `/usr/local/bin/` is the **hardcoded-path**
> variant. The copy in `scripts/` here is env-driven; set the `COINJOIN_*` env
> vars (or edit the paths) before relying on it directly.

## Viewer

The round viewer is a fork of `Copexit/am-i-exposed` and is built separately —
see [`viewer-mods.md`](viewer-mods.md).

## Secrets

Never committed; live outside this repo:

- `/home/wasabi/.walletwasabi/coordinator/Config.json` — bitcoind RPC user:pass, coordinator xpub
- `/etc/letsencrypt/**` — TLS private keys (the nginx conf only references their paths)
- bitcoind `bitcoin.conf` (`rpcpassword`), any Wasabi wallet files

Runtime artifacts (`latest*.json/html/txt`, `*.db`) are git-ignored — they
regenerate on the next timer tick.
