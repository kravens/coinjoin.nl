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


## Hardware Wallet Signers

Status of hardware wallets as unattended WabiSabi coinjoin remote signers for
Wasabi Wallet (ownership proofs + round signing under an on-device policy):

| | **Trezor** | **Coldcard** | **Passport Prime** | **Krux** | **Ledger** | **SeedSigner** |
|---|---|---|---|---|---|---|
| **Wasabi branch** | [`feature/trezor-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/trezor-coinjoin) | [`feature/coldcard-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/coldcard-coinjoin) | [`feature/passport-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/passport-coinjoin) | [`feature/krux-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/krux-coinjoin) | none — feasibility study only | none — feasibility study only |
| **Wasabi client side** | ✅ TrezorKeyChain + bridge | ✅ ColdcardKeyChain + raw USB HID (no bridge daemon) — hardware-verified on a real Mk4 | ✅ PassportKeyChain (transport moving USB HID → QuantumLink) | ✅ `KruxKeyChain` + [kruxd](kruxd/kruxd.py) bridge on :21326 (serial COM8 / simulator TCP); `importkruxwallet` RPC, per-wallet round/fee policy | ❌ none (USB channel + [client libs](https://github.com/LedgerHQ/app-bitcoin) exist, no KeyChain) | ❌ none (airgapped QR, no USB data channel) |
| **Firmware requirement** | none — stock firmware has coinjoin support | custom [`feature/slip19-coinjoin`](https://github.com/kravens/firmware/tree/feature/slip19-coinjoin) branch ([PR #685](https://github.com/Coldcard/firmware/pull/685)) + HSM policy. **Must build with `DEBUG_BUILD=1`** — the makefile defaults to `0`, which disables `is_devmode` and with it the `dev.dfu` auto-install, serial REPL and `EXEC` escape hatches | custom coinjoin logic ([KeyOS branch](https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin)) — needs 2 QuantumLink messages added upstream | custom [`feat/slip-19-coinjoin`](https://github.com/PMK/krux/tree/feat/slip-19-coinjoin) branch — builds via WSL docker, flashes to WonderMV over USB (`-B dan`, COM8) | fork of [app-bitcoin](https://github.com/LedgerHQ/app-bitcoin) needed: `AUTHORIZE_COINJOIN` + `GET_OWNERSHIP_PROOF` APDUs; swap mode already skips per-tx confirmation against a pre-approved policy — precedent to reuse | full fork needed: USB gadget (CDC) transport, SLIP-19/SLIP-21, session policy, seed persistence — none exists |
| **Ownership proofs (SLIP-19)** | ✅ device-native | ✅ segwit + taproot (simulator) | ✅ segwit + taproot (spec vector + BIP-86 vector) | ✅ on-device `create_proof` P2WPKH + P2TR (SLIP-21 ownership key); vectors pass Wasabi's verifier | ❌ not implemented (`SIGN_MESSAGE` can't produce SLIP-19 sighash format) | ❌ not implemented |
| **Unattended round signing** | ✅ on-device authorization, SLIP-25 account | ✅ HSM policy (`min_pct_self_transfer` floor) — policy install, on-device approval and unattended policy-gated PSBT signing all verified on a real Mk4 | ✅ session policy: fee cap, self-spend only, round budget, expiry | ✅ "CoinJoin USB" session: one on-device approval (fingerprint + policy summary), then unattended proofs + signing over framed UART link; self-spend floor, fee cap, SIGHASH rules, round budget (`max_rounds`) enforced | ❌ none; NVRAM policy storage feasible (MuSig2 sessions + BIP-388 HMAC precedents) | ❌ stateless by design: no policy storage, QR per interaction |
| **Real device tested** | ✅ | ⚠️ partial (Mk4, 2026-07-18) — on **stock** firmware: USB HID transport, HSM policy install + on-device approval, and unattended policy-gated PSBT signing all verified. `slp9` proofs remain simulator-only. Retail units *do* run self-built key-0 firmware (boot shows a permanent "Custom" warning), but they are RDP2-locked so the bootloader refuses ROM DFU — a broken custom build cannot be recovered by any means, which stranded one unit here. Mk4 is the only viable model (Q disables HSM, Mk3 too old) | ❌ retail locked to vendor-signed firmware; building as official KeyOS SDK app — awaiting Foundation dev unit | ✅ WonderMV signed live WabiSabi coinjoin rounds on regtest (2026-07-13) — device validated PSBTs against on-device policy, signed own inputs over USB, txns broadcast + confirmed; 1134 unit tests pass | ❌ — Nano S Plus only sideloadable target (Nano X blocks sideloading, Nano S EOL/unsupported); Speculos emulator for dev | ❌ |
| **Script types** | taproot (SLIP-25) | segwit (taproot proofs verified, signing follow-up) | segwit v0 signing; proofs segwit + taproot | segwit + taproot (P2WPKH/P2TR only, enforced in psbt validation) | n/a | n/a |
| **Readiness** | **closest to production** | **client side proven on hardware; firmware iteration needs a non-RDP2 dev unit** — retail Mk4 runs custom firmware but blocks ROM DFU, making every flash a one-way trip | **collaborating with Foundation** — logic done + tested; SDK app over QuantumLink pending dev unit + [protocol proposal](https://github.com/kravens/KeyOS/blob/feature/passport-coinjoin/os/wallet-rpc/COINJOIN_PROPOSAL.md) ([status](passport/PASSPORT_TESTING.md)) | **working end-to-end on hardware** ([bring-up guide](kruxd/README.md)) | **feasibility researched — upstream unlikely (silent signing gated to Ledger swap partners), sideload-only fork** | **concept stage — conflicts with stock security model** |
