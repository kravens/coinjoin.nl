#!/usr/bin/env python3
# Unit tests for pytrezord.py - run: python3 test_pytrezord.py  (no device needed)
import contextlib, io, json, struct, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from unittest import mock

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
		with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(DeviceGone) as raised:
			self.b.call(s, b"\0\0\0\0\0\0")
		self.assertEqual(raised.exception.reason, "not connected")
		self.assertIn(f"device 1:11 dropped, session {s} is gone: not connected", err.getvalue())  # always logged
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

	def test_zero_length_reads_are_skipped(self):
		dev = self.Endpoints()
		t = self._transport_with(dev)
		msg = struct.pack(">HI", 17, 100) + bytes(100)
		t.write("1:11", msg)
		dev.reports[1:1] = [b""]
		dev.reports.insert(0, b"")
		self.assertEqual(t.read("1:11"), msg)

	def test_desync_names_the_offending_report(self):
		dev = self.Endpoints()
		dev.reports.append(b"?" + b"\0" * 63)  # a continuation report where a header was expected
		with self.assertRaises(DeviceGone) as raised:
			self._transport_with(dev).read("1:11")
		self.assertTrue(raised.exception.reason.startswith("protocol desync"), raised.exception.reason)


class FakeThpChannel:
	"""A Safe 7 behind ThpLink: GetPublicKey needs a seeded session, session 0 has none."""
	passphrase_protection = False
	pin_prompt = False  # show a PIN screen (ButtonRequest) before answering GetPublicKey

	def __init__(self, *args):
		self.sessions, self.replies, self.after_ack = {}, [], None
		FakeThpChannel.last = self

	def send(self, session, mtype, payload):
		if mtype == 55:
			self.replies.append((17, pytrezord.pb((8, self.passphrase_protection))))
		elif mtype == 1000:
			self.sessions[session] = pytrezord.unpb(payload)
			self.replies.append((2, b""))
		elif mtype == 11:
			reply = (12, b"xpub") if session in self.sessions else (3, pytrezord.pb((1, 14)))
			if FakeThpChannel.pin_prompt:
				FakeThpChannel.pin_prompt, self.after_ack = False, reply
				reply = (26, b"")
			self.replies.append(reply)
		elif mtype == 27:
			self.replies.append(self.after_ack)

	def receive(self):
		return (0,) + self.replies.pop(0)


@mock.patch.object(pytrezord, "ThpChannel", FakeThpChannel)
class ThpLinkTests(unittest.TestCase):
	def setUp(self):
		FakeThpChannel.passphrase_protection = FakeThpChannel.pin_prompt = False
		self.link = pytrezord.ThpLink(None, "1:11", None, None)

	def call(self, mtype, payload=b""):
		self.link.write(pytrezord.v1_frame(mtype, payload))
		reply = self.link.read()
		return struct.unpack(">H", reply[:2])[0], reply[6:]

	def test_initialize_is_get_features_and_seed_gets_a_session(self):
		self.assertEqual(self.call(0)[0], 17)
		self.assertEqual(self.call(11), (12, b"xpub"))
		self.assertEqual(FakeThpChannel.last.sessions, {1: {1: [b""]}})  # empty passphrase, no prompt
		self.assertEqual(self.call(11), (12, b"xpub"))  # stays on session 1
		self.call(0)
		self.call(11)
		self.assertEqual(list(FakeThpChannel.last.sessions), [1, 2])  # Initialize starts over

	def test_passphrase_is_asked_from_the_client(self):
		FakeThpChannel.passphrase_protection = True
		self.call(0)
		self.assertEqual(self.call(11)[0], 41)  # PassphraseRequest
		self.assertEqual(self.call(42, pytrezord.pb((1, "hidden"))), (12, b"xpub"))
		self.assertEqual(FakeThpChannel.last.sessions[1], {1: [b"hidden"]})
		self.call(0)
		self.call(11)
		self.call(42, pytrezord.pb((3, True)))  # on_device
		self.assertEqual(FakeThpChannel.last.sessions[2], {2: [1]})

	def test_request_after_pin_screen_is_resent_not_the_button_ack(self):
		FakeThpChannel.pin_prompt = True
		self.call(0)
		self.assertEqual(self.call(11)[0], 26)
		self.assertEqual(self.call(27), (12, b"xpub"))


class ThpBridgeTests(unittest.TestCase):
	@mock.patch.object(pytrezord, "ThpChannel", FakeThpChannel)
	def test_invalid_protocol_switches_device_to_thp(self):
		t = FakeTransport()
		b = Bridge(t)
		s = b.acquire("1:11", None)
		t.queue.append((3, bytes.fromhex("0811")))  # Failure(InvalidProtocol): a Safe 7
		self.assertEqual(struct.unpack(">H", b.call(s, struct.pack(">HI", 0, 0))[:2])[0], 17)
		b.release(s)
		s = b.acquire("1:11", None)  # the THP channel outlives the bridge session
		self.assertEqual(struct.unpack(">H", b.call(s, struct.pack(">HI", 55, 0))[:2])[0], 17)
		self.assertEqual(len(t.written), 1)  # only the probe went out as v1
		with mock.patch.object(FakeThpChannel, "send", side_effect=DeviceGone("1:11", "unplugged")), contextlib.redirect_stderr(io.StringIO()):
			with self.assertRaises(DeviceGone):
				b.call(s, struct.pack(">HI", 55, 0))
		self.assertNotIn("1:11", b._thp)


class ThpWireTests(unittest.TestCase):
	class Packets:
		def __init__(self, packets):
			self.packets, self.written = list(packets), []

		def read_packet(self, path, timeout_ms):
			if not self.packets:
				raise pytrezord.DeviceTimeout(path, "timeout")
			return self.packets.pop(0)

		def write_packet(self, path, packet):
			self.written.append(packet)

	def channel(self, packets):
		ch = pytrezord.ThpChannel.__new__(pytrezord.ThpChannel)
		ch.t, ch.path, ch.cid, ch.recv_bit, ch.send_bit, ch.inbox = self.Packets(packets), "1:11", 0x1234, 0, 0, []
		return ch

	def test_reassembly_skips_bad_crc_and_other_channels(self):
		data = bytes(range(256)) * 2
		good = pytrezord.thp_packets(0x04, 0x1234, data)
		self.assertEqual((len(good), {len(p) for p in good}), (9, {64}))
		bad = pytrezord.thp_packets(0x04, 0x1234, b"x")[0]
		bad = bad[:5] + b"y" + bad[6:]  # payload no longer matches the CRC
		stray = struct.pack(">BH", 0x80, 0x9999).ljust(64, b"\0")  # another channel's continuation
		ch = self.channel([bad, good[0], stray] + good[1:])
		self.assertEqual(ch._read(1000), (0x04, data))

	def test_every_message_is_acked_and_repeats_are_dropped(self):
		first, second = (pytrezord.thp_packets(0x04 | seq << 4, 0x1234, bytes([seq]))[0] for seq in (0, 1))
		ch = self.channel([first, first, second])
		self.assertEqual(ch._next(1000), (0x04, b"\0"))
		self.assertEqual(ch._next(1000), (0x14, b"\1"))  # the repeat of the first was dropped
		self.assertEqual([p[0] for p in ch.t.written], [0x20, 0x20, 0x28])  # ...but ACKed again

	def test_cpace_generator_known_answers(self):  # values from trezorlib's independent implementation
		self.assertEqual(pytrezord._elligator2(bytes(range(32))).hex(), "5f3520001c6c9936a31206afe7c7ac224e8861619bf98872444915899d95f46e")
		self.assertEqual(pytrezord._cpace_generator("123456", bytes(32)).hex(), "a29b0057e8cef8c2f9d7bbf081f47cc23ab90631157eb2340d97d0915519b218")


if __name__ == "__main__":
	unittest.main(verbosity=2)
