#!/usr/bin/env python3
# -*- coding: utf-8 -*- ######  P Y T R E Z O R D  ·  coinjoin.nl  #############
#  A drop-in Trezor Bridge (trezord) replacement in one Python file, for       #
#  people who don't want to build or run the retired trezord-go binary.        #
#  Speaks the classic bridge HTTP API on 127.0.0.1:21325, so Wasabi Wallet,    #
#  HWI and anything else written against trezord keeps working.                #
#                                                                              #
#  Setup:  pip install pyusb libusb-package                                    #
#          (Linux: udev rule for 1209:53c1, see --udev)                        #
#  Run:    python3 pytrezord.py [--port 21325] [--verbose]                     #
#  Test:   python3 test_pytrezord.py   (no device needed)                      #
#                                                                              #
#  Supports Trezor Model T / Safe 3 / Safe 5 (WebUSB). The Model One speaks    #
#  HID, needs different plumbing, and cannot coinjoin - it is not supported.   #
#  Close Trezor Suite while this runs: only one program can hold the USB.     #
################################################################################
import argparse, json, re, struct, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.0.33"                    # bridge protocol version we are compatible with
TREZOR_IDS = [(0x1209, 0x53C1)]       # Model T / Safe family (WebUSB, normal mode)
ENDPOINT_OUT, ENDPOINT_IN = 0x01, 0x81
REPORT_LEN = 64                       # USB interrupt report size
READ_TIMEOUT_MS = 10 * 60 * 1000      # device calls block until the user reacts
LISTEN_POLL_S, LISTEN_MAX_S = 0.5, 30

# Origins allowed to talk to the bridge from a browser. Non-browser clients send no
# Origin header and are always allowed (a browser cannot strip its own Origin).
ORIGIN_RE = re.compile(r"^https://([\w-]+\.)*trezor\.io$|^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


# ---- USB transport ----------------------------------------------------------------
class DeviceGone(Exception):
	pass


class UsbTransport:
	"""Finds Trezors over libusb and moves protocol-v1 frames in 64-byte reports."""

	def __init__(self):
		import libusb_package
		import usb.backend.libusb1
		self._usb = __import__("usb.core", fromlist=["core"])
		self._util = __import__("usb.util", fromlist=["util"])
		self._backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
		if self._backend is None:
			raise RuntimeError("libusb backend not found - pip install libusb-package")
		self._open = {}  # path -> usb.core.Device

	def enumerate(self):
		paths = []
		for vid, pid in TREZOR_IDS:
			for dev in self._usb.find(find_all=True, backend=self._backend, idVendor=vid, idProduct=pid):
				paths.append(f"{dev.bus}:{dev.address}")
		return sorted(paths)

	def _find(self, path):
		for vid, pid in TREZOR_IDS:
			for dev in self._usb.find(find_all=True, backend=self._backend, idVendor=vid, idProduct=pid):
				if f"{dev.bus}:{dev.address}" == path:
					return dev
		raise DeviceGone(path)

	def open(self, path):
		dev = self._find(path)
		try:
			if dev.is_kernel_driver_active(0):  # Linux: the kernel may hold interface 0
				dev.detach_kernel_driver(0)
		except (NotImplementedError, self._usb.USBError):
			pass  # Windows/macOS have no kernel driver concept here
		try:
			dev.get_active_configuration()
		except self._usb.USBError:
			dev.set_configuration()
		self._util.claim_interface(dev, 0)
		self._open[path] = dev

	def close(self, path):
		dev = self._open.pop(path, None)
		if dev is not None:
			try:
				self._util.release_interface(dev, 0)
			except self._usb.USBError:
				pass
			self._util.dispose_resources(dev)

	def write(self, path, message):
		"""message = type(u16 BE) + length(u32 BE) + payload, as the bridge API frames it."""
		dev = self._open.get(path)
		if dev is None:
			raise DeviceGone(path)
		stream = b"##" + message  # '##' magic + header+payload, then chopped into '?' reports
		try:
			for i in range(0, len(stream), REPORT_LEN - 1):
				chunk = b"?" + stream[i:i + REPORT_LEN - 1]
				dev.write(ENDPOINT_OUT, chunk.ljust(REPORT_LEN, b"\0"))
		except self._usb.USBError as e:
			raise DeviceGone(path) from e

	def read(self, path):
		dev = self._open.get(path)
		if dev is None:
			raise DeviceGone(path)
		try:
			report = bytes(dev.read(ENDPOINT_IN, REPORT_LEN, timeout=READ_TIMEOUT_MS))
			if not report.startswith(b"?##"):
				raise DeviceGone(path)  # protocol desync - drop the device rather than guess
			_, length = struct.unpack(">HI", report[3:9])
			data = report[3:]
			while len(data) < 6 + length:
				cont = bytes(dev.read(ENDPOINT_IN, REPORT_LEN, timeout=READ_TIMEOUT_MS))
				data += cont[1:]  # continuation reports repeat the '?' magic only
			return data[:6 + length]
		except self._usb.USBError as e:
			raise DeviceGone(path) from e


# ---- sessions ---------------------------------------------------------------------
class Bridge:
	"""Session bookkeeping exactly like trezord: one session owns a device, an acquire
	with the right previous session steals it (that is how clients recover)."""

	def __init__(self, transport):
		self.transport = transport
		self._lock = threading.Lock()
		self._counter = 0
		self._sessions = {}     # session -> path
		self._by_path = {}      # path -> session
		self._device_locks = {} # path -> Lock, serializes USB i/o per device

	def enumerate(self):
		with self._lock:
			return [
				{
					"path": p,
					"vendor": TREZOR_IDS[0][0],
					"product": TREZOR_IDS[0][1],
					"debug": False,
					"session": self._by_path.get(p),
					"debugSession": None,
				}
				for p in self.transport.enumerate()
			]

	def acquire(self, path, previous):
		with self._lock:
			current = self._by_path.get(path)
			if (previous or None) != current:
				raise ValueError("wrong previous session")
			if current is not None:
				self._sessions.pop(current, None)
				self.transport.close(path)
			self.transport.open(path)
			self._counter += 1
			session = str(self._counter)
			self._sessions[session] = path
			self._by_path[path] = session
			self._device_locks.setdefault(path, threading.Lock())
			return session

	def release(self, session):
		with self._lock:
			path = self._sessions.pop(session, None)
			if path is None:
				raise KeyError("session not found")
			self._by_path.pop(path, None)
			self.transport.close(path)

	def _path_for(self, session):
		with self._lock:
			path = self._sessions.get(session)
			if path is None:
				raise KeyError("session not found")
			return path, self._device_locks[path]

	def _drop(self, path):
		with self._lock:
			session = self._by_path.pop(path, None)
			self._sessions.pop(session, None)
			self.transport.close(path)

	def call(self, session, message):
		path, lock = self._path_for(session)
		with lock:
			try:
				self.transport.write(path, message)
				return self.transport.read(path)
			except DeviceGone:
				self._drop(path)
				raise

	def post(self, session, message):
		path, lock = self._path_for(session)
		with lock:
			try:
				self.transport.write(path, message)
			except DeviceGone:
				self._drop(path)
				raise

	def read(self, session):
		path, lock = self._path_for(session)
		with lock:
			try:
				return self.transport.read(path)
			except DeviceGone:
				self._drop(path)
				raise


# ---- HTTP -------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
	bridge = None      # set by serve()
	verbose = False
	protocol_version = "HTTP/1.1"

	def log_message(self, fmt, *args):
		if self.verbose:
			sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

	def _reply(self, code, body, content_type="application/json"):
		data = body.encode() if isinstance(body, str) else body
		self.send_response(code)
		origin = self.headers.get("Origin")
		if origin and ORIGIN_RE.match(origin):
			self.send_header("Access-Control-Allow-Origin", origin)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	def _fail(self, code, message):
		self._reply(code, json.dumps({"error": message}))

	def do_OPTIONS(self):  # browser CORS preflight
		self.send_response(204)
		origin = self.headers.get("Origin")
		if origin and ORIGIN_RE.match(origin):
			self.send_header("Access-Control-Allow-Origin", origin)
			self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
			self.send_header("Access-Control-Allow-Headers", "Content-Type")
		self.send_header("Content-Length", "0")
		self.end_headers()

	def do_POST(self):
		origin = self.headers.get("Origin")
		if origin and not ORIGIN_RE.match(origin):
			return self._fail(403, "origin not allowed")

		length = int(self.headers.get("Content-Length") or 0)
		body = self.rfile.read(length).decode("ascii", "replace") if length else ""
		parts = [p for p in self.path.split("/") if p]

		try:
			if not parts:
				return self._reply(200, json.dumps({"version": VERSION, "githash": "pytrezord"}))
			if parts[0] == "enumerate":
				return self._reply(200, json.dumps(self.bridge.enumerate()))
			if parts[0] == "listen":
				return self._listen(body)
			if parts[0] == "acquire" and len(parts) == 3:
				previous = None if parts[2] == "null" else parts[2]
				session = self.bridge.acquire(parts[1], previous)
				return self._reply(200, json.dumps({"session": session}))
			if parts[0] == "release" and len(parts) == 2:
				self.bridge.release(parts[1])
				return self._reply(200, json.dumps({"session": parts[1]}))
			if parts[0] == "call" and len(parts) == 2:
				response = self.bridge.call(parts[1], bytes.fromhex(body))
				return self._reply(200, response.hex(), "text/plain")
			if parts[0] == "post" and len(parts) == 2:
				self.bridge.post(parts[1], bytes.fromhex(body))
				return self._reply(200, "", "text/plain")
			if parts[0] == "read" and len(parts) == 2:
				response = self.bridge.read(parts[1])
				return self._reply(200, response.hex(), "text/plain")
			return self._fail(404, "unknown endpoint")
		except KeyError as e:
			return self._fail(400, str(e).strip("'"))
		except ValueError as e:
			return self._fail(400, str(e))
		except DeviceGone:
			return self._fail(400, "device disconnected during action")

	def _listen(self, body):
		"""Long-poll: return the device list once it differs from what the client sent."""
		try:
			known = json.loads(body) if body else []
		except json.JSONDecodeError:
			known = []
		deadline = time.monotonic() + LISTEN_MAX_S
		while time.monotonic() < deadline:
			current = self.bridge.enumerate()
			if current != known:
				return self._reply(200, json.dumps(current))
			time.sleep(LISTEN_POLL_S)
		return self._reply(200, json.dumps(self.bridge.enumerate()))


UDEV_RULE = 'SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="53c1", MODE="0660", GROUP="plugdev", TAG+="uaccess"'


def serve(port, verbose, transport=None):
	Handler.bridge = Bridge(transport or UsbTransport())
	Handler.verbose = verbose
	server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
	print(f"pytrezord {VERSION}-compatible bridge on http://127.0.0.1:{port} (Ctrl+C stops)")
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		print("\nbye")
	finally:
		server.server_close()
	return server


if __name__ == "__main__":
	ap = argparse.ArgumentParser(description="Trezor Bridge (trezord) replacement - coinjoin.nl")
	ap.add_argument("--port", type=int, default=21325, help="listen port (default 21325)")
	ap.add_argument("--verbose", action="store_true", help="log every request")
	ap.add_argument("--udev", action="store_true", help="print the Linux udev rule and exit")
	args = ap.parse_args()
	if args.udev:
		print(f"# /etc/udev/rules.d/51-trezor.rules\n{UDEV_RULE}")
		sys.exit(0)
	serve(args.port, args.verbose)
