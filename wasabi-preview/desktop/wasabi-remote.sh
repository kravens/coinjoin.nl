#!/usr/bin/env bash
# Opens desktop Wasabi's JSON-RPC to the Wasabi Mobile app, over Tailscale and over a client-authorized Tor onion
# service. Runs on macOS (Homebrew tor) and Arch/Omarchy (pacman tor, systemd). Safe to run again: it only adds what is
# missing and never replaces an existing onion, client key or RPC password.
#
# Nothing personal is stored in this file. The Tailscale IP, onion address, RPC password and client key are read or
# generated on the machine it runs on; the secrets the phone needs go to a 0600 file, never to the terminal.
set -euo pipefail

PORT=37128 # Wasabi's default JSON-RPC port
WASABI_DIR=${WASABI_DIR:-$HOME/.walletwasabi/client}
OUT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/wasabi-remote
CONFIG=$WASABI_DIR/Config.json

die() { echo "wasabi-remote: $*" >&2; exit 1; }
say() { echo "wasabi-remote: $*"; }

case $(uname -s) in
  Darwin)
    command -v brew >/dev/null || die "install Homebrew first (https://brew.sh)"
    command -v tor >/dev/null || die "install tor first: brew install tor"
    BREW=$(brew --prefix)
    TORRC=$BREW/etc/tor/torrc TOR_DATA=$BREW/var/lib/tor SUDO=""
    restart_tor() { brew services restart tor >/dev/null; }
    TAILSCALE=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
    ;;
  Linux)
    command -v tor >/dev/null || die "install tor first: sudo pacman -S tor"
    TORRC=/etc/tor/torrc TOR_DATA=/var/lib/tor SUDO=sudo
    restart_tor() { sudo systemctl enable --quiet tor && sudo systemctl restart tor; }
    TAILSCALE=$(command -v tailscale || true)
    ;;
  *) die "only macOS and Linux are supported" ;;
esac

[ -f "$CONFIG" ] || die "no $CONFIG; start Wasabi once so it writes its config, then quit it"
# Wasabi writes its config back on exit, which would undo the changes below.
if pgrep -fi 'wassabee|WalletWasabi\.(Fluent|Daemon)' >/dev/null; then die "quit Wasabi first, then run this again"; fi
openssl version 2>/dev/null | grep -q '^OpenSSL 3' || die "needs OpenSSL 3 for the x25519 key (macOS: brew install openssl@3)"

TS_IP=""
if [ -x "$TAILSCALE" ]; then TS_IP=$("$TAILSCALE" ip -4 2>/dev/null | head -1 || true); fi
[ -n "$TS_IP" ] || say "Tailscale is not running here; setting up the onion only"

# 1. Wasabi: RPC on, a login, and listening on loopback (for tor) plus the Tailscale IP. Config.json starts with a UTF-8
# BOM; it is written back with one.
python3 -I - "$CONFIG" "$PORT" "$TS_IP" <<'PY'
import json, secrets, sys
path, port, ts_ip = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(path, "rb").read()
bom = raw.startswith(b"\xef\xbb\xbf")
config = json.loads(raw.decode("utf-8-sig"))
config["JsonRpcServerEnabled"] = True
config["JsonRpcUser"] = config.get("JsonRpcUser") or "wasabi"
config["JsonRpcPassword"] = config.get("JsonRpcPassword") or secrets.token_urlsafe(32)
prefixes = config.get("JsonRpcServerPrefixes") or []
for host in ["127.0.0.1"] + ([ts_ip] if ts_ip else []):
    prefix = f"http://{host}:{port}/"
    if prefix not in prefixes:
        prefixes.append(prefix)
config["JsonRpcServerPrefixes"] = prefixes
text = json.dumps(config, indent=2, ensure_ascii=False)
open(path, "wb").write((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
PY
say "Wasabi RPC enabled in $CONFIG"

# Omarchy turns ufw on with incoming traffic denied; let the phone in over the tailnet only.
if [ -n "$TS_IP" ] && command -v ufw >/dev/null && sudo ufw status | grep -q '^Status: active'; then
  sudo ufw allow in on tailscale0 to any port "$PORT" proto tcp >/dev/null
  say "ufw: allowed port $PORT on tailscale0"
fi

# 2. Tor: an onion service in front of the RPC, reachable only with a client key.
HS=$TOR_DATA/wasabi-rpc
if ! $SUDO grep -q "^HiddenServiceDir $HS\$" "$TORRC" 2>/dev/null; then
  printf '\n# Wasabi Mobile (wasabi-remote.sh)\nHiddenServiceDir %s\nHiddenServicePort 80 127.0.0.1:%s\n' "$HS" "$PORT" | $SUDO tee -a "$TORRC" >/dev/null
  say "added the onion service to $TORRC"
fi
if [ "$(uname -s)" = Linux ]; then OWNER=$(stat -c %U "$TOR_DATA"); else OWNER=$(id -un); fi
$SUDO install -d -m 700 -o "$OWNER" "$HS" "$HS/authorized_clients"

mkdir -p "$OUT_DIR" && chmod 700 "$OUT_DIR"
KEY_FILE=$OUT_DIR/phone.auth_private
if [ ! -s "$KEY_FILE" ] && [ -n "$($SUDO ls -A "$HS/authorized_clients")" ]; then
  say "a client is already authorized in $HS/authorized_clients; keeping it (no new key made)"
elif [ ! -s "$KEY_FILE" ]; then
  # x25519 client authorization: tor gets the public half, the phone the private half, both base32 without padding.
  der=$(openssl genpkey -algorithm X25519 -outform DER | base64 | tr -d '\n')
  pub=$(printf %s "$der" | base64 -d | openssl pkey -inform DER -pubout -outform DER | base64 | tr -d '\n')
  b32() { python3 -I -c 'import base64,sys; print(base64.b32encode(base64.b64decode(sys.argv[1])[-32:]).decode().rstrip("="))' "$1"; }
  auth=$HS/authorized_clients/phone.auth
  printf 'descriptor:x25519:%s\n' "$(b32 "$pub")" | $SUDO tee "$auth" >/dev/null
  $SUDO chmod 600 "$auth" && $SUDO chown "$OWNER" "$auth"
  (umask 077; printf 'x25519:%s\n' "$(b32 "$der")" >"$KEY_FILE")
  say "made a client key for the phone"
fi

restart_tor
for _ in $(seq 30); do $SUDO test -s "$HS/hostname" && break; sleep 1; done
ONION=$($SUDO cat "$HS/hostname" 2>/dev/null) || die "tor did not publish the onion; check its log"
if [ -s "$KEY_FILE" ] && ! grep -q ':descriptor:' "$KEY_FILE"; then # complete the line now that the onion is known
  (umask 077; printf '%s:descriptor:%s\n' "${ONION%.onion}" "$(cat "$KEY_FILE")" >"$KEY_FILE.new") && mv "$KEY_FILE.new" "$KEY_FILE"
fi

# 3. What to enter in the app's Settings. Secrets stay in files only.
PHONE=$OUT_DIR/phone.txt
python3 -I - "$CONFIG" "$PORT" "$TS_IP" "$ONION" "$KEY_FILE" >"$PHONE.new" <<'PY'
import json, os, sys
config_path, port, ts_ip, onion, key_file = sys.argv[1:]
config = json.loads(open(config_path, encoding="utf-8-sig").read())
key = open(key_file).read().strip().rsplit(":", 1)[-1] if os.path.exists(key_file) else "(the phone you set up before has it)"
print("Wasabi Mobile > Settings")
if ts_ip:
    print(f"Tailscale  host: {ts_ip}  port: {port}")
print(f"Tor        onion: {onion}")
print(f"           key: {key}")
print(f"Login      user: {config['JsonRpcUser']}  password: {config['JsonRpcPassword']}")
PY
chmod 600 "$PHONE.new" && mv "$PHONE.new" "$PHONE"

say "done. Start Wasabi, then enter the values from $PHONE on the phone and delete that file."
