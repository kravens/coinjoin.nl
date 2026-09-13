#!/usr/bin/env python3
"""Smallest checks that fail if sabi's network-privacy / config-safety logic breaks.
    python3 scripts/test_sabi.py     (the Tor part is skipped when no SOCKS proxy answers)"""
import os, sys, json, stat, tempfile, socket
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sabi

# plaintext-over-network detector
assert not sabi.insecure_rpc("http://127.0.0.1:37128")
assert not sabi.insecure_rpc("http://localhost:37128")
assert not sabi.insecure_rpc("https://blitz.local:37128")
assert not sabi.insecure_rpc("http://abcdefghijklmnop.onion:37128")
assert sabi.insecure_rpc("http://192.168.1.20:37128")
assert sabi.insecure_rpc("http://raspiblitz:37128")

# release downgrade guard
assert sabi.version_tuple("2.8.2") == (2, 8, 2)
assert sabi.version_tuple("v2.8.2.4") == (2, 8, 2)
assert sabi.version_tuple("2.7.9") < sabi.WASABI_MIN_VERSION <= sabi.version_tuple("2.8.2")

# config / rules land 0600 regardless of umask, and the bak too
os.umask(0o022)
d = tempfile.mkdtemp()
cfg = os.path.join(d, "Config.json")
open(cfg, "w").write(json.dumps({"JsonRpcPassword": "hunter2", "CoordinatorUri": ""}))
sabi.set_coordinator_in_config(cfg, "https://coinjoin.nl/")
for f in (cfg, cfg + ".sabi.bak"):
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
assert json.load(open(cfg))["CoordinatorUri"] == "https://coinjoin.nl/"
assert json.load(open(cfg))["JsonRpcPassword"] == "hunter2"            # untouched keys survive
sabi.RULES_FILE = os.path.join(d, "rules.json")
sabi.save_rules("w", [{"on": True}])
assert stat.S_IMODE(os.stat(sabi.RULES_FILE).st_mode) == 0o600
assert sabi.load_rules("w") == [{"on": True}]

# demo daemon speaks the preview RPC surface sabi now uses
demo = sabi.DemoRpc()
r = demo.call("importhardwarewallet", ["hw", True])
assert r["verifiedAddresses"] and len(r["accounts"]) == 2
wi = demo.call("getwalletinfo", [], wallet="DailyWallet")
assert wi["coinjoinSignedByDevice"] and wi["coinjoinDeviceMaxRounds"] == 50
demo.call("setcoinjoinlimits", [10, 2.5], wallet="DailyWallet")
assert demo.call("getwalletinfo", [], wallet="DailyWallet")["coinjoinDeviceMaxMiningFeeRate"] == 2.5

# Tor path: only when a SOCKS proxy is up (wasabi daemon or system Tor)
tor = None
for hp in sabi.TOR_SOCKS:
    try: socket.create_connection(hp, timeout=0.5).close(); tor = hp; break
    except OSError: pass
if tor:
    rs = sabi.tor_json("https://coinjoin.nl/wabisabi/human-monitor", timeout=40)
    assert "RoundStates" in rs, rs
    try: sabi.tor_open("https://github.com/kravens/definitely-not-here-404", timeout=40)
    except sabi.RpcError as e: assert "404" in str(e)
    else: raise AssertionError("404 must raise")
    print(f"tor ok via {tor}")
else:
    print("no Tor SOCKS proxy - Tor path skipped")
print("all ok")
