import CoreImage.CIFilterBuiltins
import CryptoKit
import UIKit

// Money helpers. Everything is integer satoshis; BTC strings never go through Double.
enum Bitcoin {
    // Which chain. getstatus.network is NBitcoin's name: Main, TestNet, TestNet4, Signet or RegTest.
    enum Network: Equatable {
        case main, test, regtest

        init(wasabi name: String) {
            switch name.lowercased() {
            case "main", "mainnet": self = .main
            case "regtest": self = .regtest
            default: self = .test // TestNet, TestNet4 and Signet share tb1, m, n and 2
            }
        }

        var name: String {
            switch self {
            case .main: "mainnet"
            case .test: "testnet"
            case .regtest: "regtest"
            }
        }
    }

    struct Payment: Equatable {
        var address: String
        var sats: Int?
        var label: String?
        var message: String?
    }

    // Why scanned or pasted text cannot be paid, in words the scanner shows as they are.
    enum ParseError: Error, Equatable, CustomStringConvertible {
        case notBitcoin
        case invalid // shaped like an address, but the checksum, length, padding or version is wrong
        case unsupported(version: Int) // a segwit version without spending rules yet
        case network(address: Network, wallet: Network)
        case amount
        case duplicate(String)
        case required(String)
        case offChain([String]) // only Lightning or silent-payment instructions, nothing on-chain
        case empty

        var description: String {
            switch self {
            case .notBitcoin: "Not a bitcoin address or payment link"
            case .invalid: "This looks like a bitcoin address, but it is not valid: a character is wrong or missing"
            case .unsupported(let v): "Segwit version \(v) addresses of this shape have no spending rules yet; coins sent there could be taken by anyone"
            case .network(let a, let w): "This is a \(a.name) address, but the wallet is on \(w.name)"
            case .amount: "The amount in this request is not a valid BTC amount"
            case .duplicate(let key): "This request repeats \(key), which is not allowed"
            case .required(let key): "This request requires \(key), which this app does not support"
            case .offChain(let keys): "Wasabi pays on-chain only; this request has no on-chain address, only " + keys.map(Self.name).formatted(.list(type: .and))
            case .empty: "This payment link has no address to pay"
            }
        }

        private static func name(_ key: String) -> String {
            switch key {
            case "lightning": "a Lightning invoice"
            case "lno": "a Lightning offer"
            case "sp": "a silent payment address"
            case "pay": "a BIP 351 private payment address"
            default: key
            }
        }
    }

    // "0.001" -> 100_000. Nil for anything that is not a plain decimal with at most 8 fraction digits.
    static func sats(btc: String) -> Int? {
        let text = btc.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        guard !text.isEmpty, parts.count <= 2, text.allSatisfy({ $0.isNumber || $0 == "." }) else { return nil }
        let whole = parts[0].isEmpty ? 0 : Int(parts[0])
        let fraction = parts.count == 2 ? parts[1] : ""
        guard let whole, whole <= 21_000_000, fraction.count <= 8, let frac = Int(fraction.padding(toLength: 8, withPad: "0", startingAt: 0)) else { return nil }
        return whole * 100_000_000 + frac
    }

    static func btc(_ sats: Int) -> String {
        let magnitude = abs(sats)
        return (sats < 0 ? "-" : "") + String(format: "%d.%08d", magnitude / 100_000_000, magnitude % 100_000_000)
    }

    // A plain address, or a BIP 321 (BIP 21) link. `bitcoin:` and the query keys are case-insensitive; the path may be empty
    // when a query parameter carries the payment instruction, and bech32/bech32m addresses may also come under their
    // human-readable part as the key (bc=, tb=). The first supported on-chain address wins, in the order given; Lightning and
    // silent-payment instructions are explained rather than silently dropped. pop is never contacted; req-pop, like any
    // req- key this app does not implement, makes the whole link invalid.
    static func parse(_ text: String, network wallet: Network? = nil) throws(ParseError) -> Payment {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.lowercased().hasPrefix("bitcoin:") else {
            return Payment(address: try address(text, network: wallet), sats: nil, label: nil, message: nil)
        }
        let body = text.dropFirst("bitcoin:".count)
        let path = String(body.prefix { $0 != "?" })
        var candidates = path.isEmpty ? [] : [path]
        var offChain: [String] = []
        var seen: Set<String> = []
        var payment = Payment(address: "", sats: nil, label: nil, message: nil)
        for item in body.drop(while: { $0 != "?" }).dropFirst().split(separator: "&") {
            let pair = item.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let key = pair[0].lowercased()
            let raw = pair.count == 2 ? String(pair[1]) : ""
            let value = raw.removingPercentEncoding ?? raw
            switch key {
            case "amount", "label", "message", "pop": // these MUST NOT repeat
                guard seen.insert(key).inserted else { throw .duplicate(key) }
                switch key {
                case "amount":
                    // Decimal BTC only: digits and one period; no sign, exponent or thousands separator.
                    guard raw.allSatisfy({ $0.isASCII && ($0.isNumber || $0 == ".") }), let amount = sats(btc: raw) else { throw .amount }
                    payment.sats = amount
                case "label": payment.label = value
                case "message": payment.message = value
                default: break // pop: proof of payment is not implemented and never contacted
                }
            case "bc", "tb", "bcrt": // an address keyed by its hrp must carry that hrp
                guard value.lowercased().hasPrefix(key + "1") else { throw .invalid }
                candidates.append(value)
            case "lightning", "lno", "sp", "pay":
                if !offChain.contains(key) { offChain.append(key) }
            case _ where key.hasPrefix("req-"):
                throw .required(String(key.dropFirst(4)))
            default:
                break // pj, pjos and anything else optional can be ignored; the on-chain address still gets paid
            }
        }
        var problem: ParseError?
        for candidate in candidates {
            do {
                payment.address = try address(candidate, network: wallet)
                return payment
            } catch ParseError.invalid {
                throw .invalid // a damaged address is never skipped over
            } catch {
                problem = problem ?? error // wrong network or a future version: the next candidate may do
            }
        }
        if let problem { throw problem }
        throw offChain.isEmpty ? .empty : .offChain(offChain)
    }

    // One entry point for "can Wasabi pay this": real checksums (base58check, BIP173 bech32, BIP350 bech32m), the shapes
    // Wasabi can pay (P2PKH, P2SH, P2WPKH, P2WSH, P2TR) and, when known, the wallet's network. Returns the address as it
    // should be sent: bech32 lower-cased, base58 untouched.
    static func address(_ s: String, network wallet: Network? = nil) throws(ParseError) -> String {
        let s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        let low = s.lowercased()
        let network: Network
        var legacy = false
        if let hrp = ["bc", "tb", "bcrt"].first(where: { low.hasPrefix($0 + "1") }) {
            guard let decoded = segwit(s) else { throw .invalid }
            // Spendable today: v0 (P2WPKH 20 bytes, P2WSH 32) and v1 P2TR (32). Other versions are anyone-can-spend until a soft fork.
            guard decoded.version == 0 || (decoded.version == 1 && decoded.program.count == 32) else { throw .unsupported(version: decoded.version) }
            network = hrp == "bc" ? .main : hrp == "tb" ? .test : .regtest
        } else if let first = s.first, "123mn".contains(first), (25...35).contains(s.count), s.allSatisfy(base58.contains) {
            // Version byte 0x00 P2PKH, 0x05 P2SH; 0x6f and 0xc4 on the test networks. The hash is always 20 bytes.
            guard let payload = base58check(s), payload.count == 21, [0x00, 0x05, 0x6f, 0xc4].contains(payload[0]) else { throw .invalid }
            network = payload[0] < 0x6f ? .main : .test
            legacy = true
        } else {
            throw .notBitcoin
        }
        // base58 test bytes are shared by testnet, signet and regtest; bech32 names its chain in the hrp.
        if let wallet, network != wallet, !(legacy && network == .test && wallet == .regtest) { throw .network(address: network, wallet: wallet) }
        return legacy ? s : low
    }

    static func isAddress(_ s: String, network: Network? = nil) -> Bool { (try? address(s, network: network)) != nil }

    private static let bech32Charset = Array("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
    private static let base58 = Array("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

    private static func polymod(_ values: [UInt8]) -> UInt32 {
        var chk: UInt32 = 1
        for v in values {
            let top = chk >> 25
            chk = (chk & 0x1ff_ffff) << 5 ^ UInt32(v)
            for (i, g) in [0x3b6a_57b2, 0x2650_8e6d, 0x1ea1_19fa, 0x3d42_33dd, 0x2a14_62b3].enumerated() where top >> i & 1 == 1 { chk ^= UInt32(g) }
        }
        return chk
    }

    // BIP173/BIP350: the human-readable part, the 5-bit data without its checksum, and whether the bech32m constant matched.
    // nil for anything that is not a well-formed string of either kind (upper and lower case together, bad characters, too long, wrong checksum).
    static func bech32(_ s: String) -> (hrp: String, data: [UInt8], m: Bool)? {
        guard s.count <= 90, s == s.lowercased() || s == s.uppercased() else { return nil }
        let low = s.lowercased()
        guard let separator = low.lastIndex(of: "1") else { return nil }
        let hrp = String(low[..<separator]), rest = low[low.index(after: separator)...]
        guard (1...83).contains(hrp.count), hrp.utf8.allSatisfy({ (33...126).contains($0) }), rest.count >= 6 else { return nil }
        let values = rest.compactMap { c in bech32Charset.firstIndex(of: c).map(UInt8.init) }
        guard values.count == rest.count else { return nil }
        let expanded = hrp.utf8.map { $0 >> 5 } + [0] + hrp.utf8.map { $0 & 31 }
        switch polymod(expanded + values) {
        case 1: return (hrp, Array(values.dropLast(6)), false)
        case 0x2bc8_30a3: return (hrp, Array(values.dropLast(6)), true)
        default: return nil
        }
    }

    // A segwit address: hrp, witness version and program, per BIP173 (v0, bech32) and BIP350 (v1+, bech32m). nil when invalid.
    static func segwit(_ s: String) -> (hrp: String, version: Int, program: [UInt8])? {
        guard let decoded = bech32(s), ["bc", "tb", "bcrt"].contains(decoded.hrp), let version = decoded.data.first, version <= 16, (version == 0) != decoded.m else { return nil }
        // 5-bit groups to bytes; the leftover must be fewer than 5 bits and all zero.
        var acc = 0, bits = 0
        var program: [UInt8] = []
        for v in decoded.data.dropFirst() {
            acc = acc << 5 | Int(v)
            bits += 5
            while bits >= 8 { bits -= 8; program.append(UInt8(acc >> bits & 0xff)) }
            acc &= (1 << bits) - 1
        }
        guard bits < 5, acc == 0, (2...40).contains(program.count), version != 0 || program.count == 20 || program.count == 32 else { return nil }
        return (decoded.hrp, Int(version), program)
    }

    // base58check payload (version byte and hash) when the double-SHA-256 checksum holds. nil otherwise.
    static func base58check(_ s: String) -> [UInt8]? {
        var bytes: [UInt8] = [] // big-endian
        for c in s {
            guard var carry = base58.firstIndex(of: c) else { return nil }
            for i in bytes.indices.reversed() {
                carry += 58 * Int(bytes[i])
                bytes[i] = UInt8(carry & 0xff)
                carry >>= 8
            }
            while carry > 0 { bytes.insert(UInt8(carry & 0xff), at: 0); carry >>= 8 }
        }
        bytes = Array(repeating: 0, count: s.prefix { $0 == "1" }.count) + bytes
        guard bytes.count > 4 else { return nil }
        let payload = Array(bytes.dropLast(4))
        let hash = SHA256.hash(data: Data(SHA256.hash(data: Data(payload))))
        return Array(hash.prefix(4)) == Array(bytes.suffix(4)) ? payload : nil
    }

    static func uri(address: String, sats: Int?, label: String) -> String {
        var query: [String] = []
        if let sats, sats > 0 { query.append("amount=" + btc(sats).replacing(/0+$/, with: "").replacing(/\.$/, with: "")) }
        if !label.isEmpty, let encoded = label.addingPercentEncoding(withAllowedCharacters: .alphanumerics) { query.append("label=" + encoded) }
        return "bitcoin:" + address + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
    }

    // Raw tx from `build`: which outpoints it spends and the output total. Fee = known inputs - outputs.
    static func inspect(hex: String) -> (inputs: [String], outputs: Int)? {
        let bytes = hex.chunks(of: 2).compactMap { UInt8($0, radix: 16) }
        guard hex.count % 2 == 0, bytes.count * 2 == hex.count, bytes.count > 4 else { return nil }
        var p = 4
        var inputs: [String] = []
        var outputs = 0
        func varint() -> Int? {
            guard p < bytes.count else { return nil }
            let first = bytes[p]; p += 1
            let width = first < 0xfd ? 0 : first == 0xfd ? 2 : first == 0xfe ? 4 : 8
            guard width > 0 else { return Int(first) }
            guard p + width <= bytes.count else { return nil }
            defer { p += width }
            return (0..<width).reduce(0) { $0 | Int(bytes[p + $1]) << (8 * $1) }
        }
        if bytes.count > 6, bytes[4] == 0, bytes[5] == 1 { p += 2 } // segwit marker + flag
        guard let vin = varint() else { return nil }
        for _ in 0..<vin {
            guard p + 36 <= bytes.count else { return nil }
            let txid = bytes[p..<p + 32].reversed().map { String(format: "%02x", $0) }.joined()
            let index = (0..<4).reduce(0) { $0 | Int(bytes[p + 32 + $1]) << (8 * $1) }
            inputs.append("\(txid):\(index)")
            p += 36
            guard let scriptLength = varint() else { return nil }
            p += scriptLength + 4
        }
        guard let vout = varint() else { return nil }
        for _ in 0..<vout {
            guard p + 8 <= bytes.count else { return nil }
            outputs += (0..<8).reduce(0) { $0 | Int(bytes[p + $1]) << (8 * $1) }
            p += 8
            guard let scriptLength = varint() else { return nil }
            p += scriptLength
        }
        return (inputs, outputs)
    }

    // sabi.py pick_coins(): confirmed coins, private first, then semi, then non-private, largest first. `onlyPrivate` (the
    // "More private" stop) never reaches past the private group, so the caller sees a shortfall instead of a toxic merge.
    static func pickCoins(from coins: [Coin], target: Int, need: Int, onlyPrivate: Bool = false) -> (coins: [Coin], toxic: Bool) {
        let target = Double(target)
        let spendable = coins.filter(\.confirmed)
        var groups = [spendable.filter { $0.anonymityScore >= target }]
        if !onlyPrivate { groups += [spendable.filter { 1 < $0.anonymityScore && $0.anonymityScore < target }, spendable.filter { $0.anonymityScore <= 1 }] }
        var picked: [Coin] = []
        var total = 0
        for group in groups {
            for coin in group.sorted(by: { $0.amount > $1.amount }) where total < need {
                picked.append(coin); total += coin.amount
            }
            if total >= need { break }
        }
        let toxic = picked.contains { $0.anonymityScore >= target } && picked.contains { $0.anonymityScore <= 1 }
        return (picked, toxic)
    }

    // Own outputs of the same transaction with the same value as `coin`, itself included: what the wallet can count of
    // the coin's anonymity set without the full transaction (no Wasabi RPC returns one).
    static func ownEqualOutputs(of coin: Coin, among coins: [Coin]) -> [Coin] {
        coins.filter { $0.txid == coin.txid && $0.amount == coin.amount }.sorted { $0.index < $1.index }
    }

    static func qr(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let image = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 12, y: 12)),
              let cg = CIContext().createCGImage(image, from: image.extent) else { return nil }
        return UIImage(cgImage: cg)
    }

    #if DEBUG
    // Money logic gets one runnable check; it runs once at app launch in debug builds.
    static func selfCheck() {
        assert(sats(btc: "0.001") == 100_000)
        func coin(_ sats: Int, _ score: Double, tx: String = "", index: Int = 0, confirmed: Bool = true) -> Coin {
            Coin(txid: tx, index: index, amount: sats, anonymityScore: score, confirmed: confirmed, confirmations: nil, label: nil, address: "", excludedFromCoinjoin: false, spentBy: nil)
        }
        let wallet = [coin(500, 1), coin(300, 10), coin(200, 10), coin(100, 4), coin(900, 10, confirmed: false)]
        var pick = pickCoins(from: wallet, target: 10, need: 450)
        assert(pick.coins.map(\.amount) == [300, 200] && !pick.toxic) // private coins first, largest first, unconfirmed never
        pick = pickCoins(from: wallet, target: 10, need: 550)
        assert(pick.coins.map(\.amount) == [300, 200, 100] && !pick.toxic) // semi-private next, still no toxic merge
        pick = pickCoins(from: wallet, target: 10, need: 700)
        assert(pick.coins.map(\.amount) == [300, 200, 100, 500] && pick.toxic) // reaching a non-private coin flags the merge
        pick = pickCoins(from: wallet, target: 10, need: 700, onlyPrivate: true)
        assert(pick.coins.map(\.amount) == [300, 200] && !pick.toxic && pick.coins.reduce(0) { $0 + $1.amount } < 700) // short, never toxic
        let siblings = [coin(100, 5, tx: "a", index: 2), coin(100, 5, tx: "a", index: 0), coin(100, 5, tx: "b"), coin(200, 5, tx: "a", index: 1)]
        assert(ownEqualOutputs(of: siblings[0], among: siblings).map(\.index) == [0, 2]) // same tx and value only, in output order
        assert(Mirror.privacyPercentage([coin(100, 50)], target: 50) == 100)
        assert(Mirror.privacyPercentage([coin(100, 1)], target: 50) == 0)
        assert(Mirror.privacyPercentage([coin(100, 25.5)], target: 50) == 50) // semi-private counts partly, as in Wasabi
        assert(Mirror.privacyPercentage([coin(100, 50), coin(100, 1)], target: 50) == 50)
        assert(Server(host: "100.101.102.103").isTailscale && Server(host: "mac.example.ts.net").isTailscale)
        assert(!Server(host: "192.168.1.20").isTailscale && !Server(host: "100.128.0.1").isTailscale && !Server(host: "100.64.1").isTailscale)
        let onion = String(repeating: "a", count: 56) + ".onion"
        assert(Server(transport: .onion, onion: onion, onionKey: "KEY").isAllowed && Server(transport: .onion, onion: onion, onionKey: "x:descriptor:x25519:KEY\n").authLine == String(repeating: "a", count: 56) + ":descriptor:x25519:KEY")
        assert(!Server(transport: .onion, onion: "100.101.102.103").isAllowed && !Server(transport: .tailscale, host: onion).isAllowed) // .onion only over Tor, Tailscale only over Tailscale
        assert(sats(btc: "1") == 100_000_000)
        assert(sats(btc: "0.00000001") == 1)
        assert(sats(btc: "99999999999") == nil) // above 21M BTC: rejected instead of overflowing
        assert(sats(btc: "0.1") == 10_000_000)
        assert(sats(btc: "21000000.00000001") == 2_100_000_000_000_001)
        assert(sats(btc: "0.000000001") == nil) // too many decimals
        assert(sats(btc: "abc") == nil && sats(btc: "") == nil && sats(btc: "1.2.3") == nil)
        assert(btc(123_456_789) == "1.23456789" && btc(-1) == "-0.00000001")

        func bytes(_ hex: String) -> [UInt8] { hex.chunks(of: 2).compactMap { UInt8($0, radix: 16) } }
        func fails(_ text: String, _ expected: ParseError, network: Network? = nil) -> Bool {
            do { _ = try parse(text, network: network); return false } catch { return error == expected }
        }
        func seg(_ s: String, _ hrp: String, _ version: Int, _ program: String) -> Bool {
            guard let d = segwit(s) else { return false }
            return d.hrp == hrp && d.version == version && d.program == bytes(program)
        }
        let v0 = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", v1 = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
        let v2 = "bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", tb = "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7"
        let legacy = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"

        // BIP21 as this app writes and reads it.
        let uri = try? parse("BITCOIN:BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4?amount=0.00123&label=Caf%C3%A9&foo=bar")
        assert(uri == Payment(address: v0, sats: 123_000, label: "Café", message: nil))
        assert((try? parse(" \(legacy) ")) == Payment(address: legacy, sats: nil, label: nil, message: nil))
        assert(fails("hello", .notBitcoin) && fails("", .notBitcoin))
        assert(uri == (try? parse(self.uri(address: v0, sats: 123_000, label: "Café"))))

        // BIP 321 test vectors. The spec's own example addresses (175tWpb8K1S7NmH4Zx6rewF9WQrcZv245W and its bc1/tb1 strings) fail
        // their checksums, so the structure is checked with real addresses of the same types and the literal vector must fail.
        assert(fails("bitcoin:175tWpb8K1S7NmH4Zx6rewF9WQrcZv245W", .invalid))
        assert((try? parse("bitcoin:\(legacy)?label=Luke-Jr")) == Payment(address: legacy, sats: nil, label: "Luke-Jr", message: nil))
        assert((try? parse("bitcoin:\(legacy)?amount=20.3&label=Luke-Jr"))?.sats == 2_030_000_000)
        assert((try? parse("bitcoin:\(legacy)?amount=50&label=Luke-Jr&message=Donation%20for%20project%20xyz")) == Payment(address: legacy, sats: 5_000_000_000, label: "Luke-Jr", message: "Donation for project xyz"))
        assert((try? parse("bitcoin:\(legacy)?lightning=lnbc420bogusinvoice"))?.address == legacy) // the on-chain fallback is what gets paid
        assert((try? parse("bitcoin:\(legacy)?sp=sp1qsilentpayment"))?.address == legacy)
        assert(fails("bitcoin:?lightning=lnbc420bogusinvoice", .offChain(["lightning"])))
        assert(fails("bitcoin:?lno=lno1bogusoffer", .offChain(["lno"])))
        assert(fails("bitcoin:?lno=lno1bogusoffer&sp=sp1qsilentpayment", .offChain(["lno", "sp"])))
        assert(fails("bitcoin:?sp=sp1qsilentpayment", .offChain(["sp"])))
        assert(fails("bitcoin:\(legacy)?req-somethingyoudontunderstand=50&req-somethingelseyoudontget=999", .required("somethingyoudontunderstand")))
        assert((try? parse("bitcoin:\(legacy)?somethingyoudontunderstand=50&somethingelseyoudontget=999"))?.address == legacy)
        assert((try? parse("bitcoin:?bc=\(v0)&bc=\(v1)"))?.address == v0) // the first supported on-chain address
        assert((try? parse("BITCOIN:\(v0.uppercased())?BC=\(v1.uppercased())"))?.address == v0)
        assert((try? parse("BITCOIN:?BC=\(v0.uppercased())&BC=\(v1.uppercased())"))?.address == v0)
        assert((try? parse("bitcoin:?tb=\(tb)", network: .test))?.address == tb)
        assert(fails("bitcoin:?tb=\(tb)", .network(address: .test, wallet: .main), network: .main))
        assert(fails("bitcoin:\(legacy)?label=Luke-Jr&label=Matt", .duplicate("label")))
        assert(fails("bitcoin:\(legacy)?amount=42&amount=10", .duplicate("amount")))
        assert(fails("bitcoin:\(legacy)?amount=42&amount=42", .duplicate("amount")))
        assert(fails("bitcoin:\(legacy)?pop=callback%3a&req-pop=callback%3a", .required("pop")))
        assert(fails("bitcoin:?bc=\(tb)", .invalid)) // a testnet address under the bc key
        assert((try? parse("bitcoin:\(legacy)?pop=callback%3a"))?.address == legacy) // optional pop: paid, never contacted
        assert(fails("bitcoin:", .empty) && fails("bitcoin:?foo=bar", .empty))
        assert((try? parse("bitcoin:?bc=\(v2)&bc=\(v0)"))?.address == v0) // a future version is skipped for one Wasabi can pay
        assert(fails(v2, .unsupported(version: 2)) && fails("bitcoin:?bc=\(v2)", .unsupported(version: 2)))
        assert((try? parse("bitcoin:\(v0)?pj=https%3A%2F%2Fexample.com&pjos=0"))?.address == v0) // payjoin is optional; the plain address is paid
        assert(fails("bitcoin:\(legacy)?amount=50,000.00", .amount) && fails("bitcoin:\(legacy)?amount=-1", .amount) && fails("bitcoin:\(legacy)?amount=1e3", .amount))
        assert(fails("bitcoin:\(legacy)?amount=0.000000001", .amount) && fails("bitcoin:\(legacy)?amount=21000001", .amount) && fails("bitcoin:\(legacy)?amount=", .amount))
        assert((try? parse("bitcoin:\(legacy)?amount=.5"))?.sats == 50_000_000 && (try? parse("bitcoin:\(legacy)?amount=5."))?.sats == 500_000_000)

        // BIP173 / BIP350 segwit address vectors.
        assert(seg("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "bc", 0, "751e76e8199196d454941c45d1b3a323f1433bd6"))
        assert(seg(tb, "tb", 0, "1863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262"))
        assert(seg("bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y", "bc", 1, "751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6"))
        assert(seg("BC1SW50QGDZ25J", "bc", 16, "751e"))
        assert(seg(v2, "bc", 2, "751e76e8199196d454941c45d1b3a323"))
        assert(seg("tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy", "tb", 0, "000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"))
        assert(seg("tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c", "tb", 1, "000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"))
        assert(seg(v1, "bc", 1, "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"))
        for bad in [
            "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut", // human-readable part
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd", // bech32 for v1
            "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf",
            "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",
            "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh", // bech32m for v0
            "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",
            "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4", // character in checksum
            "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R", // witness version 17
            "bc1pw5dgrnzv", // 1-byte program
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav", // 41-byte program
            "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P", // v0 with a program that is neither 20 nor 32 bytes
            "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq", // upper and lower case together
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf", // zero padding of more than 4 bits
            "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j", // non-zero padding
            "bc1gmk9yu", // empty data
            "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", // BIP173: checksum
            "bc1rw5uspcuh", "bc10w508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kw5rljs90", // BIP173: program length
        ] { assert(segwit(bad) == nil, bad) }
        // Raw bech32 and bech32m strings from the same specs.
        for good in ["A12UEL5L", "a12uel5l", "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw", "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w", "?1ezyfcl",
                     "an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs"] { assert(bech32(good)?.m == false, good) }
        for good in ["A1LQFN3A", "a1lqfn3a", "abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx", "split1checkupstagehandshakeupstreamerranterredcaperredlc445v", "?1v759aa",
                     "an83characterlonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11sg7hg6"] { assert(bech32(good)?.m == true, good) }
        for bad in ["\u{20}1nwldj5", "\u{7F}1axkwrx", "\u{80}1eym55h", "an84characterslonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1569pvx",
                    "pzry9x0s0muk", "1pzry9x0s0muk", "x1b4n0q5v", "li1dgmt3", "de1lg7wt\u{FF}", "A1G7SGD8", "10a06t8", "1qzzfhee",
                    "qyrz8wqd2c9m", "1qyrz8wqd2c9m", "y1b0jsk6g", "lt1igcx5c0", "in1muywd", "mm1crxm3i", "au1s5cgom", "M1VUXWEZ", "16plkw9", "1p2gdwpf"] { assert(bech32(bad) == nil, bad) }

        // base58check: double SHA-256 over version byte and hash; one wrong character fails. Test bytes serve signet and regtest too.
        assert(isAddress(legacy) && isAddress("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy") && isAddress("1111111111111111111114oLvT2"))
        assert(isAddress("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", network: .test) && isAddress("2MzQwSSnBHWHqSAqtTVQ6v47XtaisrJa1Vc", network: .regtest))
        assert(fails("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3", .invalid) && fails("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLz", .invalid))
        assert(fails(legacy, .network(address: .main, wallet: .test), network: .test))
        assert(fails("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn", .network(address: .test, wallet: .main), network: .main))
        assert(fails(tb, .network(address: .test, wallet: .regtest), network: .regtest)) // bech32 names its chain
        let bcrt = "bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"
        assert(isAddress(bcrt, network: .regtest) && fails(bcrt, .network(address: .regtest, wallet: .main), network: .main))
        assert(Network(wasabi: "Main") == .main && Network(wasabi: "TestNet4") == .test && Network(wasabi: "Signet") == .test && Network(wasabi: "RegTest") == .regtest)
        assert((try? address("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")) == v0) // bech32 is sent lower-case
        assert(fails(v0 + "x", .invalid) && fails("bc1", .invalid))

        // 1-in 2-out legacy tx skeleton: input 00..01:0, outputs 1000 + 2000 sats, empty scripts.
        let tx = "01000000" + "01" + String(repeating: "00", count: 31) + "01" + "00000000" + "00" + "ffffffff"
            + "02" + "e803000000000000" + "00" + "d007000000000000" + "00" + "00000000"
        let parsed = inspect(hex: tx)
        assert(parsed?.outputs == 3000 && parsed?.inputs == ["01" + String(repeating: "00", count: 31) + ":0"])
        assert(inspect(hex: "0100") == nil)
    }
    #endif
}

private extension String {
    func chunks(of size: Int) -> [String] {
        stride(from: 0, to: count, by: size).map { String(self[index(startIndex, offsetBy: $0)..<index(startIndex, offsetBy: $0 + size)]) }
    }
}
