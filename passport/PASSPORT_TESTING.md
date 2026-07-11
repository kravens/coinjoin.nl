# Passport Prime coinjoin — testing & hardware bring-up

State of the implementation and the procedure for the mainnet test once a physical
Passport Prime arrives. Two components, mirroring the Coldcard work:

1. **KeyOS firmware** — a new `wallet-rpc` service exposing a USB HID coinjoin
   remote-signing protocol:
   https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin
2. **Wasabi** — `feature/passport-coinjoin` branch (off `feature/coldcard-coinjoin`): a `Hwi/Passport`
   USB transport + `PassportKeyChain` implementing `IKeyChain`:
   https://github.com/kravens/WalletWasabi/tree/feature/passport-coinjoin

## What is verified in software (no device)

| Layer | Test | Result |
|---|---|---|
| Firmware protocol/coinjoin/SLIP-19/framing | `cargo test -p wallet-rpc-core` | 29/29 |
| Firmware ↔ security-server seed path, full flow | `just one-int-test --ci security-server settings-server wallet-rpc-test` | exit 0 |
| Wasabi transport/framing/policy/encoding | `dotnet test --filter PassportProtocolTests` | 10/10 |
| Wasabi library | `dotnet build WalletWasabi.csproj -c Release` | 0 errors |
| Firmware device build (armv7) | `just build` | see repo (task in progress at time of writing) |

The firmware integration test drives the exact desktop-wallet sequence — `AuthorizeCoinjoin`
→ `GetXpub` → `GetOwnershipProof` (checked byte-for-byte against the SLIP-0019 spec vector)
→ `SignCoinjoin` (our P2WPKH input signed, foreign input untouched) → foreign-coordinator proof
rejected — against the real `security-server` seed source.

## Wire protocol (v1), shared contract

Firmware `os/wallet-rpc-core/src/protocol.rs` and Wasabi `Hwi/Passport/PassportTransport.cs` must agree:

- Request `[ver=1][cmd][len u16 LE][payload]`, response adds `status` after the cmd echo.
- Commands: `0x01 GetInfo`, `0x02 GetXpub`, `0x03 GetOwnershipProof`, `0x04 AuthorizeCoinjoin`,
  `0x05 SignCoinjoin`, `0x06 RevokeSession`.
- Reports are 64 bytes; frames larger than one report use init `[0x00][len u16][data]` + continuation
  `[seq][data]` (firmware `frames.rs` ↔ C# `PassportFraming`).
- Session policy bytes: `[network][account u32][coord_len u8][coord][max_fee u64][max_rounds u16][valid_secs u32]`.
- SLIP-0019 proof: `534c0019 || flags || varint(n) || id*n || bip322-sig`; the device rejects any
  commitment that does not open with the authorized coordinator id.

## Hardware bring-up checklist (when the device arrives)

### 0. Confirm the USB identity (blocking — placeholders in code)

`Hwi/Passport/PassportHid.cs` has **placeholder** `VendorId`/`ProductId`. With the wallet-rpc firmware
flashed and the interface enabled, read the real values:

- Windows: Device Manager → the Passport HID interface → Details → Hardware Ids, **or** enumerate with
  `HidD_GetAttributes`.
- Update `PassportUsb.VendorId`/`ProductId` to match. Until then enumeration finds nothing.

### 0.5. Register wallet-rpc in the boot image (blocking)

`wallet-rpc` compiles for the device target (`cargo build -p wallet-rpc --target
armv7a-unknown-xous-elf --release` — clean, `--cfg keyos` active, the USB path included), but
`just build` does **not** yet bundle it: the service is a workspace member, not in the boot image's
service list. Before flashing, add `wallet-rpc` to the image service set (the xtask/release-manifest
service list alongside the other `os/*` servers) and grant it auto-start, so KeyOS launches it. Until
then the crate builds but no service runs on the device.

### 1. Flash the wallet-rpc firmware

Open question from the feasibility study: **does retail Prime boot non-Foundation-signed KeyOS builds?**
KeyOS releases are cosign2-signed. If retail units accept dev/self-signed images (dev-mode / blue PCB),
build and flash per `DEVELOPMENT.md`:

- `just build-all` then `just flash` (SAM-BA), **or** copy `app.bin` to the mounted `PRIME` volume and
  reboot (system-services path). The new `wallet-rpc` service is part of the image.
- If retail units only run Foundation-signed firmware: this needs Foundation to merge/sign, exactly like
  the Coldcard `feature/slip19-coinjoin` situation. Coordinate via the Foundation community/GitHub first
  (they have an internal Spark remote-signer design — align the message set before upstreaming).

### 2. Import the wallet into Wasabi (watch-only, hardware)

- Build the branch with `Contrib/release.sh` (self-contained Release build, as for the Trezor and
  Coldcard coinjoin test builds).
- With the device unlocked, import the account. Enumeration must report the model as
  **Foundation Passport**; set `IsPassportCoinjoin = true` on the key manager (same mechanism as
  `IsColdcardCoinjoin`).

### 3. Authorize a coinjoin session

- Start coinjoin; Wasabi calls `AuthorizeHardwareCoinJoinAsync` → `AuthorizePassportCoinJoinAsync`,
  which opens the device and sends the policy.
- **On the device**, review and approve the session policy alert: coordinator, account, per-round fee
  cap, round budget, 12-hour expiry. This is the single human approval; afterwards rounds sign
  unattended.
- The device must stay **unlocked** for the session — `GetSeed` fails when locked (confirmed by the
  hosted test's `not logged in ... GetSeed` warning). If the screen locks mid-session, unlock and the
  session resumes (or re-authorize).

### 4. Mainnet test (small amount)

- Fund the segwit account with a small amount you can afford to lose.
- Run one coinjoin round end to end. Watch for:
  - ownership proofs accepted by the coordinator (input registration succeeds),
  - the signed round broadcasts,
  - the fee contribution stays within the policy cap (round is rejected on-device if it wouldn't).
- Segwit v0 only for this build (taproot is a follow-up, matching the Coldcard branch scope).

### 5. Failure modes to check

- Wrong coordinator: point Wasabi at a different coordinator id than authorized → device rejects the
  ownership proof (`STATUS_ERR_POLICY`), no signature.
- Over-cap fee: a round whose fee contribution exceeds the cap → device rejects `SignCoinjoin`.
- Locked device: signing requests during a lock → `STATUS_ERR_DENIED`; unlock and retry.

## Warnings

- Placeholder VID/PID — enumeration is inert until step 0 is done.
- Unsigned, unmerged, testing-only. Don't use with funds you can't lose.
- Firmware flashing to retail Prime is gated on the signed-firmware question (step 1).
