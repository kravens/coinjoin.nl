#!/usr/bin/env python3
# -*- coding: utf-8 -*- ######  P Y T R E Z O R D  ·  coinjoin.nl  #############
#  A drop-in Trezor Bridge (trezord) replacement in one Python file, for       #
#  people who don't want to build or run the retired trezord-go binary.        #
#  Speaks the classic bridge HTTP API on 127.0.0.1:21325, so Wasabi Wallet,    #
#  HWI and anything else written against trezord keeps working.                #
#                                                                              #
#  Setup:  pip install pyusb libusb-package cryptography                       #
#          (Linux: udev rule for 1209:53c1, see --udev)                        #
#  Run:    python3 pytrezord.py [--port 21325] [--verbose]                     #
#  Test:   python3 test_pytrezord.py   (no device needed)                      #
#                                                                              #
#  Supports Trezor Model T / Safe 3 / Safe 5 (codec v1) and Safe 7 (THP, the   #
#  encrypted Trezor-Host Protocol: pairs once - type the code the Trezor       #
#  shows into this terminal - and keeps serving v1 to its HTTP clients).       #
#  The Model One speaks HID and cannot coinjoin - it is not supported.         #
#  Close Trezor Suite while this runs: only one program can hold the USB.      #
################################################################################
import argparse, hashlib, hmac, json, os, random, re, secrets, socket, struct, sys, threading, time, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # only Trezors that speak THP (Safe 7 and newer) need it
	from cryptography.exceptions import InvalidTag
	from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
	from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
	AESGCM = None

VERSION = "2.0.33"                    # bridge protocol version we are compatible with
TREZOR_IDS = [(0x1209, 0x53C1)]       # Model T / Safe family (WebUSB, normal mode)
ENDPOINT_OUT, ENDPOINT_IN = 0x01, 0x81
REPORT_LEN = 64                       # USB interrupt report size
READ_TIMEOUT_MS = 10 * 60 * 1000      # device calls block until the user reacts
LISTEN_POLL_S, LISTEN_MAX_S = 0.5, 30

# Origins allowed to talk to the bridge from a browser. Non-browser clients send no
# Origin header and are always allowed (a browser cannot strip its own Origin).
ORIGIN_RE = re.compile(r"^https://([\w-]+\.)*trezor\.io$|^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


def log(message):
	"""Always-on diagnostics (a dropped device, and why); per-request logging needs --verbose."""
	sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


# ---- USB transport ----------------------------------------------------------------
class DeviceGone(Exception):
	"""The device cannot be used any more: unplugged, a USB error, or a frame we cannot parse.
	The reason is logged when the bridge drops the session, so a dead session can be explained
	later - the HTTP client only ever sees 'device disconnected during action'."""

	def __init__(self, path, reason="not connected"):
		super().__init__(f"{path}: {reason}")
		self.path, self.reason = path, reason


class DeviceTimeout(DeviceGone):
	"""Nothing arrived in time. THP retransmits on it; everywhere else it ends the session."""


class UsbTransport:
	"""Finds Trezors over libusb and moves 64-byte reports; codec v1 framing on top."""

	def __init__(self):
		import usb.backend.libusb1
		self._usb = __import__("usb.core", fromlist=["core"])
		self._util = __import__("usb.util", fromlist=["util"])
		try:  # only needed where the system has no libusb of its own
			import libusb_package

			find_library = libusb_package.find_library
		except ImportError:
			find_library = None
		self._backend = usb.backend.libusb1.get_backend(find_library=find_library)
		if self._backend is None:
			raise RuntimeError("libusb backend not found - install libusb, or pip install libusb-package")
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
		raise DeviceGone(path, "not enumerated")

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

	def write_packet(self, path, packet):
		dev = self._open.get(path)
		if dev is None:
			raise DeviceGone(path, "not open")
		try:
			dev.write(ENDPOINT_OUT, packet)
		except self._usb.USBError as e:
			raise DeviceGone(path, f"usb write failed: {e}") from e

	def read_packet(self, path, timeout_ms=READ_TIMEOUT_MS):
		dev = self._open.get(path)
		if dev is None:
			raise DeviceGone(path, "not open")
		try:
			while True:  # skip zero-length reads (seen on Linux), as trezord-go and trezorlib do
				report = bytes(dev.read(ENDPOINT_IN, REPORT_LEN, timeout=timeout_ms))
				if report:
					return report
		except self._usb.USBTimeoutError as e:
			raise DeviceTimeout(path, f"usb read timed out: {e}") from e
		except self._usb.USBError as e:
			raise DeviceGone(path, f"usb read failed: {e}") from e

	def write(self, path, message):
		"""message = type(u16 BE) + length(u32 BE) + payload, as the bridge API frames it."""
		stream = b"##" + message  # '##' magic + header+payload, then chopped into '?' reports
		for i in range(0, len(stream), REPORT_LEN - 1):
			self.write_packet(path, (b"?" + stream[i:i + REPORT_LEN - 1]).ljust(REPORT_LEN, b"\0"))

	def read(self, path):
		report = self.read_packet(path)
		if not report.startswith(b"?##"):
			# Protocol desync - drop the device rather than guess. Usually the reply to an earlier
			# request that the client gave up on, arriving where a fresh message header was expected.
			raise DeviceGone(path, f"protocol desync: report starts with {report[:9]!r}")
		_, length = struct.unpack(">HI", report[3:9])
		data = report[3:]
		while len(data) < 6 + length:
			data += self.read_packet(path)[1:]  # continuation reports repeat the '?' magic only
		return data[:6 + length]


# ---- THP: Trezor-Host Protocol (Safe 7 and newer) ---------------------------------
# These devices answer any codec v1 packet with Failure(InvalidProtocol) and only talk over
# an encrypted channel (Noise XX: X25519, AES-GCM, SHA-256) that a host has to pair once.
# pytrezord ends THP here and keeps serving v1 frames on HTTP, so Wasabi and HWI see an
# ordinary Trezor. Spec: trezor-firmware/docs/common/thp/specification.md and sessions.md.
INVALID_PROTOCOL = bytes.fromhex("0003" "00000002" "0811")  # v1 Failure(code=InvalidProtocol)
THP_BROADCAST = 0xFFFF
THP_ALLOC_REQ, THP_ALLOC_RES, THP_ERROR = 0x40, 0x41, 0x42
THP_HS_INIT, THP_HS_COMPLETE, THP_ENCRYPTED = 0x00, 0x02, 0x04  # the device answers a handshake step with +1
THP_ERRORS = {1: "device busy", 2: "unallocated channel", 3: "decryption failed", 5: "device locked"}
THP_ACK_TIMEOUT_MS, THP_RETRIES = 1000, 30
NOISE_NAME = b"Noise_XX_25519_AESGCM_SHA256\0\0\0\0"
CREDENTIALS = os.path.join(os.path.expanduser("~"), ".pytrezord", "thp-credentials.json")
CODE_ENTRY = 2  # the one pairing method current firmware offers over USB

MT_INITIALIZE, MT_SUCCESS, MT_FAILURE, MT_FEATURES, MT_BUTTON_REQUEST, MT_BUTTON_ACK = 0, 2, 3, 17, 26, 27
MT_PASSPHRASE_REQUEST, MT_PASSPHRASE_ACK, MT_GET_FEATURES, MT_CREATE_SESSION = 41, 42, 55, 1000
MT_PAIRING_REQUEST, MT_PAIRING_APPROVED, MT_SELECT_METHOD = 1008, 1009, 1010
MT_CREDENTIAL_REQUEST, MT_CREDENTIAL_RESPONSE, MT_END_REQUEST, MT_END_RESPONSE = 1016, 1017, 1018, 1019
MT_CODE_COMMITMENT, MT_CODE_CHALLENGE, MT_CODE_CPACE_TREZOR, MT_CODE_CPACE_HOST, MT_CODE_SECRET = 1024, 1025, 1026, 1027, 1028
FAILURE_PROCESS_ERROR, FAILURE_INVALID_SESSION = 9, 14


def v1_frame(mtype, payload):
	return struct.pack(">HI", mtype, len(payload)) + payload


def _varint(n):
	out = bytearray()
	while True:
		out.append(n & 0x7F | (0x80 if n > 0x7F else 0))
		n >>= 7
		if not n:
			return bytes(out)


def pb(*fields):
	"""Protobuf-encode (field number, value) pairs: ints/bools as varints, str/bytes length-delimited, None left out."""
	out = b""
	for number, value in fields:
		if value is None:
			continue
		if isinstance(value, str):
			value = value.encode()
		if isinstance(value, bytes):
			out += _varint(number << 3 | 2) + _varint(len(value)) + value
		else:
			out += _varint(number << 3) + _varint(int(value))
	return out


def unpb(data):
	"""Protobuf fields as {number: [values]}: varints as ints, everything else as bytes."""
	fields, i = {}, 0

	def varint():
		nonlocal i
		n = shift = 0
		while True:
			byte = data[i]
			i += 1
			n |= (byte & 0x7F) << shift
			shift += 7
			if byte < 0x80:
				return n

	while i < len(data):
		key = varint()
		wire = key & 7
		if wire == 0:
			value = varint()
		else:
			size = varint() if wire == 2 else {1: 8, 5: 4}[wire]
			value, i = data[i:i + size], i + size
		fields.setdefault(key >> 3, []).append(value)
	return fields


def thp_packets(ctrl, cid, data):
	"""One THP message as 64-byte packets: header, data and CRC-32 (over header+data), split over continuations."""
	body = struct.pack(">BHH", ctrl, cid, len(data) + 4) + data
	body += zlib.crc32(body).to_bytes(4, "big")
	packets = [body[:REPORT_LEN]]
	for i in range(REPORT_LEN, len(body), REPORT_LEN - 3):
		packets.append(struct.pack(">BH", 0x80, cid) + body[i:i + REPORT_LEN - 3])
	return [p.ljust(REPORT_LEN, b"\0") for p in packets]


def _h(*parts):
	return hashlib.sha256(b"".join(parts)).digest()


def _hkdf(ck, ikm):
	"""Noise HKDF: (new chaining key, key)."""
	temp = hmac.new(ck, ikm, hashlib.sha256).digest()
	first = hmac.new(temp, b"\x01", hashlib.sha256).digest()
	return first, hmac.new(temp, first + b"\x02", hashlib.sha256).digest()


def _x25519(private, public):
	return X25519PrivateKey.from_private_bytes(private).exchange(X25519PublicKey.from_public_bytes(public))


def _x25519_public(private):
	return _x25519(private, (9).to_bytes(32, "little"))  # times the base point


def _elligator2(r):
	"""RFC 9380 map_to_curve_elligator2 for curve25519 (J=486662, Z=2), Montgomery u only."""
	p, J = 2**255 - 19, 486662
	u = (int.from_bytes(r, "little") & ((1 << 255) - 1)) % p
	tv1 = 2 * u * u % p
	x1 = -J * pow(1 + tv1, p - 2, p) % p
	gx1 = (x1 * x1 * x1 + J * x1 * x1 + x1) % p
	x = x1 if pow(gx1, (p - 1) // 2, p) in (0, 1) else (-x1 - J) % p
	return x.to_bytes(32, "little")


def _cpace_generator(code, handshake_hash):
	"""CPace-X25519 generator for the pairing code (THP spec, Code Entry: prefix, padding)."""
	data = b"\x08CPace255\x06" + code.encode() + b"\x6f" + b"\0" * 111 + b"\x20" + handshake_hash + b"\0"
	return _elligator2(hashlib.sha512(data).digest()[:32])


def _load_credentials(path):
	try:
		with open(path) as f:
			return [{k: bytes.fromhex(v) for k, v in c.items()} for c in json.load(f)]
	except FileNotFoundError:
		return []


def _save_credential(path, new):
	"""One credential per Trezor. It lets this host reconnect without pairing again, so owner-only."""
	creds = [c for c in _load_credentials(path) if c["trezor"] != new["trezor"]] + [new]
	os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
	fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w") as f:
		json.dump([{k: v.hex() for k, v in c.items()} for c in creds], f)
	os.replace(path + ".tmp", path)


def ask_pairing_code(channel):
	return input(f"Type the 6-digit pairing code the Trezor ({channel.path}) shows: ")


class ThpRefused(Exception):
	"""Opening the channel ended without one; .frame is the v1 Failure the client gets instead."""

	def __init__(self, message, frame=None):
		super().__init__(message)
		self.frame = frame or v1_frame(MT_FAILURE, pb((1, FAILURE_PROCESS_ERROR), (2, message)))


class ThpChannel:
	"""One encrypted THP channel to one device, allocated, handshaken and (if new) paired by the
	constructor. Carries (session id, message type, protobuf); session 0 is the seedless one."""

	def __init__(self, transport, path, ask_code, credentials):
		if AESGCM is None:
			raise ThpRefused("this Trezor speaks THP, which pytrezord needs 'pip install cryptography' for")
		self.t, self.path = transport, path
		self.cid, self.send_bit, self.recv_bit, self.inbox = None, 0, 0, []
		props = self._allocate()
		state = self._handshake(props, credentials)
		if state == 0:  # unpaired
			methods = [m for v in unpb(props).get(5, []) for m in (v if isinstance(v, bytes) else [v])]
			self._pair(methods, ask_code, credentials)
		elif state == 1:  # paired, but the device asks the user to allow the connection
			log(f"device {path}: confirm the connection on the Trezor")
		self.call(MT_END_REQUEST, b"", MT_END_RESPONSE)

	# transport layer: packets, CRC, alternating-bit ACKs
	def _write(self, ctrl, cid, data):
		for packet in thp_packets(ctrl, cid, data):
			self.t.write_packet(self.path, packet)

	def _read(self, timeout_ms):
		"""Next complete, CRC-checked message on our channel (or the broadcast one): (ctrl, data)."""
		packet = self.t.read_packet(self.path, timeout_ms)
		while True:
			if packet[0] & 0x80:  # a continuation whose start we did not see
				packet = self.t.read_packet(self.path, timeout_ms)
				continue
			head, body, restart = packet[:5], packet[5:], None
			ctrl, cid, length = struct.unpack(">BHH", head)
			while len(body) < length:
				packet = self.t.read_packet(self.path, timeout_ms)
				if not packet[0] & 0x80:
					restart = packet  # a new message began; the sender repeats the broken one
					break
				if packet[1:3] == head[1:3]:
					body += packet[3:]
			if restart:
				packet = restart
				continue
			data, crc = body[:length - 4], body[length - 4:length]
			if length >= 4 and cid in (self.cid, THP_BROADCAST) and zlib.crc32(head + data).to_bytes(4, "big") == crc:
				return ctrl, data
			packet = self.t.read_packet(self.path, timeout_ms)

	def _next(self, timeout_ms):
		"""Next ACK or new data message as (ctrl, data). Data gets ACKed; repeats are dropped."""
		while True:
			ctrl, data = self._read(timeout_ms)
			if ctrl == THP_ERROR:
				code = data[0] if data else 0
				if code == 1:  # busy with another channel: back off, the sender retransmits
					time.sleep(random.uniform(0, 0.5))
					raise DeviceTimeout(self.path, "THP: device busy")
				raise DeviceGone(self.path, f"THP: {THP_ERRORS.get(code, f'error {code}')}")
			if ctrl & 0xF7 == 0x20:  # ACK
				return ctrl, data
			if ctrl & 0xE0 == 0:  # handshake or encrypted message
				seq = ctrl >> 4 & 1
				self._write(0x20 | seq << 3, self.cid, b"")
				if seq == self.recv_bit:
					self.recv_bit ^= 1
					return ctrl, data

	def _send(self, ctrl, data):
		"""Send one data message and wait for its ACK, retransmitting on silence."""
		ctrl |= self.send_bit << 4
		for _ in range(THP_RETRIES):
			self._write(ctrl, self.cid, data)
			try:
				while True:
					reply, rdata = self._next(THP_ACK_TIMEOUT_MS)
					if reply & 0xF7 != 0x20:
						self.inbox.append((reply, rdata))  # an answer that overtook the ACK
					elif reply >> 3 & 1 == self.send_bit:
						self.send_bit ^= 1
						return
			except DeviceTimeout:
				continue
		raise DeviceGone(self.path, "THP: the device does not acknowledge")

	def _receive(self, timeout_ms=READ_TIMEOUT_MS):
		while not self.inbox:
			ctrl, data = self._next(timeout_ms)
			if ctrl & 0xF7 != 0x20:  # a late duplicate ACK is harmless
				return ctrl, data
		return self.inbox.pop(0)

	def _allocate(self):
		nonce = secrets.token_bytes(8)
		self._write(THP_ALLOC_REQ, THP_BROADCAST, nonce)
		for _ in range(THP_RETRIES):
			ctrl, data = self._read(THP_ACK_TIMEOUT_MS)
			if ctrl == THP_ALLOC_RES and data[:8] == nonce:
				self.cid = struct.unpack(">H", data[8:10])[0]
				return data[10:]  # ThpDeviceProperties, also the Noise prologue
		raise DeviceGone(self.path, "THP: no channel allocated")

	# secure channel
	def _seal(self, key, nonce, data, ad):
		return AESGCM(key).encrypt(nonce.to_bytes(12, "big"), data, ad)

	def _unseal(self, key, nonce, data, ad):
		try:
			return AESGCM(key).decrypt(nonce.to_bytes(12, "big"), data, ad)
		except InvalidTag:
			raise DeviceGone(self.path, "THP: a message failed authentication") from None

	def _handshake(self, props, credentials):
		"""Noise XX with THP's masked device key; returns the pairing state (0 unpaired, 1 paired, 2 paired, no prompt)."""
		ephemeral = secrets.token_bytes(32)
		e_pub = _x25519_public(ephemeral)
		self._send(THP_HS_INIT, e_pub + b"\x01")  # 1: if locked, the device asks for its PIN
		ctrl, data = self._receive()
		if ctrl & 0xE7 != THP_HS_INIT + 1 or len(data) != 96:
			raise DeviceGone(self.path, f"THP: bad handshake reply {ctrl:#04x}")
		trezor_e, enc_static, tag = data[:32], data[32:80], data[80:]
		h = _h(_h(_h(_h(NOISE_NAME, props), e_pub), b"\x01"), trezor_e)
		ck, k = _hkdf(NOISE_NAME, _x25519(ephemeral, trezor_e))
		trezor_s = self._unseal(k, 0, enc_static, h)  # the device's static key, masked per handshake
		h = _h(h, enc_static)
		ck, k = _hkdf(ck, _x25519(ephemeral, trezor_s))
		self._unseal(k, 0, tag, h)
		h = _h(h, tag)
		known = next((c for c in _load_credentials(credentials) if _x25519(_h(c["trezor"], trezor_e), c["trezor"]) == trezor_s), None)
		self.host_key = known["host"] if known else secrets.token_bytes(32)
		enc_host = self._seal(k, 1, _x25519_public(self.host_key), h)
		h = _h(h, enc_host)
		ck, k = _hkdf(ck, _x25519(self.host_key, trezor_e))
		enc_payload = self._seal(k, 0, pb((1, known["credential"] if known else None)), h)
		self.handshake_hash = _h(h, enc_payload)
		self._send(THP_HS_COMPLETE, enc_host + enc_payload)
		ctrl, data = self._receive()
		if ctrl & 0xE7 != THP_HS_COMPLETE + 1:
			raise DeviceGone(self.path, f"THP: bad handshake completion {ctrl:#04x}")
		self.k_req, self.k_resp = _hkdf(ck, b"")
		state = self._unseal(self.k_resp, 0, data, b"")
		self.n_req, self.n_resp = 0, 1
		return state[0]

	def send(self, session, mtype, payload):
		sealed = self._seal(self.k_req, self.n_req, struct.pack(">BH", session, mtype) + payload, b"")
		self.n_req += 1
		self._send(THP_ENCRYPTED, sealed)

	def receive(self, timeout_ms=READ_TIMEOUT_MS):
		ctrl, data = self._receive(timeout_ms)
		if ctrl & 0xE7 != THP_ENCRYPTED:
			raise DeviceGone(self.path, f"THP: unexpected message {ctrl:#04x}")
		plain = self._unseal(self.k_resp, self.n_resp, data, b"")
		self.n_resp += 1
		return plain[0], struct.unpack(">H", plain[1:3])[0], plain[3:]

	def call(self, mtype, payload, expect):
		"""A request on session 0 while setting up; button screens wait for the user."""
		self.send(0, mtype, payload)
		while True:
			_, rtype, rdata = self.receive()
			if rtype == MT_BUTTON_REQUEST:
				self.send(0, MT_BUTTON_ACK, b"")
			elif rtype == expect:
				return unpb(rdata)
			else:  # a Failure: the user declined, a wrong code, ...
				raise ThpRefused(f"the Trezor answered {rtype} to {mtype}", v1_frame(rtype, rdata))

	def _pair(self, methods, ask_code, credentials):
		"""Code Entry pairing (CPace on the 6-digit code), then a credential for later connections."""
		if CODE_ENTRY not in methods:
			raise ThpRefused(f"this Trezor offers no pairing method pytrezord knows (offers {methods})")
		if ask_code is None:
			raise ThpRefused("this Trezor is not paired with pytrezord yet: run pytrezord in a terminal once to pair")
		log(f"device {self.path}: pairing - allow it on the Trezor, then type the code it shows")
		self.call(MT_PAIRING_REQUEST, pb((1, socket.gethostname()), (2, "pytrezord")), MT_PAIRING_APPROVED)
		commitment = self.call(MT_SELECT_METHOD, pb((1, CODE_ENTRY)), MT_CODE_COMMITMENT)[1][0]
		challenge = secrets.token_bytes(16)
		trezor_cpace = self.call(MT_CODE_CHALLENGE, pb((1, challenge)), MT_CODE_CPACE_TREZOR)[1][0]
		code = ask_code(self).strip()
		if not (len(code) == 6 and code.isdigit()):
			raise ThpRefused("the pairing code has 6 digits")
		private = secrets.token_bytes(32)
		host_cpace = _x25519(private, _cpace_generator(code, self.handshake_hash))
		tag = _h(_x25519(private, trezor_cpace))
		secret = self.call(MT_CODE_CPACE_HOST, pb((1, host_cpace), (2, tag)), MT_CODE_SECRET)[1][0]
		expected = int.from_bytes(_h(bytes([CODE_ENTRY]), self.handshake_hash, secret, challenge), "big") % 1000000
		if _h(secret) != commitment or f"{expected:06}" != code:
			raise ThpRefused("pairing check failed: the Trezor's secret does not match the code")
		reply = self.call(MT_CREDENTIAL_REQUEST, pb((1, _x25519_public(self.host_key))), MT_CREDENTIAL_RESPONSE)
		_save_credential(credentials, {"trezor": reply[1][0], "host": self.host_key, "credential": reply[2][0]})
		log(f"device {self.path}: paired, credential saved in {credentials}")


class ThpLink:
	"""Serves a v1 client over a THP channel. THP has neither Initialize nor a passphrase prompt:
	Initialize becomes GetFeatures, and the first request that needs the seed (the seedless
	session answers it with InvalidSession) gets a seeded session made for it - with the
	passphrase asked from the client by a PassphraseRequest, as a v1 Trezor would."""

	def __init__(self, transport, path, ask_code, credentials):
		self.args = (transport, path, ask_code, credentials)
		self.channel = None
		self.session, self.next_session = 0, 1
		self.last = self.pending = None  # last seedless request / the one waiting for a seeded session
		self.creating = False            # ThpCreateNewSession in flight
		self.outbox = []                 # v1 frames pytrezord answers itself

	def write(self, frame):
		mtype, payload = struct.unpack(">H", frame[:2])[0], frame[6:]
		if self.channel is None:
			try:
				self.channel = ThpChannel(*self.args)
			except ThpRefused as e:
				log(f"device {self.args[1]}: {e}")
				self.outbox.append(e.frame)
				return
		if mtype == MT_INITIALIZE:  # v1: start over, a new passphrase on the next seeded request
			self.session, self.pending, self.creating = 0, None, False
			mtype, payload = MT_GET_FEATURES, b""
		elif mtype == MT_PASSPHRASE_ACK and self.pending and not self.creating:
			ack = unpb(payload)
			on_device = bool(ack.get(3, [0])[0])
			return self._create_session(None if on_device else ack.get(1, [b""])[0].decode(), on_device)
		elif not self.creating:
			self.pending = None  # the client did not answer our PassphraseRequest
		if self.session == 0 and mtype != MT_BUTTON_ACK:  # the request, not the screens it went through (PIN)
			self.last = (mtype, payload)
		self.channel.send(self.session, mtype, payload)

	def read(self):
		while not self.outbox:
			_, mtype, payload = self.channel.receive()
			if self.creating:
				self.creating = mtype not in (MT_SUCCESS, MT_FAILURE)
				if mtype == MT_SUCCESS:  # the seeded session exists: now the request that needed it
					self.channel.send(self.session, *self.pending)
					self.pending = None
					continue
				if mtype == MT_FAILURE:
					self.session, self.pending = 0, None
			elif self.session == 0 and self.last and mtype == MT_FAILURE and unpb(payload).get(1) == [FAILURE_INVALID_SESSION]:
				self.pending = self.last
				features = self._features()
				if features.get(36) == [1]:  # passphrase_always_on_device
					self._create_session(None, True)
				elif features.get(8) == [1]:  # passphrase_protection: the client decides, as with v1
					self.outbox.append(v1_frame(MT_PASSPHRASE_REQUEST, b""))
				else:
					self._create_session("", False)
				continue
			return v1_frame(mtype, payload)
		return self.outbox.pop(0)

	def _features(self):
		self.channel.send(0, MT_GET_FEATURES, b"")
		_, mtype, payload = self.channel.receive()
		return unpb(payload) if mtype == MT_FEATURES else {}

	def _create_session(self, passphrase, on_device):
		self.session, self.next_session = self.next_session, self.next_session % 255 + 1
		self.creating = True
		self.channel.send(self.session, MT_CREATE_SESSION, pb((1, passphrase), (2, on_device or None)))


# ---- sessions ---------------------------------------------------------------------
class Bridge:
	"""Session bookkeeping exactly like trezord: one session owns a device, an acquire
	with the right previous session steals it (that is how clients recover)."""

	def __init__(self, transport, ask_code=None, credentials=CREDENTIALS):
		self.transport = transport
		self._lock = threading.Lock()
		self._counter = 0
		self._sessions = {}     # session -> path
		self._by_path = {}      # path -> session
		self._device_locks = {} # path -> Lock, serializes USB i/o per device
		self._thp = {}          # path -> ThpLink; outlives bridge sessions, so reconnects need no confirming
		self._last = {}         # path -> last v1 frame, replayed over THP if the device refuses v1
		self._thp_args = (ask_code, credentials)

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

	def _drop(self, path, error):
		"""Forget a device that failed mid-action. Its session is dead from here on and the client
		has to acquire anew, so say why - the client only gets a generic 400 for it."""
		with self._lock:
			session = self._by_path.pop(path, None)
			self._sessions.pop(session, None)
			self._thp.pop(path, None)
			self._last.pop(path, None)
			self.transport.close(path)
		log(f"device {path} dropped, session {session} is gone: {error.reason}")

	def _write(self, path, message):
		if path in self._thp:
			return self._thp[path].write(message)
		self._last[path] = message
		self.transport.write(path, message)

	def _read(self, path):
		if path in self._thp:
			return self._thp[path].read()
		reply = self.transport.read(path)
		if reply == INVALID_PROTOCOL and path in self._last:  # Safe 7 and newer: THP only
			log(f"device {path} speaks THP, opening an encrypted channel")
			link = self._thp[path] = ThpLink(self.transport, path, *self._thp_args)
			link.write(self._last.pop(path))
			return link.read()
		return reply

	def call(self, session, message):
		path, lock = self._path_for(session)
		with lock:
			try:
				self._write(path, message)
				return self._read(path)
			except DeviceGone as e:
				self._drop(path, e)
				raise

	def post(self, session, message):
		path, lock = self._path_for(session)
		with lock:
			try:
				self._write(path, message)
			except DeviceGone as e:
				self._drop(path, e)
				raise

	def read(self, session):
		path, lock = self._path_for(session)
		with lock:
			try:
				return self._read(path)
			except DeviceGone as e:
				self._drop(path, e)
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
	interactive = sys.stdin is not None and sys.stdin.isatty()  # pairing a THP Trezor needs a typed code
	Handler.bridge = Bridge(transport or UsbTransport(), ask_pairing_code if interactive else None)
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
