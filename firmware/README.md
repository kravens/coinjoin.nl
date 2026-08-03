# Coldcard Mk4 firmware with SLIP-19 coinjoin remote signing

Unofficial build. Not made, endorsed or supported by Coinkite.

This is Coldcard firmware 5.6.0 plus the SLIP-19 ownership proofs and HSM policy rules a Coldcard
needs to sign WabiSabi coinjoin rounds unattended. The same code is [open as pull request #685
against Coinkite's firmware repository](https://github.com/Coldcard/firmware/pull/685). Until it is
merged and released by Coinkite, this build is how the feature can be tried on a device.

**Mk4 only.** The header declares Mk4/Mk5 compatibility. The Q disables the classic HSM command set,
and the Mk3 line ended before the policy rules this depends on existed.

## Files

| file | what |
| --- | --- |
| `firmware-5.6.0-slip19-NODEBUG-mk4.dfu` | the firmware image, as loaded from SD card |
| `SHA256SUMS.txt` | SHA-256 of that image |
| `SHA256SUMS.txt.sig` | detached GPG signature over `SHA256SUMS.txt` |

## Verify before you install

Put all three files in the same directory and run the commands there.

**Check the hash.**

Linux:

```bash
sha256sum -c SHA256SUMS.txt
```

macOS (no `sha256sum` by default; `shasum` reads the same file):

```bash
shasum -a 256 -c SHA256SUMS.txt
```

Windows, in PowerShell:

```powershell
$want = (Get-Content SHA256SUMS.txt).Split(' ')[0]
$got = (Get-FileHash firmware-5.6.0-slip19-NODEBUG-mk4.dfu -Algorithm SHA256).Hash
if ($got -eq $want) { "OK" } else { "MISMATCH" }
```

Either way the answer is one word. Anything other than `OK` means stop.

**Check the signature over the hash file.** Same command everywhere, once GPG is installed — most
Linux distributions ship it, macOS gets it from `brew install gnupg`, Windows from
[Gpg4win](https://gpg4win.org/):

```bash
gpg --verify SHA256SUMS.txt.sig SHA256SUMS.txt
```

Expected signing key, fetched from a keyserver rather than from this repository:

```bash
gpg --keyserver hkps://keyserver.ubuntu.com --recv-keys 2536F69E9C3725662B6C146BDCF17F7A01272020
```

GPG will say the key is not certified with a trusted signature. That is expected and not a failure:
nobody has signed this key for you. The fingerprint above is the thing to check, against a source
that is not this page.

Verifying the hash without checking the signature only tells you the file downloaded intact, not who
built it. Checking the signature without knowing the fingerprint from somewhere other than this page
tells you only that whoever wrote this page also made the signature.

## What is in it

Base: Coinkite firmware `c849c4e0`, which is 5.6.0 including the July 2026 entropy hotfix.

Added, from branch `feature/slip19-coinjoin` at commit `5d0dd982`:

- `slp9` USB command returning SLIP-19 ownership proofs (P2WPKH and P2TR), with a `slip19_paths`
  HSM policy whitelist gating which paths may be proven unattended.
- Five HSM policy rules bounding what unattended signing may do: `max_txn`, `max_txn_per_period`,
  `max_sats_leaving`, `max_fee_per_kvbyte` and `min_inputs`.
- Two safety fixes: devmode test commands (`EVAL`/`EXEC`/`XKEY`) no longer bypass HSM mode on a
  debug build, and the master seed copy is blanked after the one HMAC that needs it.
- Two screen changes so an unattended device reads honestly: an indicator while a proof is being
  signed, and a busy line that expires instead of claiming to be working after the host goes quiet.

Firmware header, as built:

```
version_string   5.6.0
timestamp        2026-08-02 02:48:55 UTC
pubkey_num       0          (shared developer key, not Coinkite's factory key)
install_flags    0x0        (FWHIF_HIGH_WATER not set)
firmware_length  1011712
hw_compat        0x28       (Mk4, Mk5)
```

The build is not bit-reproducible: the signing timestamp is part of the header, so rebuilding the
same source produces a different hash. To rebuild the payload yourself, check out `5d0dd982` from
<https://github.com/kravens/firmware> and build `stm32/` for Mk4.

## Read this before installing

- **The device warns at every boot, and you should expect it to.** This image is signed with the
  shared developer key (`pubkey_num 0`), not Coinkite's factory key, so an Mk4 running it shows a
  "Danger: Custom Firmware" screen on each start. That warning is the indicator that matters. If a
  device running this build ever stops showing it, stop and find out why.
- **The green genuine light stays green.** Observed on a retail Mk4 with this exact image. Do not
  read that as "Coinkite signed this" — on this build they did not. Whatever the light attests to,
  it is not the identity of the key in the firmware header, so the boot warning above is the only
  thing distinguishing an official image from this one.
- **Use a device that does not hold significant funds.** This code has not been reviewed by
  Coinkite. It has been tested — on a retail Mk4, in the simulator, and in coinjoin rounds that
  confirmed on regtest and mainnet — but tested is not audited.
- `FWHIF_HIGH_WATER` is not set in the header, so installing this does not raise the device's
  permanent OTP downgrade floor.
- **`hsmcmd` must be enabled on the device** for any of the coinjoin functionality. A
  factory-fresh unit ships with it off.
- Signing speed is the practical limit today. An Mk4 needs roughly 2.7 ms per PSBT byte, so a
  ~14 KB round signs in about 40 s while a ~40 KB round takes 100 s or more and can miss a
  coordinator's signing phase. Small rounds work; large ones need a coordinator with a longer
  signing phase.

## Why the debug build is not here

A debug build of the same source exists and was used for testing, but it is not published. Its
serial REPL and `dev_helper` keypad injection are enabled at boot and have no HSM concept, so a
debug build is not safe to leave connected to a host — which is exactly what unattended coinjoin
signing does. Debug builds belong on test units.

## Licence

Coldcard firmware is copyright Coinkite Inc., licensed MIT with Commons Clause v1.0. The Commons
Clause withholds one right: selling. This build is not sold, and neither is anything derived from
it here. See `COPYING-CC` in the upstream repository for the full text.

"Coldcard" and "Coinkite" are their trademarks. This build is not affiliated with them.
