# Wasabi Preview Mobile design system

One place for every visual choice of the iOS and Android apps. Change a value here first, then in both theme files:

- iOS: [`WasabiPreview/Theme.swift`](../WasabiPreview/Theme.swift) (`enum Wasabi`, `Backdrop`, `TileSurface`, `Amount`, `PrimaryAction`, `ReadableWidth`)
- Android: [`android/app/src/main/java/nl/kravens/wasabipreview/Theme.kt`](../android/app/src/main/java/nl/kravens/wasabipreview/Theme.kt) (`object Wasabi`, `palette()`, `Backdrop`, `glass()`, `Amount`, `PrimaryAction`)

Where a screen still has its own number (paddings inside one view), this file gives the rule it follows.

## Principles

- **Desktop Wasabi first.** Colours and icons come from Wasabi Wallet's own resources (`WalletWasabi.Fluent/Styles/Themes/{Base,Dark,Light}.axaml` and its icon paths), so the phone reads as the same product.
- **Platform look second.** iOS uses Liquid Glass and SF Symbols where it has them; Android draws the same glass and glyphs itself. Neither platform uses a third-party UI library.
- **Plain words.** "Making your coins private", "Lower fees / Balanced / More private". A coinjoin is a non-custodial collaborative transaction; never write "mix" or "mixing".
- **Privacy on screen.** Discreet mode, a decoy wallet with modest amounts, no real data in screenshots.

## Themes

| Theme | Background | Surfaces | When |
|---|---|---|---|
| **Wasabi** (default) | `region` plus the green glow (Backdrop) | Glass | Follows the system light or dark mode |
| **Black** | Pure black `#000000` | Near-black cards `#0E0E0E`, no glass, no glow | Always dark; keeps OLED pixels off |

## Colours

All from desktop Wasabi unless noted. Dark and light are the system appearance; Black uses the dark values.

| Token | Dark | Light | Desktop resource | Used for |
|---|---|---|---|---|
| `accent` | `#29903B` | `#1AB248` | `SystemAccentColor` | Buttons, links, active icons, the glow, Tailscale "link is up" |
| `region` | `#1F1F1F` | `#FFFFFF` | `RegionColor` | Window background (Wasabi theme) |
| `tile` | `#262626` | `#EFEFEF` | `TileRegionColor` | Glass tint (Android) |
| `privacyStrong` | `#5A8F4B` | `#7BA170` | `PrivacyLevelStrongBrush` | Private coins, privacy bar, the shield at 100% |
| `privacyMedium` | `#536A4B` | `#B0C7A8` | `PrivacyLevelMediumBrush` | Semi-private coins |
| `privacyNone` | `#383838` | `#E0E0E0` | `PrivacyLevelNoneBrush` | Non-private coins, empty bar |
| `positive` | `#58A553` | same | AmountControl "+" run | Incoming amount sign |
| `negative` | `#B43434` | same | AmountControl "-" run | Outgoing amount sign |
| `warning` | `#DDA73E` | same | `WarningMessageForeground` | Warnings (combining private and non-private coins) |
| `error` | `#DD4343` | same | `DismissColor` | Errors, the stop-coinjoin button |
| `orangeCoin` | `#F2994A` | same | `OrangeCoinColor` | Excluded-from-coinjoin coins |
| `tor` | `#B58BD8` | `#7D4698` | Tor Project purple (ours), lighter on dark for contrast | Tor link mark |
| `logoGradient` | `#00FF4D` to `#17C04A`, top trailing to bottom leading | same | `LogoAccentColor` | The accent part of the Wasabi logo |
| Black card | `#0E0E0E` | n/a | ours | Cards on the Black theme |

Text colours are the iOS system label colours, copied on Android:

| Text | Dark | Light |
|---|---|---|
| primary | white | black |
| secondary | `#EBEBF5` at 60% | `#3C3C43` at 60% |
| tertiary | `#EBEBF5` at 30% | `#3C3C43` at 30% |

Rule: every text colour is stated. Never let a component derive it from a transparent container (on Android that falls back to black).

## Type

The system font on each platform (SF Pro, Roboto), at the platform's text styles so Dynamic Type and font scaling work. No custom fonts.

| Role | iOS | Android | Notes |
|---|---|---|---|
| Card caption ("BALANCE DAILY", "PRIVACY", "TRANSACTIONS") | `caption2`, bold, secondary | `labelSmall`, bold, secondary | Upper case, centred |
| Balance | `largeTitle`, light | `headlineLarge`, light | Amount style below; iOS shrinks it to 60% before it would wrap |
| Privacy percentage | `title`, bold | `headlineMedium`, bold | Tabular digits |
| Wallet name (title bar) | `headline` | top app bar title, semibold | |
| Row title / value | `body` | `bodyLarge` | One line, tail ellipsis |
| Row detail (date, label) | `caption` / `footnote`, secondary | `bodySmall`, secondary | One line |
| Footers and explanations | `footnote`, secondary | `bodySmall`, secondary | |
| Addresses, txids, keys | `footnote`, monospaced | monospace | Receive, Send review and Sent show addresses in groups of four; Copy and Share give them whole |
| Primary action | semibold | `titleMedium`, semibold | |

### Amounts

Wasabi's AmountControl, on both platforms:

- Always 8 decimals plus " BTC": `0.00487654 BTC`.
- The leading `0.`/`0` digits and spaces are secondary colour; the significant digits are bold. From 1 BTC up nothing is dimmed.
- Optional sign in `positive` or `negative`.
- Tabular (monospaced) digits so columns line up.
- Fiat: US dollars, whole dollars, two significant digits under a dollar.

## Spacing and shape

| Token | Value | Where |
|---|---|---|
| Screen margin | 16 | Left and right of lists and cards |
| Card gap | 10 | Between cards in a list |
| Card padding | 14 (iOS tiles), 16 horizontal (Android) | Inside a card |
| Card corner radius | 15 | Every card and tile (desktop TileControl) |
| Row padding | 10 vertical | List rows |
| Icon to text | 12 | Row icon column (20 to 22 wide) |
| Round buttons | 44 | Back, close, menu, and the scan and discreet pill |
| Primary action | Full width minus 16 each side, 8 above the home indicator; Android 52 high, radius 26 | One per screen |
| Receive / Send buttons | Two equal glass buttons, 12 apart; Android radius 30, icon 22 | Over the bottom of the home list |
| Side menu | 300 wide, radius 28 on the open edge | Both platforms |
| Column gap (wide home) | 12 | Between transactions and balance, and between Receive and Send, so they line up |

Units are points on iOS and dp on Android.

## Surfaces

### Glass (Wasabi theme)

- **iOS:** system Liquid Glass, `.glassEffect(.regular)` on cards, `.buttonStyle(.glass)` and `.glassProminent` on buttons.
- **Android:** drawn by `Modifier.glass()`: the backdrop and the content behind are redrawn into a layer, blurred 24 dp, then tinted with `tile` at 50% (dark) or 60% (light), with a 0.75 dp rim that fades from primary at 22% (dark) or 50% (light) to 5%. Android 12 and later; older versions get the tint only.
- What may sit under glass: the backdrop always; the home list under Receive and Send; the whole screen under the side menu. A glass element never blurs a source that contains itself.
- **Edge frost on the wide home (iOS):** the system frosts only one scroll view under a bar, so with two columns the app hides it and draws its own across the full width: `ultraThinMaterial` (black on the Black theme), solid over the navigation bar and fading out 40 below it; under Receive and Send at 60% so the list still shows faintly.

### Black theme cards

`#0E0E0E` fill, radius 15, no rim, no blur.

### Backdrop (Wasabi theme)

- `region` colour, plus the Wasabi logo shape in `accent`, 440 by 340, offset 120 right and 80 up from the top trailing corner.
- Blurred: iOS radius 70; Android 120 dp (Android's blur radius becomes a sigma of about 0.58 times, so it looks the same).
- Opacity 32% dark, 22% light.
- iOS adds a faint noise texture (3.5% dark, 2% light, overlay) so the glow does not band on OLED.

## Components

| Component | Rule |
|---|---|
| Tile / card | Caption centred on top; hero tiles (balance, privacy) centre their content, row tiles go edge to edge |
| Privacy tile | Shield greens up with the percentage (secondary to `privacyStrong`), state line in `accent` while coinjoin runs, privacy bar, private amount |
| Privacy bar | Private, semi-private, rest in the three privacy colours |
| Transaction row | Direction icon, title and date, signed amount, pending clock, chevron. When title and amount do not fit side by side the amount moves under the title, next to the date |
| Privacy rounds | Consecutive coinjoins collapse into one "Privacy rounds" row with stacked shields |
| Label input (Receive) | Chosen labels are filled `accent` chips with a cross (tap to remove); earlier labels are tinted chips under the field, ranked like desktop Wasabi (receive labels, newest first), filtered as you type. A comma or Return turns typed text into a chip |
| Waiting for payment | Under the Receive form: addresses handed out and not paid yet (label, middle-truncated address); tap to show that request again |
| Payment received | On a payment request, a card with a check in `accent`, the amount, and "Pending" or "Confirmed", as soon as a coin lands on the address |
| Segmented control | iOS segmented style; Android `Segmented` pill track |
| Secondary button | Tinted: `accent` at 16% fill, `accent` text, capsule |
| Link status | Tailscale mark in `accent`, Tor mark in `tor`, grey when the link timed out; pulses while connecting |
| Discreet mode | Digits become `*`, then blurred (iOS 2.5 plus a glass capsule, Android 3 dp); addresses keep 4 characters |
| Icons | Desktop Wasabi's icon paths (`WasabiIcons`), drawn from their SVG paths; chevrons, menu and close are stroked glyphs of equal weight |

## Layout

| Width | Home | Detail screens |
|---|---|---|
| Phone (under 640) | One list: balance and privacy card, then transactions | Full width with 16 margins |
| Wide, 640 and up (iPhone Duo unfolded, foldables, tablets, phone on its side) | Two columns: transactions on the left, balance, coins and privacy on the right (card 378 iOS, 394 Android), each scrolling on its own. Receive sits exactly under the transactions card and Send under the balance card: same outer margin (16 inside the safe area), same 12 gap | Rows at most 600 wide, centred; the backdrop stays edge to edge |

Narrow screens (a foldable's cover display) use the phone layout; transaction rows reflow as described above.

## Motion

- Short and functional: 0.2 to 0.3 s ease-out or spring. Coins fold out with a snappy 0.3 s; screens slide a third of their width and fade (Android).
- Respect Reduce Motion: no animation when it is on.

## Demo wallet and decoy

- Two wallets: **Daily** (about US$ 460, coinjoin in progress) and **Savings** (about US$ 1,100, all private).
- Keep demo balances modest: the decoy is shown to whoever holds the phone, and a large balance makes the holder a target.
- Addresses are BIP173 and BIP350 test vectors, txids are hashes of a counter. Never real data, also not in screenshots.

## Platform differences on purpose

- iOS sheets for Receive, Send, Connection and Settings; Android full screens with Close.
- iOS Liquid Glass is the system's; Android's glass is our own approximation of it.
- Android hides the app switcher preview while the decoy is on (Android 13+); iOS has no such switch.
- Android's home-screen label and iOS's are both "Wasabi Mobile"; the app name is "Wasabi Preview Mobile".
