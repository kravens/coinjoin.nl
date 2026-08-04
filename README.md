# coinjoin.nl tooling

Scripts, systemd units, nginx config and the landing page for the
[coinjoin.nl](https://coinjoin.nl) WabiSabi coordinator. Runs on a RaspiBlitz
box (aarch64). No secrets in this repo — RPC creds, SSL keys and wallet files
live outside it.

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

## Hardware Wallet Signers

Status of hardware wallets as unattended WabiSabi coinjoin remote signers for
Wasabi Wallet (ownership proofs + round signing under an on-device policy):

| | **Trezor** | **Coldcard** | **Passport Prime** | **Krux** | **Ledger** | **SeedSigner** |
|---|---|---|---|---|---|---|
| **Wasabi branch** | [`feature/trezor-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/trezor-coinjoin) | [`feature/coldcard-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/coldcard-coinjoin) | [`feature/passport-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/passport-coinjoin) | [`feature/krux-coinjoin`](https://github.com/kravens/WalletWasabi/tree/feature/krux-coinjoin) | none — feasibility study only | none — feasibility study only |
| **Wasabi client side** | ✅ TrezorKeyChain + bridge | ✅ ColdcardKeyChain + raw USB HID (no bridge daemon) — hardware-verified on a real Mk4 | ✅ PassportKeyChain (transport moving USB HID → QuantumLink) | ✅ `KruxKeyChain` + [kruxd](kruxd/kruxd.py) bridge on :21326 (serial COM8 / simulator TCP); `importkruxwallet` RPC, per-wallet round/fee policy | ❌ none (USB channel + [client libs](https://github.com/LedgerHQ/app-bitcoin) exist, no KeyChain) | ❌ none (airgapped QR, no USB data channel) |
| **Firmware requirement** | none — stock firmware has coinjoin support | custom build, [**published here for testers**](firmware/) — 5.6.0 + [PR #685](https://github.com/Coldcard/firmware/pull/685) ([branch](https://github.com/kravens/firmware/tree/feature/slip19-coinjoin)). Mk4 only | custom coinjoin logic ([KeyOS branch](https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin)) — needs 2 QuantumLink messages added upstream | custom [`feat/slip-19-coinjoin`](https://github.com/PMK/krux/tree/feat/slip-19-coinjoin) branch — builds via WSL docker, flashes to WonderMV over USB (`-B dan`, COM8) | fork of [app-bitcoin](https://github.com/LedgerHQ/app-bitcoin) needed: `AUTHORIZE_COINJOIN` + `GET_OWNERSHIP_PROOF` APDUs; swap mode already skips per-tx confirmation against a pre-approved policy — precedent to reuse | full fork needed: USB gadget (CDC) transport, SLIP-19/SLIP-21, session policy, seed persistence — none exists |
| **Ownership proofs (SLIP-19)** | ✅ device-native | ✅ segwit + taproot, verified on a real Mk4 and accepted by Wasabi's own verifier. Ownership id is the real SLIP-21 derivation, pinned to the published vectors | ✅ segwit + taproot (spec vector + BIP-86 vector) | ✅ on-device `create_proof` P2WPKH + P2TR (SLIP-21 ownership key); vectors pass Wasabi's verifier | ❌ not implemented (`SIGN_MESSAGE` can't produce SLIP-19 sighash format) | ❌ not implemented |
| **Unattended round signing** | ✅ on-device authorization, SLIP-25 account | ✅ HSM policy, verified on a real Mk4. Five device-side rules bound one transaction and the sequence together: self-transfer floor, sats leaving, feerate per own vByte, round count and rate | ✅ session policy: fee cap, self-spend only, round budget, expiry | ✅ "CoinJoin USB" session: one on-device approval (fingerprint + policy summary), then unattended proofs + signing over framed UART link; self-spend floor, fee cap, SIGHASH rules, round budget (`max_rounds`) enforced | ❌ none; NVRAM policy storage feasible (MuSig2 sessions + BIP-388 HMAC precedents) | ❌ stateless by design: no policy storage, QR per interaction |
| **Real device tested** | ✅ | ✅ **mainnet** — retail Mk4 signing full WabiSabi rounds unattended, confirmed on-chain, after regtest rounds first. Every policy rule exercised from both sides on the device. Mk4 only: Q disables HSM, Mk3 too old | ❌ retail locked to vendor-signed firmware; building as official KeyOS SDK app — awaiting Foundation dev unit | ✅ WonderMV signed live WabiSabi coinjoin rounds on regtest (2026-07-13) — device validated PSBTs against on-device policy, signed own inputs over USB, txns broadcast + confirmed; 1134 unit tests pass | ❌ — Nano S Plus only sideloadable target (Nano X blocks sideloading, Nano S EOL/unsupported); Speculos emulator for dev | ❌ |
| **Script types** | taproot (SLIP-25) | segwit (taproot proofs verified, signing follow-up) | segwit v0 signing; proofs segwit + taproot | segwit + taproot (P2WPKH/P2TR only, enforced in psbt validation) | n/a | n/a |
| **Readiness** | **closest to production** | **testable today** — [firmware published](firmware/), mainnet rounds confirmed, [PR #685](https://github.com/Coldcard/firmware/pull/685) open with Coinkite. Open: signing speed caps round size, Linux/macOS HID transport unwritten, taproot *signing* untested | **collaborating with Foundation** — logic done + tested; SDK app over QuantumLink pending dev unit + [protocol proposal](https://github.com/kravens/KeyOS/blob/feature/passport-coinjoin/os/wallet-rpc/COINJOIN_PROPOSAL.md) ([status](passport/PASSPORT_TESTING.md)) | **working end-to-end on hardware** ([bring-up guide](kruxd/README.md)) | **feasibility researched — upstream unlikely (silent signing gated to Ledger swap partners), sideload-only fork** | **concept stage — conflicts with stock security model** |

### Coldcard: ready for testers

A signed Mk4 build is [published in `firmware/`](firmware/) with its SHA-256 and a GPG signature, so
this can be tried without building anything. It is Coinkite's 5.6.0 plus the ownership proofs and
HSM policy rules from [PR #685](https://github.com/Coldcard/firmware/pull/685), which is open and
not yet reviewed by Coinkite — so it is unofficial firmware, the device says so at every boot, and it
belongs on a wallet you can afford to be wrong about.

What works today: a retail Mk4 signs live WabiSabi rounds unattended, on mainnet, under a policy it
displays and you approve once. What limits it today is speed — roughly 2.7 ms per PSBT byte, so a
small round signs comfortably while a large one can take longer than a coordinator's signing phase
allows. Small rounds, or a coordinator with a longer signing phase, work now; large rounds need
firmware work that belongs upstream rather than in this branch.

### Security note: debug builds are not safe for funded wallets

A debug-built Coldcard (`DEBUG_BUILD=1`, i.e. `is_devmode`) exposes three channels that each
amount to arbitrary code execution from the host, with no physical interaction:

| Channel | Enabled by | What a host can do |
| --- | --- | --- |
| USB HID `EVAL` / `EXEC` / `XKEY` | `is_devmode`, dispatched in `usb.py` | Run any Python on the device, including reading the seed |
| Serial REPL | `ckcc.vcp_enabled(True)` at boot (`main.py`) | Same, over the CDC port the device presents |
| `dev_helper` keypad injection | started when `is_devmode` (`main.py`) | Drive the UI remotely; `T` re-enables the REPL |

This matters more here than it does for ordinary desk use, because unattended coinjoin signing
is *designed* around leaving the device connected to a host for hours while nobody is watching.

Upstream dispatched the USB test commands **before** the HSM whitelist, so HSM mode did not stop
them: with `hsm_active` set, `EXEC` still executed. Demonstrated on the simulator and fixed on
[`feature/slip19-coinjoin`](https://github.com/kravens/firmware/tree/feature/slip19-coinjoin) —
they are now refused on real hardware while HSM is active, and the simulator keeps them so the
test suite can still drive HSM (regression test: `testing/test_devmode_hsm.py`).

That fix closes the HID channel. It does **not** close the serial REPL or keypad injection, which
are enabled at boot and have no HSM concept. So the rule stands: **use a debug build only on a
test unit, never on a wallet holding funds.** HSM mode is not a substitute for a non-debug build.

### How the vendor-agnostic signer works

![Hardware-agnostic remote coinjoin signing](hardware-coinjoin-signing.svg)

Every device above plugs into the same core. Wasabi's coinjoin flow never learns how a
particular device talks — it only records *which vendor* signs a wallet
(`KeyManager.CoinJoinVendor`), maps a connected model to that vendor in one place
(`HardwareCoinJoin.VendorOf`), and dispatches once when authorizing a batch of rounds. Signing
itself goes through `IKeyChain`, whose entire contract is two calls: produce a SLIP-19 ownership
proof for an input, and sign our own inputs of a coinjoin. A device is touched at exactly two
points in a round — input registration and transaction signing — and not at all in between.

The consent model is deliberately left to each device rather than flattened into a common
abstraction, because the guarantees genuinely differ: a Trezor counts down preauthorized rounds
bound to its SLIP-25 account, a Coldcard enforces a self-transfer floor and a feerate cap in its HSM
policy, Krux holds a CoinJoin USB session with its own fee cap and round budget. Each expresses
the user's limits in its own terms; the client enforces whatever the device cannot (round budget,
fee-rate cap on round selection) and refuses to widen anything the device already decided.

SLIP-25 account behaviour — destination selection, account splitting, taproot-only coin choice —
keys off the *account shape* rather than the brand, so a future vendor that adopts SLIP-25
inherits it and one signing from the default accounts does not. Adding a vendor is four small
edits: an enum value, a model-to-vendor entry, a case in `AuthorizeHardwareCoinJoinAsync`, and an
`IKeyChain` implementation over whatever transport the device speaks.
