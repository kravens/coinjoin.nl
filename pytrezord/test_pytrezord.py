#!/usr/bin/env python3
# Unit tests for pytrezord.py - run: python3 test_pytrezord.py  (no device needed)
import json, struct, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer

import pytrezord
from pytrezord import Bridge, DeviceGone, Handler


class FakeTransport:
	"""Behaves like one connected Trezor: echoes a Success(2) reply carrying the
	request type+length, and records everything written."""

	def __init__(self, paths=("1:11",)):
		self.paths = list(paths)
		self.opened = []
		self.written = []
		self.queue = []  # canned replies (type, payload); default = echo

	def enumerate(self):
		return sorted(self.paths)

	def open(self, path):
		if path not in self.paths:
			raise DeviceGone(path)
		self.opened.append(path)

	def close(self, path):
		if path in self.opened:
			self.opened.remove(path)

	def write(self, path, message):
		if path not in self.opened:
			raise DeviceGone(path)
		self.written.append((path, message))

	def read(self, path):
		if path not in self.opened:
			raise DeviceGone(path)
		if self.queue:
			mtype, payload = self.queue.pop(0)
		else:
			mtype, payload = 2, self.written[-1][1][:6]  # echo request header back
		return struct.pack(">HI", mtype, len(payload)) + payload


class BridgeTests(unittest.TestCase):
	def setUp(self):
		self.t = FakeTransport()
		self.b = Bridge(self.t)

	def test_acquire_release_lifecycle(self):
		s = self.b.acquire("1:11", None)
		self.assertEqual(self.b.enumerate()[0]["session"], s)
		self.b.release(s)
		self.assertIsNone(self.b.enumerate()[0]["session"])
		self.assertEqual(self.t.opened, [])

	def test_acquire_wrong_previous_rejected(self):
		self.b.acquire("1:11", None)
		with self.assertRaises(ValueError):
			self.b.acquire("1:11", None)  # someone holds it, previous must match

	def test_acquire_steals_with_right_previous(self):
		s1 = self.b.acquire("1:11", None)
		s2 = self.b.acquire("1:11", s1)
		self.assertNotEqual(s1, s2)
		with self.assertRaises(KeyError):
			self.b.call(s1, b"\0\0\0\0\0\0")  # stolen session is dead

	def test_call_roundtrip_and_post_read_pair(self):
		s = self.b.acquire("1:11", None)
		msg = struct.pack(">HI", 55, 0)  # GetFeatures
		reply = self.b.call(s, msg)
		self.assertEqual(struct.unpack(">HI", reply[:6])[0], 2)
		self.b.post(s, msg)
		self.assertEqual(self.t.written[-1][1], msg)
		self.t.queue.append((17, b"features"))
		self.assertEqual(self.b.read(s)[6:], b"features")

	def test_device_unplug_drops_session(self):
		s = self.b.acquire("1:11", None)
		self.t.opened.clear()  # simulates the cable coming out
		with self.assertRaises(DeviceGone):
			self.b.call(s, b"\0\0\0\0\0\0")
		with self.assertRaises(KeyError):
			self.b.call(s, b"\0\0\0\0\0\0")  # session cleaned up


class HttpTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.transport = FakeTransport()
		Handler.bridge = Bridge(cls.transport)
		Handler.verbose = False
		cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		cls.port = cls.server.server_address[1]
		threading.Thread(target=cls.server.serve_forever, daemon=True).start()

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()
		cls.server.server_close()

	def _post(self, path, body="", origin=None):
		req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body.encode(), method="POST")
		if origin:
			req.add_header("Origin", origin)
		try:
			with urllib.request.urlopen(req, timeout=10) as r:
				return r.status, r.read().decode()
		except urllib.error.HTTPError as e:
			return e.code, e.read().decode()

	def test_version(self):
		code, body = self._post("/")
		self.assertEqual(code, 200)
		self.assertEqual(json.loads(body)["version"], pytrezord.VERSION)

	def test_full_wasabi_flow(self):
		code, body = self._post("/enumerate")
		self.assertEqual(code, 200)
		path = json.loads(body)[0]["path"]

		code, body = self._post(f"/acquire/{path}/null")
		session = json.loads(body)["session"]

		code, body = self._post(f"/call/{session}", struct.pack(">HI", 0, 0).hex())
		self.assertEqual(code, 200)
		self.assertEqual(struct.unpack(">HI", bytes.fromhex(body)[:6])[0], 2)

		code, _ = self._post(f"/release/{session}")
		self.assertEqual(code, 200)

	def test_errors(self):
		self.assertEqual(self._post("/call/999", "0000")[0], 400)          # session not found
		self.assertEqual(self._post("/acquire/nope/null")[0], 400)         # no such device
		self.assertEqual(self._post("/nonsense")[0], 404)

	def test_origin_policy(self):
		self.assertEqual(self._post("/enumerate", origin="https://wallet.trezor.io")[0], 200)
		self.assertEqual(self._post("/enumerate", origin="http://127.0.0.1:8080")[0], 200)
		self.assertEqual(self._post("/enumerate", origin="https://evil.example")[0], 403)
		self.assertEqual(self._post("/enumerate", origin="https://eviltrezor.io")[0], 403)
		self.assertEqual(self._post("/enumerate")[0], 200)                  # no Origin = CLI client

	def test_listen_returns_on_change(self):
		code, body = self._post("/listen", json.dumps([]))  # differs from reality -> immediate
		self.assertEqual(code, 200)
		self.assertEqual(json.loads(body)[0]["path"], "1:11")


class FramingTests(unittest.TestCase):
	"""Chunking against a recording endpoint pair, covering the report boundaries."""

	class Endpoints:
		def __init__(self):
			self.reports = []
			self.bus, self.address = 1, 11

		def write(self, ep, data):
			assert len(data) == 64
			self.reports.append(bytes(data))

		def read(self, ep, n, timeout=None):
			return self.reports.pop(0)

		def is_kernel_driver_active(self, i):
			return False

		def get_active_configuration(self):
			return object()

	def _transport_with(self, dev):
		t = pytrezord.UsbTransport.__new__(pytrezord.UsbTransport)
		import usb.core, usb.util
		t._usb, t._util, t._backend = usb.core, usb.util, None
		t._open = {"1:11": dev}
		return t

	def test_write_read_roundtrip_across_report_boundary(self):
		for payload_len in (0, 1, 55, 56, 57, 63, 64, 200):  # 55 fills report 1 exactly
			dev = self.Endpoints()
			t = self._transport_with(dev)
			msg = struct.pack(">HI", 17, payload_len) + bytes(range(256))[:payload_len] * 1
			t.write("1:11", msg)
			self.assertTrue(all(r[0:1] == b"?" for r in dev.reports))
			self.assertTrue(dev.reports[0].startswith(b"?##"))
			self.assertEqual(t.read("1:11"), msg, f"payload_len={payload_len}")


if __name__ == "__main__":
	unittest.main(verbosity=2)
