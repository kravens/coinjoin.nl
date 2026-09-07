#!/usr/bin/env python3
"""kruxd - trezord-style localhost bridge for the Krux CoinJoin remote signer.

Talks the framed link protocol (4-byte BE length + payload) to a Krux device
on the "CoinJoin USB" screen, over serial (real device) or TCP (simulator).
Exposes a small localhost HTTP API for a Wasabi KeyChain:

  POST /info               -> {"fingerprint": hex, "rounds_used": n, "max_rounds": n,
                               "device": "krux"|"sabi", "script_types": [...],
                               "authorized": bool (only when the device reports it)}
  POST /xpub  {"path": [uint32...]}          -> {"fingerprint": hex, "xpub": base58}
  POST /proof {"script_type": "p2wpkh"|"p2tr", "path": [uint32...],
               "commitment": hex}          -> {"proof": hex}
  POST /sign  {"psbt": base64}             -> {"psbt": base64}

Usage: kruxd.py COM8 [--baud 115200]   (serial)
       kruxd.py sim [host]             (simulator TCP :52123)
       kruxd.py sabi [--account m/84'/0'/0'] [--max-rounds N]
                     [--max-fee-per-round-sat N] [--max-total-fee-sat N]
                     [--sabisigner-src PATH]    (SabiSigner over USB HID)

The sabi target drives a SabiSigner (SeedSigner fork) instead of a Krux: same HTTP
API towards Wasabi, but the device end is the SabiSigner's encrypted HID session.
SabiSigner authorizations are host-initiated, so the bridge asks for one at startup
with the budget given on the command line; compare the six pairing digits it prints
against the device screen, then approve the budget on the device.
"""
import argparse
import base64
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HTTP_PORT = 21326  # 21325 is taken by trezord itself; keep the neighborhood
LINK_TCP_PORT = 52123

CMD_INFO = 1
CMD_PROOF = 2
CMD_SIGN = 3
CMD_AUTHORIZE = 4
CMD_XPUB = 5
SCRIPT_TYPES = {"p2wpkh": 0, "p2tr": 1}
MAGIC = b"KXJ1"  # frame delimiter; must match the extension link.py


def _path_str(indexes):
    """m/84'/0'/0'/0/5 from raw uint32 indexes with the hardened bit set."""
    return "m/" + "/".join(str(i - 2**31) + "'" if i >= 2**31 else str(i) for i in indexes)


class DeviceLink:
    """One framed request/response at a time against the device link."""

    device = "krux"
    script_types = ("p2wpkh", "p2tr")

    def __init__(self, target, arg):
        self._lock = threading.Lock()
        if target == "sim":
            self.sock = socket.create_connection((arg or "127.0.0.1", LINK_TCP_PORT))
            self.sock.settimeout(120)
            self._write = self.sock.sendall
            self._read = self._sock_read
        else:
            import serial

            s = serial.serial_for_url(
                target, do_not_open=True, baudrate=int(arg or 115200), timeout=120
            )
            # CH340 DTR/RTS wired to K210 reset/boot - keep deasserted so the
            # device is not reset into ISP mode on open.
            s.dtr = False
            s.rts = False
            s.open()
            time.sleep(0.2)
            s.reset_input_buffer()  # boot console noise otherwise parses as a frame
            self.serial = s
            self._write = s.write
            self._read = s.read

    def _sock_read(self, n):
        chunks = b""
        while len(chunks) < n:
            data = self.sock.recv(n - len(chunks))
            if not data:
                raise ConnectionError("link closed")
            chunks += data
        return chunks

    def _sync_to_magic(self):
        """Consumes bytes until the frame MAGIC is seen, so device boot/console
        noise is skipped instead of read as a length."""
        window = b""
        while window != MAGIC:
            byte = self._read(1)
            if not byte:  # serial read timed out with no data
                raise ConnectionError("no response from device")
            window = (window + byte)[-len(MAGIC):]

    def request(self, payload):
        with self._lock:
            if hasattr(self, "serial"):
                self.serial.reset_input_buffer()  # drop any console noise between requests
            self._write(MAGIC + len(payload).to_bytes(4, "big") + payload)
            self._sync_to_magic()
            header = self._read(4)
            if len(header) != 4:
                raise ConnectionError("no response from device")
            body = self._read(int.from_bytes(header, "big"))
        if not body or body[0] != 0:
            raise ValueError(body[1:].decode(errors="replace") or "device error")
        return body[1:]


class SabiLink:
    """
    A SabiSigner behind the same request() contract DeviceLink offers, so the HTTP handler
    does not know which device it is talking to. Krux's binary commands are translated into
    SabiSigner's JSON requests and carried over its encrypted HID session.
    """

    VENDOR_ID = 0x1209
    PRODUCT_ID = 0x0001

    device = "sabi"
    # The device wants the full previous transaction of every non-taproot input, which a
    # coinjoin PSBT cannot carry for the foreign ones, so only taproot rounds are signable.
    script_types = ("p2tr",)

    def __init__(self, sabisigner_src):
        """Open the device and run the handshake; nothing is authorized yet."""
        import hid  # pip install hidapi

        sys.path.insert(0, sabisigner_src)
        from seedsigner.usb import crypto, hidframe  # the device's own session code, reused verbatim
        from embit import ec

        self._hidframe = hidframe
        self._lock = threading.Lock()
        self._dev = hid.device()
        self._dev.open(self.VENDOR_ID, self.PRODUCT_ID)
        self._decoder = hidframe.Decoder()
        self.fingerprint = None
        self.max_rounds = 0
        self.rounds_remaining = 0

        host_priv = ec.PrivateKey(os.urandom(32))
        self._send_raw(json.dumps({
            "t": "hello",
            "pk": base64.b64encode(host_priv.get_public_key().sec()).decode("ascii"),
        }).encode())
        reply = json.loads(self._recv_raw())
        if reply.get("t") != "hello":
            raise ConnectionError("SabiSigner did not answer the handshake: %r" % reply)
        self._channel = crypto.handshake_initiator(base64.b64decode(reply["pk"], validate=True), host_priv)
        print("SabiSigner pairing code: %s %s -- confirm the same digits on the device"
              % (self._channel.sas[:3], self._channel.sas[3:]))
        # The device drops every record until the user has approved the pairing, and it
        # does not say when that happened; only the human knows. A record sent too early
        # is lost, and the encrypted channel accepts records strictly in order, so nothing
        # is sent until they say so here.
        input("Approve the pairing on the device, then press enter here: ")

    def authorize(self, account_path, max_rounds, max_fee_per_round_sat, max_total_fee_sat, coordinator="wasabi"):
        """
        The one thing the user approves; everything after it is policy-checked on the device.
        Blocks until the pairing and the budget prompts are both answered.
        """
        approved = self.json({
            "t": "authorize_coinjoin",
            "coordinator": coordinator,
            "account_path": account_path,
            "max_rounds": max_rounds,
            "max_fee_per_round_sat": max_fee_per_round_sat,
            "max_total_fee_sat": max_total_fee_sat,
        })
        self.fingerprint = bytes.fromhex(approved["fingerprint"])
        self.max_rounds = max_rounds
        self.rounds_remaining = approved["rounds_remaining"]
        print("SabiSigner authorized: fingerprint %s, %d rounds, %d sat/round, %d sat total, account %s"
              % (approved["fingerprint"], max_rounds, max_fee_per_round_sat, max_total_fee_sat, account_path))
        return self

    # -- transport ----------------------------------------------------------------------

    def _send_raw(self, message):
        for report in self._hidframe.encode(message):
            self._dev.write(b"\x00" + report)

    def _recv_raw(self):
        while True:
            report = bytes(self._dev.read(self._hidframe.REPORT_SIZE))
            message = self._decoder.push(report)
            if message is not None:
                return message

    def json(self, body):
        """One encrypted request; a device-side refusal becomes ValueError like a Krux policy error."""
        self._send_raw(self._channel.seal(json.dumps(body).encode()))
        reply = json.loads(self._channel.open(self._recv_raw()))
        if reply.get("t") != "ok":
            raise ValueError(reply.get("message", "device error"))
        return reply

    # -- the DeviceLink contract --------------------------------------------------------

    def request(self, payload):
        with self._lock:
            cmd = payload[0]
            if cmd == CMD_INFO:
                used = self.max_rounds - self.rounds_remaining
                return (self.fingerprint + used.to_bytes(2, "big")
                        + self.max_rounds.to_bytes(2, "big") + bytes([self.rounds_remaining > 0]))
            if cmd == CMD_AUTHORIZE:
                # Authorized at startup from the command line; the device will not take a
                # budget from the host mid-session without the user, so this is a no-op.
                return b""
            if cmd == CMD_XPUB:
                # One device prompt per account; the fingerprint comes from the device too, so
                # the wallet can tell whether the accounts belong to the authorized seed.
                reply = self.json({"t": "get_xpub", "path": _path_str(_indexes(payload, 1))})
                return bytes.fromhex(reply["fingerprint"]) + reply["xpub"].encode("ascii")
            if cmd == CMD_PROOF:
                script_type = next(name for name, code in SCRIPT_TYPES.items() if code == payload[1])
                indexes = _indexes(payload, 2)
                commitment = payload[3 + 4 * len(indexes):]
                reply = self.json({
                    "t": "get_ownership_proof",
                    "path": _path_str(indexes),
                    "script_type": script_type,
                    "commitment": base64.b64encode(commitment).decode("ascii"),
                })
                return base64.b64decode(reply["proof"])
            if cmd == CMD_SIGN:
                reply = self.json({"t": "sign_coinjoin", "psbt": base64.b64encode(payload[1:]).decode("ascii")})
                self.rounds_remaining = reply["rounds_remaining"]
                return base64.b64decode(reply["psbt"])
            raise ValueError("unknown command %d" % cmd)


def _indexes(payload, at):
    """The [count u8][uint32 BE * count] path that starts at payload[at]."""
    count = payload[at]
    return [int.from_bytes(payload[at + 1 + 4 * i:at + 5 + 4 * i], "big") for i in range(count)]


def _path_payload(path):
    return bytes([len(path)]) + b"".join(int(index).to_bytes(4, "big") for index in path)


def handle(link, path, req):
    """One HTTP request against the link; None for an unknown path. ValueError/KeyError = 400."""
    if path == "/info":
        body = link.request(bytes([CMD_INFO]))
        result = {
            "fingerprint": body[:4].hex(),
            "rounds_used": int.from_bytes(body[4:6], "big"),
            "max_rounds": int.from_bytes(body[6:8], "big"),
            "device": link.device,
            "script_types": list(link.script_types),
        }
        if len(body) > 8:  # only the device may say it authorized something
            result["authorized"] = bool(body[8])
        return result
    if path == "/authorize":
        payload = (
            bytes([CMD_AUTHORIZE])
            + int(req["max_rounds"]).to_bytes(2, "big")
            + int(req["max_fee_rate_sat_vb"]).to_bytes(2, "big")
            + bytes([int(req["min_self_transfer_pct"])])
        )
        try:
            link.request(payload)  # blocks on the device confirmation
        except ValueError:
            # A garbled response frame (e.g. boot/console noise) can look
            # like a device error even though the device authorized.
            # Re-check /info before surfacing a failure.
            info = link.request(bytes([CMD_INFO]))
            if not (len(info) > 8 and info[8]):
                raise
        return {"authorized": True}
    if path == "/xpub":
        body = link.request(bytes([CMD_XPUB]) + _path_payload(req["path"]))
        return {"fingerprint": body[:4].hex(), "xpub": body[4:].decode("ascii")}
    if path == "/proof":
        payload = bytes([CMD_PROOF, SCRIPT_TYPES[req["script_type"]]]) + _path_payload(req["path"])
        payload += bytes.fromhex(req["commitment"])
        return {"proof": link.request(payload).hex()}
    if path == "/sign":
        signed = link.request(bytes([CMD_SIGN]) + base64.b64decode(req["psbt"]))
        return {"psbt": base64.b64encode(signed).decode()}
    return None


class Handler(BaseHTTPRequestHandler):
    link = None  # set at startup

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            result = handle(self.link, self.path, req)
            if result is None:
                self.send_error(404)
                return
            self._reply(200, result)
        except (ValueError, KeyError) as e:  # bad request or device policy rejection
            self._reply(400, {"error": str(e)})
        except Exception as e:
            self._reply(500, {"error": str(e)})

    def _reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("%s %s" % (self.command if hasattr(self, "command") else "-", fmt % args))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    target = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if target.startswith("--"):
        sys.exit(__doc__)
    if target == "sabi":
        parser = argparse.ArgumentParser(prog="kruxd.py sabi")
        parser.add_argument("--account", default="m/84'/0'/0'", help="account the device may sign under; Wasabi uses coin type 0 even on regtest")
        parser.add_argument("--max-rounds", type=int, default=20)
        parser.add_argument("--max-fee-per-round-sat", type=int, default=5_000)
        parser.add_argument("--max-total-fee-sat", type=int, default=50_000)
        parser.add_argument("--sabisigner-src", default=os.path.expanduser("~/Documents/SabiSigner/src"),
                            help="SabiSigner checkout's src/ (its usb.crypto and usb.hidframe are reused)")
        opts = parser.parse_args(sys.argv[2:])
        Handler.link = SabiLink(opts.sabisigner_src).authorize(
            opts.account, opts.max_rounds, opts.max_fee_per_round_sat, opts.max_total_fee_sat)
    else:
        Handler.link = DeviceLink(target, arg.replace("--baud", "").strip() if arg else None)
    server = HTTPServer(("127.0.0.1", HTTP_PORT), Handler)  # localhost only
    print("kruxd on http://127.0.0.1:%d -> %s" % (HTTP_PORT, target))
    server.serve_forever()


if __name__ == "__main__":
    main()
