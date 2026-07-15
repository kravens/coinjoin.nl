# Coinjoin Signer — Passport Prime app (UI mockup)

A KeyOS SDK app: the on-device UI for using Passport Prime as an unattended
WabiSabi coinjoin signer for Wasabi Wallet. **This is a UI/flow mockup** — the
screens and navigation are real and build for the device, but state is mocked
(buttons drive the flow); the real QuantumLink transport + signing come from
[`wallet-rpc-core`](https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin)
once Foundation adds the two coinjoin QuantumLink messages
([proposal](https://github.com/kravens/KeyOS/blob/feature/passport-coinjoin/os/wallet-rpc/COINJOIN_PROPOSAL.md)).

## Screens

1. **Home** — idle, "Waiting for Wasabi".
2. **Authorize** — the one human approval: coordinator, account, per-round fee
   cap, round budget, 12-hour expiry, then slide-to-authorize.
3. **Session active** — live round counter (mock "Simulate round"), self-spend
   confirmation, revoke.
4. **Complete** — session summary.

## Build & run (Foundation SDK)

Needs the Foundation Passport Prime SDK (public beta) + Nix. `Cargo.toml` deps
point at `__SDK_KEYOS_ROOT__` — either regenerate the scaffold and drop these
`ui/`, `theme/`, `app-config.toml` files in, or substitute your
`~/.foundation/sdk/current/lib/keyos` path.

```sh
foundation new coinjoin-signer -t multi-page-app   # scaffold
# copy this repo's ui/pages, theme/, app-config.toml over the scaffold
foundation build      # builds for armv7a-unknown-xous-elf  (verified working)
foundation sim        # hosted simulator — run from an interactive WSLg/desktop
                      # session so the window renders (headless CI can't)
```

`foundation build` compiles clean for the device target. `foundation sim` needs
a real display (the KeyOS GUI simulator won't start in headless CI) — run it
from Windows Terminal / a desktop WSL session and the app window appears; click
through the four screens to capture screenshots.

## Status

Built against Foundation SDK 0.4.0. UI-only; not signed, not for real funds.
Verified: `foundation build` produces the armv7 app bundle. Simulator
screenshots pending an interactive display session.
