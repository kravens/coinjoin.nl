# Passport Prime coinjoin — status & plan

Making Foundation Passport Prime work as an **unattended coinjoin signer** for
Wasabi Wallet (WabiSabi), alongside the Trezor and Coldcard work. This is now a
collaboration with Foundation via their official KeyOS developer program.

- KeyOS firmware branch: https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin
- Wasabi branch: https://github.com/kravens/WalletWasabi/tree/feature/passport-coinjoin
- Protocol proposal (for Foundation): `os/wallet-rpc/COINJOIN_PROPOSAL.md` in the KeyOS branch

## Real-device sideload test (2026-07-16, retail KeyOS 1.2.1)

Tried the full sideload on the physical retail Prime. Result: **the bundle
reaches the device but retail firmware has no path to install it** — blocked
one layer *before* signature verification would even run.

What worked:
- `foundation build` produced the signed bundle (`app.elf` + `manifest.json`,
  dev cert via `foundation cert gen` — non-interactive, no TTY needed).
- `foundation sideload --no-run --mount-path …` copied it onto the Airlock
  volume at `keyos/apps/coinjoin-signer/`; the files are visible and intact in
  the device's own Files app (photo-verified on-device).

Where retail stops (all source-verified on the KeyOS tree):
- The app scanner reads `/keyos/apps` at **`Location::System`** — once, at
  app-manager boot (`os/app-manager/src/lib.rs:47`, `registry.rs:34`,
  `launch.rs:108`).
- The on-device Files app can only write **`Location::User`** ("Internal") or
  Airlock (`apps/gui-app-file-browser/src/location.rs:55`) — it has no System
  access, so moving the folder anywhere it offers cannot land in the scan path.
- The app-manager API exposes **no install or rescan message** (only
  `LaunchApp`/`GetAppName`).
- The only bridge into System storage is the **USB debug channel** that
  `foundation sideload` uses to launch — and the retail unit does not expose it
  (verified from the host: HID interfaces only, no serial/debug device).

Conclusion: sideloading onto retail 1.2.1 requires the Developer Mode / dev
unit debug channel, exactly as Foundation's docs imply. This is the concrete
data point for the dev-unit request — the signed bundle, the transfer and the
on-device presence all work; only the retail install gate stands.

## Where this stands (2026-07-15)

The device arrived, and reading Foundation's now-public **KeyOS SDK** docs
reshaped the approach. The good news: Foundation **explicitly lists coinjoins**
as a supported use case, and there's a real developer program (SDK, simulator,
CLI, dev units). The catch: the way we first built it (a custom USB-HID signing
service) isn't the platform's sanctioned path. So the plan pivoted — and the
hard cryptographic work carries straight over.

### What the SDK docs changed

- **USB HID for third-party apps is "🚧 Coming"** — an app can't open its own USB
  data channel yet. Our USB-HID transport only works as a system service baked
  into Foundation-signed firmware.
- **QuantumLink (Bluetooth) is the blessed interactive transport** — Foundation
  names coinjoins alongside Lightning, Nostr, Ark and swaps as its use cases. But
  its message set is a fixed enum owned by Foundation; there is no generic app
  channel, so a custom protocol needs two new message types added upstream.
- **Retail devices enforce cosign2 signing** (two Foundation keys) on every app
  and image, so nothing custom runs on a stock retail unit. **Dev units** run in
  Developer Mode with your own signing certificate, and sideloading is a
  first-class flow there (`foundation cert gen` + `foundation sideload`).
- **Seed access prompts the user on the trusted display per operation** — handled
  by retrieving the seed once per session (see below).

### The plan now

1. **Protocol proposal to Foundation.** Coinjoin needs two QuantumLink messages
   that don't exist yet — `AuthorizeCoinjoin` (one-time session policy) and
   `GetOwnershipProof` (SLIP-0019). PSBT signing reuses their existing
   `PublishPsbt` / `SignPsbt`. The proposal (in the branch) specifies both,
   ready to become a KeyOS PR.
2. **Dev unit** from Foundation's developer program to prototype the on-device
   app in the simulator and on real hardware.
3. **Rebuild as a KeyOS SDK app** over QuantumLink. The transport-agnostic core
   (all the crypto and policy logic, below) drops in unchanged.
4. **Wasabi side** becomes a desktop QuantumLink client (open question: does a
   desktop QuantumLink library exist, or is it Envoy/mobile-only?).

## What is built and verified in software (no device)

The signing logic is complete and transport-independent — it survives the
pivot. It lives in `os/wallet-rpc-core` (pure Rust, no KeyOS/USB/GUI deps).

| Layer | Test | Result |
|---|---|---|
| Protocol, coinjoin policy + signing, SLIP-0019 proofs, framing | `cargo test -p wallet-rpc-core` | 29/29 |
| Real security-server seed path, full flow | `just one-int-test --ci security-server settings-server wallet-rpc-test` | exit 0 |
| Wasabi transport / framing / policy / encoding | `dotnet test --filter PassportProtocolTests` | 10/10 |
| Wasabi library | `dotnet build -c Release` | 0 errors |
| Device target compiles (armv7) | `cargo build -p wallet-rpc --target armv7a-unknown-xous-elf` | clean |

The integration test drives the full desktop-wallet sequence against the real
`security-server`: authorize a session → fetch xpub → SLIP-0019 ownership proof
(checked byte-for-byte against the spec vector) → sign a coinjoin round (our
P2WPKH input signed, foreign input untouched) → foreign-coordinator proof
rejected.

## The on-device policy (what the device guarantees)

One human approval per session, then conforming rounds sign unattended. Per
round the device enforces (`wallet_rpc_core::coinjoin::check_and_sign`):

- **Self-spend only** — every input/output claimed as ours must re-derive from
  the seed to exactly its scriptPubKey; a lying host gains nothing.
- **Account scope** — our keys must sit under the one authorized account.
- **Bounded loss** — `sum(our inputs) − sum(our outputs) ≤ max_fee_contribution`;
  only outputs paying back into the account count as credit.
- **Session limits** — stops after the round budget or expiry; RAM-only, cleared
  on reboot.
- **Coordinator binding** — ownership proofs only sign a commitment opening with
  the authorized coordinator id.

**Seed handling:** the seed is retrieved once, at the session approval, and
cached for the session (zeroized on drop/reboot). Proofs and signatures serve
from the cache, so the trusted-display prompt happens once per session, not per
round — required for unattended signing.

## SLIP-0019 ownership proof format

`proof = proofBody || bip322Sig`, where
`proofBody = 0x534c0019 || flags || varint(n) || ownership_id*n`, the ownership
id is `HMAC-SHA256(SLIP-21_node(seed, "SLIP-0019"/"Ownership identification
key"), scriptPubKey)`, and the signed digest is
`SHA256(proofBody || varint(len(spk))||spk || varint(len(cd))||cd)`. Trezor-
compatible; verified byte-for-byte against the SLIP-0019 P2WPKH test vector.
Segwit v0 in this version; taproot is a follow-up.

## Open items

- **Desktop QuantumLink client** for Wasabi — exists, or build it? *(biggest unknown)*
- Foundation's decision on adding the two coinjoin QuantumLink messages.
- Security-review checklist from Foundation (requested for key-touching apps).
- Taproot ownership proofs + signing (follow-up after segwit v0 works).

## Warning

Unmerged, testing-only, developer work. Not for use with funds you can't lose
until it ships through Foundation's review.
