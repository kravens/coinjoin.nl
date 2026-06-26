# CoinJoin round viewer (`/var/www/coinjoin/viewer/`)

The viewer is a **self-hosted static build of [`Copexit/am-i-exposed`](https://github.com/Copexit/am-i-exposed)** (Next.js static export), not original code — so it is **not** committed here. This file records the local patches so the build is reproducible.

## Build / deploy

Repo at `/home/admin/am-i-exposed`. Toolchain: Node 22 (NodeSource) + pnpm (corepack). WASM is prebuilt/committed (no Rust needed).

```sh
git pull
# re-apply ALL mods below, then:
pnpm install && pnpm build
sudo cp -r out /var/www/coinjoin/viewer
sudo chown -R www-data:www-data /var/www/coinjoin/viewer
```

## Local source mods to re-apply after every `git pull`

(`output: "export"`, `trailingSlash`, `images.unoptimized` are already upstream.)

1. **`next.config.ts`** — add `basePath: "/viewer"` + `assetPrefix: "/viewer"`.
2. **`src/hooks/useExperienceMode.ts`** — default arg `false` → `true` (start in Cypherpunk, not Normie).
3. **`src/hooks/useCjLinkabilityView.ts`** — default coinjoins to the "Transaction flow" (TxFlowDiagram) view in pro mode instead of "CoinJoin structure"; single effect sets `proMode && isCoinJoin` (dropped the Boltzmann gate).
4. **`src/components/viz/TxFlowDiagram.tsx`** — `showAllInputs`/`showAllOutputs` default `true`; `linkabilityMode` init `!!isCoinJoinOverride && !!boltzmannResult` (don't start in linkability-coloring mode with no Boltzmann data, e.g. large rounds); add a mount `useEffect(() => { expand(); }, [expand])` so the full-view flow overlay auto-opens once the tx loads.
5. **`src/app/page.tsx`** — remove `<AppStoreAnnouncement />` and `<InstallPrompt />` renders + imports (kills the "Run on your own node" promo banner and PWA install prompt). Leave `TipToast` in place.

Effect: viewer starts in Cypherpunk view, auto-expands to the full-view Transaction flow, no promo/install pop-ups.

## nginx serving

The viewer's `public/` assets use root-absolute paths that Next's `assetPrefix` does NOT rewrite, so the nginx conf needs explicit prefix blocks (`^~ /viewer/`, plus `^~ /workers/ /wasm/ /data/ /locales/` served with `root /var/www/coinjoin/viewer`). See `nginx/coinjoin.nl.conf`.
