#!/usr/bin/env python3
"""kruxd - trezord-style localhost bridge for the Krux CoinJoin remote signer.

Talks the framed link protocol (4-byte BE length + payload) to a Krux device
on the "CoinJoin USB" screen, over serial (real device) or TCP (simulator).
Exposes a small localhost HTTP API for a Wasabi KeyChain:

  POST /info               -> {"fingerprint": hex, "rounds_used": n, "max_rounds": n}
  POST /proof {"script_type": "p2wpkh"|"p2tr", "path": [uint32...],
               "commitment": hex}          -> {"proof": hex}
  POST /sign  {"psbt": base64}             -> {"psbt": base64}

Usage: kruxd.py COM8 [--baud 115200]   (serial)
       kruxd.py sim [host]             (simulator TCP :52123)
"""
import base64
import json
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
SCRIPT_TYPES = {"p2wpkh": 0, "p2tr": 1}
MAGIC = b"KXJ1"  # frame delimiter; must match the extension link.py


class DeviceLink:
    """One framed request/response at a time against the device link."""

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


class Handler(BaseHTTPRequestHandler):
    link = None  # set at startup

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/info":
                body = self.link.request(bytes([CMD_INFO]))
                result = {
                    "fingerprint": body[:4].hex(),
                    "rounds_used": int.from_bytes(body[4:6], "big"),
                    "max_rounds": int.from_bytes(body[6:8], "big"),
                    "authorized": bool(body[8]) if len(body) > 8 else False,
                }
            elif self.path == "/authorize":
                payload = (
                    bytes([CMD_AUTHORIZE])
                    + int(req["max_rounds"]).to_bytes(2, "big")
                    + int(req["max_fee_rate_sat_vb"]).to_bytes(2, "big")
                    + bytes([int(req["min_self_transfer_pct"])])
                )
                try:
                    self.link.request(payload)  # blocks on the device confirmation
                except ValueError:
                    # A garbled response frame (e.g. boot/console noise) can look
                    # like a device error even though the device authorized.
                    # Re-check /info before surfacing a failure.
                    info = self.link.request(bytes([CMD_INFO]))
                    if not (len(info) > 8 and info[8]):
                        raise
                result = {"authorized": True}
            elif self.path == "/proof":
                payload = bytes([CMD_PROOF, SCRIPT_TYPES[req["script_type"]]])
                path = req["path"]
                payload += bytes([len(path)])
                for index in path:
                    payload += int(index).to_bytes(4, "big")
                payload += bytes.fromhex(req["commitment"])
                result = {"proof": self.link.request(payload).hex()}
            elif self.path == "/sign":
                psbt = base64.b64decode(req["psbt"])
                signed = self.link.request(bytes([CMD_SIGN]) + psbt)
                result = {"psbt": base64.b64encode(signed).decode()}
            else:
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
    Handler.link = DeviceLink(target, arg.replace("--baud", "").strip() if arg else None)
    server = HTTPServer(("127.0.0.1", HTTP_PORT), Handler)  # localhost only
    print("kruxd on http://127.0.0.1:%d -> %s" % (HTTP_PORT, target))
    server.serve_forever()


if __name__ == "__main__":
    main()
