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
v0.37.x rewrote the UI: Normie/Cypherpunk mode, `useCjLinkabilityView` and `TxFlowDiagram` are gone
(old mods 2-4 obsolete; old patch kept in `viewer-mods-0.35.7.patch`). Current mods:

1. **`next.config.ts`** — add `basePath: "/viewer"` + `assetPrefix: "/viewer"`.
2. **`src/components/stage/TxStage.tsx`** — `open` state default `{ input: true, output: true }` (show all I/O); add `useEffect(() => { expand(); }, [expand]);` right after `useFullscreen()` so the fullscreen Transaction flow auto-opens.
3. **`src/app/page.tsx`** — remove `<InstallPrompt />` render + import (PWA install prompt). AppStoreAnnouncement no longer exists upstream.

Deploy: don't `cp -r out viewer` over an existing dir (nests `out/`); move old dir aside first:
`sudo mv /var/www/coinjoin/viewer /var/www/coinjoin/viewer.bak-<ver> && sudo cp -r out /var/www/coinjoin/viewer`.

## nginx serving

The viewer's `public/` assets use root-absolute paths that Next's `assetPrefix` does NOT rewrite, so the nginx conf needs explicit prefix blocks (`^~ /viewer/`, plus `^~ /workers/ /wasm/ /data/ /locales/` served with `root /var/www/coinjoin/viewer`). See `nginx/coinjoin.nl.conf`.
