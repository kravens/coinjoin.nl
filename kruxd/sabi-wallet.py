#!/usr/bin/env python3
"""
Write a Wasabi wallet file for a SabiSigner.

Wasabi imports hardware wallets through HWI, which does not know a SabiSigner, so the
wallet file is written here instead: one USB session, the BIP-84 and BIP-86 account xpubs
(one device prompt each), and a KeyManager JSON that says "hardware wallet, coinjoin via
the Krux backend" so that Wasabi talks to the device through `kruxd.py sabi`.

    python sabi-wallet.py SabiRegTest --network RegTest
    # then: restart Wasabi, the wallet appears; start kruxd.py sabi before coinjoining

Wasabi derives every network's accounts under coin type 0 except testnet4/signet, which is
why the defaults here are m/84'/0'/0' and m/86'/0'/0' even for RegTest.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kruxd  # noqa: E402

KRUX_VENDOR = 3  # HardwareCoinJoinVendor.Krux: the backend that speaks kruxd's HTTP API


def as_xpub(extended_key: str) -> str:
    """Wasabi stores xpub-prefixed keys whatever the network; the device may hand back a tpub."""
    from embit import bip32
    from embit.networks import NETWORKS

    return bip32.HDKey.from_base58(extended_key).to_base58(version=NETWORKS["main"]["xpub"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="wallet name (file name in Wasabi's Wallets directory)")
    parser.add_argument("--network", default="RegTest", choices=["Main", "TestNet4", "RegTest"])
    parser.add_argument("--segwit-account", default="m/84'/0'/0'")
    parser.add_argument("--taproot-account", default="m/86'/0'/0'")
    parser.add_argument("--wallets-dir", default=None, help="default: ~/.walletwasabi/client/Wallets/<network>")
    parser.add_argument("--sabisigner-src", default=os.path.expanduser("~/Documents/SabiSigner/src"))
    args = parser.parse_args()

    wallets_dir = args.wallets_dir or os.path.expanduser(f"~/.walletwasabi/client/Wallets/{args.network}")
    path = os.path.join(wallets_dir, f"{args.name}.json")
    if os.path.exists(path):
        sys.exit(f"{path} already exists; not overwriting a wallet file")

    link = kruxd.SabiLink(args.sabisigner_src)
    print("Approve the pairing, then the two xpub exports, on the device.")
    segwit = link.json({"t": "get_xpub", "path": args.segwit_account})
    taproot = link.json({"t": "get_xpub", "path": args.taproot_account})
    if segwit["fingerprint"] != taproot["fingerprint"]:
        sys.exit("the two xpubs came from different seeds")

    wallet = {
        "EncryptedSecret": None,
        "ChainCode": None,
        "MasterFingerprint": segwit["fingerprint"],
        "ExtPubKey": as_xpub(segwit["xpub"]),
        "TaprootExtPubKey": as_xpub(taproot["xpub"]),
        "SilentPaymentScanExtPubKey": None,
        "SilentPaymentSpendExtPubKey": None,
        "MinGapLimit": 21,
        "AccountKeyPath": args.segwit_account[2:].replace("h", "'"),
        "TaprootAccountKeyPath": args.taproot_account[2:].replace("h", "'"),
        "BlockchainState": {"Network": args.network, "Height": "0", "BirthHeight": 0},
        "PreferPsbtWorkflow": False,
        "AutoCoinJoin": False,
        "PlebStopThreshold": "0.005",
        "Icon": "Hardware",
        "AnonScoreTarget": 10,
        "CoinJoinDeviceMaxRounds": 50,
        "CoinJoinDeviceMaxMiningFeeRate": 5,
        "CoinJoinVendor": KRUX_VENDOR,
        "CoinJoinDisabled": False,
        "RedCoinIsolation": False,
        "DefaultReceiveScriptType": "Taproot",
        "ChangeScriptPubKeyType": "Segwit",
        "DefaultSendWorkflow": "Automatic",
        "ExcludedCoinsFromCoinJoin": [],
        "HdPubKeys": [],
    }

    os.makedirs(wallets_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wallet, f, indent=2)
    print(f"wrote {path} (fingerprint {segwit['fingerprint']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
