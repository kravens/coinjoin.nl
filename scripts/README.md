# coinjoin.nl · terminal tools

Two single-file Python tools. No dependencies — pure standard library, Python 3.7+.
They run anywhere with a truecolor terminal: Linux, macOS, Windows 10+, Raspberry Pi, SSH/tmux/Termius.

| Tool | What it is |
|---|---|
| **txflow.py** | Animated bitcoin transaction explorer with coinjoin detection — see *where coins flow* |
| **sabi.py** | Full terminal UI for the headless **Wasabi Wallet daemon** — mix, send and automate *your own coins* |

---

## txflow.py — transaction flow explorer
<img width="1898" height="987" alt="image" src="https://github.com/user-attachments/assets/ec721494-024c-48ea-95dc-e545a115917e" />

Pulls any bitcoin transaction from [mempool.space](https://mempool.space) (or your own self-hosted
instance) and animates its input → output flow as ASCII, with coinjoins detected and highlighted.

```bash
python3 txflow.py                        # live mempool dashboard (default)
python3 txflow.py <txid>                 # animate one transaction, walk the chain from it
python3 txflow.py <address>              # address privacy report
python3 txflow.py <txid> --depth 3       # interactive multi-transaction graph
python3 txflow.py <txid> --export out.html   # shareable animated page (also .gif/.png/.svg)
python3 txflow.py --mempool http://umbrel.local:3006   # your own mempool = no third party
```

**Live mempool dashboard** — transaction stream, upcoming blocks with fee rates, chain tip,
the **live coinjoin.nl round status** (phase, inputs, mining fee rate, countdown — join when green!),
latest coinjoins via LiquiSabi, and automatic coinjoin detection in the feed.

**Coinjoin goggles** — classifies Wasabi/WabiSabi (standard denomination set), Samourai Whirlpool,
Wasabi 1.x, JoinMarket and generic equal-output mixes, with per-denomination anonymity-set bars.
The `--depth` graph shows the **cumulative anonymity set** growing across chained rounds.

**Privacy analysis** — address view with a 0–100 **privacy score**, red warnings for address reuse,
mixed private/non-private coins and consolidation; **coin-control advice** per UTXO (`c`);
**peel-chain** and **toxic change** detection; Lightning channel open/close hints.

Keys: `w/s` move · `a/d` walk the chain · `space` open · `e` expand to graph · `m` mempool ·
`y` copy · `Tab`/`Shift+Tab` page · `?` help · `q` back · `Ctrl+C` quit.

---

## sabi.py — Wasabi daemon terminal
<img width="1903" height="991" alt="image" src="https://github.com/user-attachments/assets/d6521984-d630-41eb-b46f-9dcde7335098" />

A complete TUI for [Wasabi Wallet](https://wasabiwallet.io)'s headless daemon (v2.8.0 RPC):
wallets, balances, history, coinjoin and privacy-aware spending — from any terminal, over SSH,
on a Pi. The official Wasabi logo breathes green while your coins are mixing.

```bash
python3 sabi.py --demo        # safe preview with fake data - try everything, no daemon needed
python3 sabi.py               # zero-config: finds your daemon via Wasabi's own Config.json
```

First run help: sabi reads the daemon's `Config.json` for the RPC port and credentials, offers to
enable `JsonRpcServerEnabled` if it's off, and can find + start the daemon for you.

Seven tabs (`1-7`, mouse works too):

1. **dashboard** — daemon health (tor, P2P peers, filter sync), wallets: `space` load, `n` create, `v` recover
2. **wallet** — private / semi / non-private balance breakdown vs your anon-score target, privacy
   progress bar, coin list with anonymity scores and labels, `x` exclude from coinjoin, `k` address book
3. **history** — coinjoins marked ◆, `u` speed up (RBF), `c` cancel unconfirmed, `y` copy txid
4. **coinjoin** — `space` start/stop · `o` join exactly **one round** · `b` sweep to another wallet ·
   `p` **pay inside a coinjoin** (receiver gets a mixed output)
5. **send** — batch many recipients into one transaction; privacy-first coin selection with a
   **⚠ TOXIC MERGE** warning before it would ever undo your mix; live fee estimate; `i` paste a payment list
6. **auto** — **programmable rules**: *when non-private ≥ 0.05 BTC → start coinjoin*,
   *when private ≥ 0.5 BTC → sweep → cold wallet*, optional night-window (cheap fees),
   arm once with your password and it runs unattended — **hot → cold via coinjoin, automatically**

7. **scheme** — cross-wallet console: 9 native reports (total/balances/privacy % /toxic
   coins/label audit across ALL loaded wallets — exact and crash-safe) plus curated snippets
   for Wasabi's experimental Scheme `query` RPC (metadata-only; sabi can enable the
   `scripting` feature flag in your config with consent)

Everywhere: `g` receive (label → fresh address + **QR code**) · `.` **privacy mode** (obfuscates all
amounts and addresses — screen-share safe) · `?` help · `Ctrl+C` instant quit.

---

## Privacy notes

- **sabi.py** talks only to *your local daemon*. Wallet data never leaves your machine.
  (The coinjoin.nl round banner is a plain GET carrying no wallet data.)
- **txflow.py** queries the mempool server *you configure* with what *you type*. For maximum
  privacy, point `--mempool` at your own instance (Umbrel/RaspiBlitz/etc.).
- Passwords are prompted per action, masked, and kept in memory only — never on disk, never in the script.

## Requirements

- Python 3.7+ (nothing to install)
- A terminal with truecolor support (Windows Terminal, iTerm2, GNOME Terminal, kitty, Termius, ...)
- Optional: [Pillow](https://pypi.org/project/pillow/) only if you want txflow's `--export` to gif/png

*Coinjoin to break the link — [coinjoin.nl](https://coinjoin.nl)*
