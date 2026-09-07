"""
Loopback check for the sabi target: a fake hid.device wired to a real SabiSigner UsbSession
through the real handshake and record layer, so the Krux-command-to-JSON translation in
SabiLink is exercised end to end without a device.

    PYTHONPATH=~/Documents/SabiSigner/src python3 -m pytest kruxd/test_sabi_link.py
"""
import base64
import json
import os
import sys
import types

import pytest

SABISIGNER_SRC = os.path.expanduser(os.environ.get("SABISIGNER_SRC", "~/Documents/SabiSigner/src"))
sys.path.insert(0, SABISIGNER_SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seedsigner.models.seed import Seed
from seedsigner.models.settings import SettingsConstants
from seedsigner.usb import crypto, hidframe, slip19
from seedsigner.usb.protocol import UsbSession
from embit import bip32, script
from embit.networks import NETWORKS

import kruxd


MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about".split()


class FakeDevice:
    """A SabiSigner reduced to its message loop: hello, pairing (auto-approved), requests."""

    def __init__(self, seed):
        self.session = UsbSession(seed=seed, network=SettingsConstants.MAINNET)
        self.channel = None
        self.decoder = hidframe.Decoder()
        self.outbox = []
        self.confirmations = []

    # hid.device interface
    def open(self, vid, pid):
        pass

    def write(self, data):
        message = self.decoder.push(data[1:])  # strip the report id
        if message is None:
            return
        if self.channel is None:
            hello = json.loads(message)
            self.channel, device_pub = crypto.handshake_responder(base64.b64decode(hello["pk"]))
            reply = json.dumps({"t": "hello", "pk": base64.b64encode(device_pub).decode()}).encode()
        else:
            reply = self.channel.seal(self.session.handle_message(self.channel.open(message), self._confirm))
        self.outbox.extend(hidframe.encode(reply))

    def read(self, size):
        return list(self.outbox.pop(0))

    def _confirm(self, kind, details):
        self.confirmations.append(kind)
        return True


@pytest.fixture
def link(monkeypatch):
    seed = Seed(MNEMONIC)
    device = FakeDevice(seed)
    monkeypatch.setitem(sys.modules, "hid", types.SimpleNamespace(device=lambda: device))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the pairing keypress
    link = kruxd.SabiLink(SABISIGNER_SRC).authorize("m/84'/0'/0'", 3, 1_000, 3_000)
    return link, device, seed


def test_startup_authorizes_once_and_reports_the_fingerprint(link):
    link, device, seed = link
    assert device.confirmations == ["authorize_coinjoin"]
    assert link.fingerprint.hex() == seed.get_fingerprint()

    info = link.request(bytes([kruxd.CMD_INFO]))
    assert info[:4] == link.fingerprint
    assert int.from_bytes(info[4:6], "big") == 0 and int.from_bytes(info[6:8], "big") == 3 and info[8] == 1


def test_proof_command_translates_and_verifies(link):
    link, device, seed = link
    path = [84 + 2**31, 0 + 2**31, 0 + 2**31, 0, 5]
    commitment = b"round-id|coordinator"
    payload = bytes([kruxd.CMD_PROOF, kruxd.SCRIPT_TYPES["p2wpkh"], len(path)])
    payload += b"".join(i.to_bytes(4, "big") for i in path) + commitment

    proof = link.request(payload)

    root = bip32.HDKey.from_seed(seed.seed_bytes, version=NETWORKS["main"]["xprv"])
    spk = script.p2wpkh(root.derive(path).key.get_public_key())
    assert slip19.verify_proof(proof, spk, commitment, require_confirmation=True)
    assert device.confirmations == ["authorize_coinjoin"]  # nothing asked the user again


def test_xpub_command_returns_the_account_key_and_fingerprint(link):
    link, device, seed = link
    path = [84 + 2**31, 0 + 2**31, 0 + 2**31]

    body = link.request(bytes([kruxd.CMD_XPUB]) + kruxd._path_payload(path))

    root = bip32.HDKey.from_seed(seed.seed_bytes, version=NETWORKS["main"]["xprv"])
    assert body[:4] == link.fingerprint
    assert bip32.HDKey.from_base58(body[4:].decode()) == root.derive(path).to_public()
    assert device.confirmations == ["authorize_coinjoin", "get_xpub"]  # one prompt per account


def test_info_says_which_device_answers_and_what_it_signs(link):
    link, device, seed = link
    info = kruxd.handle(link, "/info", {})
    assert info["device"] == "sabi"
    assert info["script_types"] == ["p2tr"]
    assert info["authorized"] is True
    assert info["fingerprint"] == seed.get_fingerprint()


def test_xpub_over_http_has_the_documented_shape(link):
    link, device, seed = link
    reply = kruxd.handle(link, "/xpub", {"path": [86 + 2**31, 0 + 2**31, 0 + 2**31]})
    assert reply["fingerprint"] == seed.get_fingerprint()
    assert reply["xpub"].startswith("xpub")


def test_a_device_refusal_is_a_value_error_like_a_krux_policy_error(link):
    link, device, seed = link
    outside = [84 + 2**31, 0 + 2**31, 1 + 2**31, 0, 0]
    payload = bytes([kruxd.CMD_PROOF, kruxd.SCRIPT_TYPES["p2tr"], len(outside)])
    payload += b"".join(i.to_bytes(4, "big") for i in outside) + b"x"
    with pytest.raises(ValueError, match="account"):
        link.request(payload)
