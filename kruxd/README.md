# kruxd

Localhost bridge (trezord-style) for the Krux CoinJoin remote signer.
Pairs with the `feat/slip-19-coinjoin` Krux firmware branch (PMK/krux) which
adds a "CoinJoin USB" pre-approved signing session served over the console
UART (CH340), framed as 4-byte BE length + payload.

## Run

```
# real device (WonderMV on COM8, on the "CoinJoin USB" screen):
python kruxd.py COM8

# simulator / headless link (TCP :52123):
python kruxd.py sim
```

HTTP API on `http://127.0.0.1:21326` (localhost only; 21325 is trezord):

| endpoint | body | returns |
|---|---|---|
| POST /info | `{}` | `{fingerprint, rounds_used, max_rounds}` |
| POST /proof | `{script_type: "p2wpkh"\|"p2tr", path: [uint32...], commitment: hex}` | `{proof: hex}` (SLIP-19) |
| POST /sign | `{psbt: base64}` | `{psbt: base64}` signed under device policy |

Device-side policy (set on the Krux: Settings > Security > CoinJoin):
enabled, min self-transfer %, max fee rate sat/vB, max rounds per session.
Policy violations come back as HTTP 400 with the device's reason.

> **Start order matters:** launch kruxd *before* opening the CoinJoin USB screen on the
> device. The Windows CH340 driver pulses RTS when the port is first opened, which resets
> the K210 (RTS is wired to reset on WonderMV/Dock boards) — rebooting the device and
> wiping the loaded wallet. kruxd keeps the port open for its whole lifetime, so this
> only happens once, at startup.

## Hardware test procedure (WonderMV)

1. Flash `feat/slip-19-coinjoin` build: `ktool.py -B dan -p COM8 -b 2000000 kboot.kfpkg`
2. On device: Settings > Security > CoinJoin > Enabled; load testnet wallet
3. Home > Sign > CoinJoin USB; verify fingerprint + policy summary; approve
4. `python kruxd.py COM8`, then `POST /info` — fingerprint must match device
5. `POST /proof` with the wallet account path + `change/index`, any commitment —
   verify SLIP-19 proof (Wasabi `OwnershipProof.FromBytes` or embit verifier)
6. `POST /sign` with a policy-conforming testnet coinjoin PSBT — expect sigs;
   with a violating one (fee too high / self-transfer too low) — expect 400
7. Round counter on device screen must tick; at max_rounds sign returns
   "round budget exhausted"

Wasabi integration: `feature/krux-coinjoin` branch — `KruxKeyChain`
mirroring `TrezorKeyChain`, HTTP against this daemon instead of trezord.
