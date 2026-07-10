# pytrezord — a Trezor Bridge replacement in one Python file

SatoshiLabs retired the standalone Trezor Bridge ([trezord-go](https://github.com/trezor/trezord-go)
publishes no releases anymore) and its replacement only ships inside Trezor Suite — whose new
JS bridge speaks a different protocol that classic clients can't use. `pytrezord` re-implements
the classic bridge HTTP API on `127.0.0.1:21325`, so **Wasabi Wallet, HWI and anything else
written against trezord keeps working** without building Go software.

## Install & run

```bash
pip install pyusb libusb-package
python3 pytrezord.py                 # listens on 127.0.0.1:21325
```

Linux additionally needs a udev rule (`python3 pytrezord.py --udev` prints it).
Close Trezor Suite while pytrezord runs — only one program can hold the USB device.

## Supported

- Trezor Model T, Safe 3, Safe 5 (WebUSB devices) — normal mode
- Full classic bridge API: `/`, `/enumerate`, `/listen`, `/acquire`, `/release`,
  `/call`, `/post`, `/read`, with trezord's Origin policy and session semantics
  (including session takeover with the correct previous session)

Not supported: Trezor Model One (HID protocol, end-of-life, cannot coinjoin) and
bootloader/firmware-update mode — use Trezor Suite for firmware updates.

## Tested

- `python3 test_pytrezord.py` — 11 unit tests, no device needed (framing across USB
  report boundaries, session lifecycle/steal, HTTP endpoints, Origin policy, long-poll)
- Live against a Trezor Model T (fw 2.12.1) on Windows: full API round-trips including
  multi-report messages both directions
- Wasabi Wallet (GUI and daemon) drives it end-to-end: wallet import, coinjoin
  authorization and unattended round signing on mainnet

## Security notes

- Binds to localhost only. Browser pages can only reach it from `*.trezor.io` or
  localhost origins (same policy as trezord); non-browser clients are unaffected.
- pytrezord never sees secrets — it moves opaque protobuf frames between TCP and USB.
  Everything security-relevant stays between your wallet software and the device screen.
