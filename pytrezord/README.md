# pytrezord — a Trezor Bridge replacement in one Python file

SatoshiLabs retired the standalone Trezor Bridge ([trezord-go](https://github.com/trezor/trezord-go)
publishes no releases anymore) and its replacement only ships inside Trezor Suite — whose new
JS bridge speaks a different protocol that classic clients can't use. `pytrezord` re-implements
the classic bridge HTTP API on `127.0.0.1:21325`, so **Wasabi Wallet, HWI and anything else
written against trezord keeps working** without building Go software.

## Install & run

```bash
pip install pyusb cryptography       # plus libusb-package if your system has no libusb
python3 pytrezord.py                 # listens on 127.0.0.1:21325
```

Linux additionally needs a udev rule (`python3 pytrezord.py --udev` prints it).
Close Trezor Suite while pytrezord runs — only one program can hold the USB device.
`cryptography` is only needed for the Trezor Safe 7.

## Supported

- Trezor Model T, Safe 3, Safe 5 (WebUSB, codec v1) — normal mode
- Trezor Safe 7 (THP, the encrypted Trezor-Host Protocol) — see below
- Full classic bridge API: `/`, `/enumerate`, `/listen`, `/acquire`, `/release`,
  `/call`, `/post`, `/read`, with trezord's Origin policy and session semantics
  (including session takeover with the correct previous session)

Not supported: Trezor Model One (HID protocol, end-of-life, cannot coinjoin) and
bootloader/firmware-update mode — use Trezor Suite for firmware updates.

## Trezor Safe 7 (THP)

The Safe 7 no longer speaks codec v1: it answers every v1 message with
`Failure(InvalidProtocol)` and only talks over a paired, encrypted channel
([THP](https://github.com/trezor/trezor-firmware/blob/main/docs/common/thp/specification.md):
Noise XX with X25519, AES-GCM and SHA-256). pytrezord notices that answer, opens the
channel itself and keeps serving plain v1 frames on HTTP, so clients need no change.

- **Pairing, once per device:** the first time, the Trezor asks to allow pytrezord and
  then shows a 6-digit code; type it into the terminal pytrezord runs in. pytrezord
  stores the pairing credential in `~/.pytrezord/thp-credentials.json` (owner-only).
  Run pytrezord in a terminal for this. Without one, clients get a Failure that says so.
- **Later connections** use the credential. When the Trezor no longer knows pytrezord's
  previous channel (after it restarted, for instance) it asks once to allow the
  connection. The channel outlives bridge sessions, and a pytrezord restart while the
  Trezor still knows that channel needs no confirmation.
- **PIN:** a locked Trezor asks for its PIN when pytrezord connects.
- **Sessions and passphrase:** THP has no `Initialize` and no passphrase prompt. pytrezord
  answers `Initialize` with the device's Features. When a request needs the seed, it
  creates a session the way a v1 Trezor would ask for a passphrase: with a
  `PassphraseRequest` to the client if passphrase protection is on, on the device if
  the Trezor enforces on-device entry, otherwise with the empty passphrase.

## Tested

- `python3 test_pytrezord.py` — 19 unit tests, no device needed (v1 framing across USB
  report boundaries, session lifecycle/steal, HTTP endpoints, Origin policy, long-poll,
  THP packets/CRC/ACKs, the v1↔THP session and passphrase mapping, THP detection)
- Live against a Trezor Model T (fw 2.12.1) on Windows: full API round-trips including
  multi-report messages both directions
- Wasabi Wallet (GUI and daemon) drives it end-to-end: wallet import, coinjoin
  authorization and unattended round signing on mainnet
- THP against the Trezor Safe 7 (T3W1) firmware emulator, nightly of 2026-11-20:
  pairing, credential reconnect (with and without the device's confirmation),
  seeded sessions, standard and hidden (passphrase) wallets, PIN unlock during the
  handshake and mid-session, re-pairing after a wipe. Not yet on a physical Safe 7.

## Security notes

- Binds to localhost only. Browser pages can only reach it from `*.trezor.io` or
  localhost origins (same policy as trezord); non-browser clients are unaffected.
- Codec v1 devices: pytrezord moves opaque protobuf frames between TCP and USB.
- Safe 7: the THP encryption ends in pytrezord, which then holds what any classic bridge
  client sends anyway (protobuf messages, a passphrase typed on the host). Its pairing
  credential lets this computer reconnect without pairing again, so it is stored
  owner-only. Delete the file to forget the pairing.
- Everything security-relevant still stays between your wallet software and the device
  screen; pytrezord never sees the seed.
