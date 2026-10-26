import Foundation
import Security

struct Server: Codable, Equatable {
    enum Transport: String, Codable, CaseIterable { case tailscale, onion }

    var transport = Transport.tailscale
    var host = "" // the computer's Tailscale IP or MagicDNS name, entered in Settings
    var port = 37128
    var user = ""
    var password = ""
    var onion = "" // xyz...onion, served by the C tor on the computer
    var onionKey = "" // the x25519 client-auth private key, base32 as in C tor's .auth_private

    // Tailscale CGNAT range 100.64.0.0/10 or a MagicDNS name; anything else would send the login unencrypted.
    var isTailscale: Bool {
        let host = host.lowercased()
        if host.hasSuffix(".ts.net") { return true }
        #if targetEnvironment(simulator)
        if host == "127.0.0.1" { return true } // the simulator shares the Mac's loopback
        #endif
        let octets = host.split(separator: ".").compactMap { UInt8($0) }
        return octets.count == 4 && host.split(separator: ".").count == 4 && octets[0] == 100 && (64...127).contains(octets[1])
    }

    // A v3 onion is 56 base32 characters plus ".onion"; Tor itself then encrypts and authenticates the link.
    var isOnion: Bool { onion.lowercased().wholeMatch(of: /[a-z2-7]{56}\.onion/) != nil }

    var isAllowed: Bool { transport == .onion ? isOnion : isTailscale }

    // C tor's ClientOnionAuthDir line, which is what Arti parses; accepts a pasted full line in the key field too.
    var authLine: String {
        let key = onionKey.split(separator: ":").last.map(String.init) ?? ""
        return "\(onion.lowercased().replacing(".onion", with: "")):descriptor:x25519:\(key.trimmingCharacters(in: .whitespacesAndNewlines))"
    }
}

// In an extension so the memberwise init survives. Older Keychain blobs predate the Tor fields.
extension Server {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        transport = try c.decodeIfPresent(Transport.self, forKey: .transport) ?? .tailscale
        host = try c.decode(String.self, forKey: .host)
        port = try c.decode(Int.self, forKey: .port)
        user = try c.decode(String.self, forKey: .user)
        password = try c.decode(String.self, forKey: .password)
        onion = try c.decodeIfPresent(String.self, forKey: .onion) ?? ""
        onionKey = try c.decodeIfPresent(String.self, forKey: .onionKey) ?? ""
    }
}

struct RPCError: LocalizedError {
    let errorDescription: String?
    var transport = false // the link failed (Tor circuit, timeout), not Wasabi; the app keeps showing its last data
    init(_ message: String, transport: Bool = false) {
        errorDescription = message
        self.transport = transport
    }

    static func isTransport(_ error: any Error) -> Bool { error is URLError || (error as? RPCError)?.transport == true }
    // Wasabi answers "'X' wallet requires the passphrase..." or "Invalid password" when the wallet is not logged in.
    var needsPassphrase: Bool {
        guard let text = errorDescription, !text.hasPrefix("Wrong RPC user") else { return false }
        return text.localizedCaseInsensitiveContains("passphrase") || text.localizedCaseInsensitiveContains("password")
    }
}

struct WasabiRPC {
    let server: Server

    // The same RPC credentials can also stop Wasabi, dump keys or run scripts, so this app refuses anything not on this list.
    // listcoins: read-only, every coin incl. spent ones with spentBy (public keys and addresses only, like listunspentcoins).
    static let allowed: Set<String> = [
        "getstatus", "listwallets", "getwalletinfo", "listunspentcoins", "listcoins", "gethistory", "getfeerates", "listpaymentsincoinjoin",
        "loadwallet", "getnewaddress", "build", "send", "payincoinjoin", "cancelpaymentincoinjoin",
        "startcoinjoin", "startcoinjoinsweep", "stopcoinjoin", "excludefromcoinjoin",
    ]

    private static let reads: Set<String> = ["getstatus", "listwallets", "getwalletinfo", "listunspentcoins", "listcoins", "gethistory", "getfeerates", "listpaymentsincoinjoin"]

    // params: positional array or named dictionary, as the Wasabi method expects (sabi.py uses both).
    func call<T: Decodable & Sendable>(_ method: String, params: any Sendable = [String](), wallet: String? = nil, as _: T.Type = T.self) async throws -> T {
        let envelope = try await post(method, params: params, wallet: wallet, as: Envelope<T>.self)
        guard let result = envelope.result else { throw RPCError("Empty response for \(method)") }
        return result
    }

    // For methods that return nothing (startcoinjoin, stopcoinjoin, excludefromcoinjoin, ...).
    func call(_ method: String, params: any Sendable = [String](), wallet: String? = nil) async throws {
        _ = try await post(method, params: params, wallet: wallet, as: Envelope<Nothing>.self)
    }

    private func post<E: Envelopeish>(_ method: String, params: any Sendable, wallet: String?, as _: E.Type) async throws -> E {
        guard Self.allowed.contains(method) else { throw RPCError("\(method) is not allowed from this app") }
        guard server.isAllowed else { throw RPCError("Only Tailscale addresses (100.64.0.0/10 or *.ts.net) or a Tor onion are allowed: the RPC login can move funds and plain HTTP is only safe inside WireGuard or Tor") }
        let body = try JSONSerialization.data(withJSONObject: ["jsonrpc": "2.0", "id": "1", "method": method, "params": params])
        let credentials = Data("\(server.user):\(server.password)".utf8).base64EncodedString()

        let data: Data
        let statusCode: Int
        if server.transport == .onion {
            // Wasabi's HttpListener only answers its own prefix, so Host names the loopback RPC port, not the onion.
            (statusCode, data) = try await Tor.post(authLine: server.authLine, path: "/" + (wallet ?? ""), host: "127.0.0.1:\(server.port)", authorization: credentials, body: body)
        } else {
            guard var url = URL(string: "http://\(server.host):\(server.port)/") else { throw RPCError("Invalid host") }
            if let wallet { url.append(path: wallet) }
            // Reads are instant on a working link, so fail fast; build/send/coinjoin calls can take a while.
            var request = URLRequest(url: url, timeoutInterval: Self.reads.contains(method) ? 10 : 60)
            request.httpMethod = "POST"
            request.setValue("Basic \(credentials)", forHTTPHeaderField: "Authorization")
            request.httpBody = body
            let response: URLResponse
            (data, response) = try await URLSession.shared.data(for: request)
            statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        }
        if statusCode == 401 { throw RPCError("Wrong RPC user or password") }
        if statusCode == 404 { throw RPCError("Wasabi answered 404: wrong wallet name or RPC port") }
        let envelope = try JSONDecoder().decode(E.self, from: data)
        if let error = envelope.error { throw RPCError(error.message) }
        return envelope
    }

    struct Nothing: Decodable {}
    private struct Envelope<R: Decodable>: Envelopeish {
        let result: R?
        let error: Message?
    }
}

private protocol Envelopeish: Decodable {
    var error: Message? { get }
}
private struct Message: Decodable { let message: String }

// Response shapes from WalletWasabi.Client/Rpc/WasabiJsonRpcService.cs. Amounts are satoshis.
struct Status: Decodable {
    let torStatus: String
    let bestBlockchainHeight: String
    let filtersLeft: Int
    let network: String
    let exchangeRate: Double
    let peers: [Peer]
    struct Peer: Decodable { let isConnected: Bool }
}

struct WalletName: Decodable { let walletName: String }

struct WalletInfo: Decodable {
    let loaded: Bool
    let balance: Int?
    let coinjoinStatus: String?
    let anonScoreTarget: Int
    let isWatchOnly: Bool
    let isHardwareWallet: Bool
    let isAutoCoinjoin: Bool? // the desktop's own "Automatically start coinjoin"; when on, Wasabi starts coinjoins itself
    let coinjoinSignedByDevice: Bool? // preview builds: a Trezor signs the rounds, so payincoinjoin is impossible
    var deviceSigns: Bool { coinjoinSignedByDevice ?? isHardwareWallet }
    var coinjoining: Bool { coinjoinStatus.map { $0 != "Idle" } ?? false }
    // Desktop CoinJoinStateViewModel: hardware and watch-only wallets never auto-start; a device-signed one needs a tap on the device.
    var canAutoCoinjoin: Bool { !isHardwareWallet && !isWatchOnly }
}

// listunspentcoins; listcoins (every coin ever, with spentBy) has no label or excludedFromCoinjoin.
struct Coin: Decodable, Identifiable, Hashable {
    let txid: String
    let index: Int
    let amount: Int
    let anonymityScore: Double
    let confirmed: Bool
    let confirmations: Int?
    let label: String?
    let address: String
    let excludedFromCoinjoin: Bool?
    let spentBy: String?
    var id: String { "\(txid):\(index)" }
    var excluded: Bool { excludedFromCoinjoin == true }
}

struct HistoryItem: Decodable, Identifiable, Hashable {
    let datetime: Date
    let height: String
    let amount: Int
    let label: String?
    let tx: String
    let islikelycoinjoin: Bool
    var id: String { tx }

    // Wasabi 2.8 sends epoch seconds, older builds an ISO string.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let epoch = try? c.decode(Double.self, forKey: .datetime) {
            datetime = Date(timeIntervalSince1970: epoch)
        } else {
            datetime = ISO8601DateFormatter().date(from: try c.decode(String.self, forKey: .datetime)) ?? .distantPast
        }
        height = try c.decode(String.self, forKey: .height)
        amount = try c.decode(Int.self, forKey: .amount)
        label = try c.decodeIfPresent(String.self, forKey: .label)
        tx = try c.decode(String.self, forKey: .tx)
        islikelycoinjoin = try c.decode(Bool.self, forKey: .islikelycoinjoin)
    }
    private enum CodingKeys: CodingKey { case datetime, height, amount, label, tx, islikelycoinjoin }
}

struct NewAddress: Decodable { let address: String }

struct SendResult: Decodable { let txid: String }

// listpaymentsincoinjoin; state[0] is the newest state.
struct CoinjoinPayment: Decodable, Identifiable {
    let id: String
    let amount: Int
    let address: String?
    let state: [State]
    struct State: Decodable { let status: String }
    var status: String { state.first?.status ?? "?" }
}

// getfeerates: block target -> sat/vB, e.g. {"2": 1.064, "6": 0.5}.
typealias FeeRates = [String: Double]

enum Keychain {
    private static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: "server"] }

    static func load() -> Server? {
        var item: CFTypeRef?
        var q = query
        q[kSecReturnData as String] = true
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(Server.self, from: data)
    }

    static func save(_ server: Server) {
        SecItemDelete(query as CFDictionary)
        var q = query
        q[kSecValueData as String] = try? JSONEncoder().encode(server)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
}
