import CryptoKit
import SwiftUI

@main
struct WasabiPreviewApp: App {
    init() {
        #if DEBUG
        Bitcoin.selfCheck()
        WasabiIcon.selfCheck()
        PrivacyStop.selfCheck()
        #endif
    }

    var body: some Scene {
        WindowGroup { RootView() }
    }
}

// Owns the theme. @AppStorage re-evaluates reliably in a View, not in the App struct, and dark mode goes through the
// windows: preferredColorScheme does not revert once set back to nil, overrideUserInterfaceStyle does, and sheets follow.
struct RootView: View {
    @AppStorage("theme") private var theme = AppTheme.wasabi

    var body: some View {
        ContentView()
            .environment(\.appTheme, theme)
            .onChange(of: theme, initial: true) {
                let style: UIUserInterfaceStyle = theme == .black ? .dark : .unspecified
                for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
                    scene.windows.forEach { $0.overrideUserInterfaceStyle = style }
                }
            }
    }
}

@MainActor @Observable
final class Mirror {
    var server = Keychain.load() ?? Server() {
        didSet { failingSince = nil; linkError = nil; status = nil; updated = nil }
    }
    var wallets: [String] = []
    var wallet: String? = UserDefaults.standard.string(forKey: "wallet") {
        didSet { UserDefaults.standard.set(wallet, forKey: "wallet"); loadPrivacyStop(); seenIncoming = nil; coinjoinHint = nil; allCoins = [] }
    }
    // The privacy slider's stop, per wallet; the default is the desktop's Default Strategy.
    var privacyStop = PrivacyStop.balanced {
        didSet {
            #if DEBUG
            if Screen.current != nil { return } // screenshot runs force a stop without leaving it behind in the simulator
            #endif
            if let wallet { UserDefaults.standard.set(privacyStop.rawValue, forKey: "privacy." + wallet) }
        }
    }
    var coinjoinHint: String? // why a coinjoin did not start by itself (wallet not logged in on the computer); shown quietly, never as a prompt
    var allCoins: [Coin] = [] // listcoins, fetched when a coin screen opens
    // Incoming txids already on screen, tagged with their wallet so a refresh that lands after a wallet switch can never
    // make the new wallet's history look "new" and start a coinjoin on it. nil until a wallet's first history.
    private var seenIncoming: (wallet: String, txs: Set<String>)?
    var discreet = UserDefaults.standard.bool(forKey: "discreet") {
        didSet { UserDefaults.standard.set(discreet, forKey: "discreet") }
    }
    var status: Status?
    var info: WalletInfo?
    var coins: [Coin] = []
    var history: [HistoryItem] = []
    var payments: [CoinjoinPayment] = []
    var fees: FeeRates = [:]
    var error: String?
    var walletError: String?
    var updated: Date?
    var notice: String?
    var passphrase: PassphraseRequest?
    var busy = false
    var torProgress: Int? // 0...100 while Arti bootstraps; nil on Tailscale or once ready
    var linkError: String? // last link failure (Tor circuit, timeout); the dashboard keeps showing the last good data
    var refreshing = false
    private var failingSince: Date?

    enum Link { case connecting, connected, failed }
    var link: Link {
        if linkError != nil { return .failed }
        return torProgress != nil || updated == nil ? .connecting : .connected
    }

    init() {
        #if DEBUG
        // Simulator runs: `SIMCTL_CHILD_WASABI_RPC_PASSWORD=... simctl launch ...` skips the Settings sheet.
        let env = ProcessInfo.processInfo.environment
        if let password = env["WASABI_RPC_PASSWORD"] {
            server = Server(host: env["WASABI_RPC_HOST"] ?? "127.0.0.1", user: env["WASABI_RPC_USER"] ?? "preview", password: password)
            // WASABI_ONION_KEY is a C tor .auth_private line: <onion>:descriptor:x25519:<key>.
            if let line = env["WASABI_ONION_KEY"], let onion = line.split(separator: ":").first {
                server.transport = .onion
                server.onion = onion + ".onion"
                server.onionKey = line
            }
        }
        if let flag = env["WASABI_DISCREET"] { discreet = flag == "1" }
        if Self.demo { loadDemo() }
        #endif
        loadPrivacyStop()
    }

    private func loadPrivacyStop() {
        #if DEBUG
        if let stop = Screen.current?.stop { privacyStop = stop; return }
        #endif
        privacyStop = wallet.flatMap { UserDefaults.standard.string(forKey: "privacy." + $0) }.flatMap(PrivacyStop.init) ?? .balanced
    }

    // A mutating call that needs the wallet passphrase; the typed passphrase only ever lives in the retry call.
    struct PassphraseRequest: Identifiable {
        let id = UUID()
        let title: String
        let action: @MainActor (String) async throws -> String?
    }

    var rpc: WasabiRPC { WasabiRPC(server: server) }
    var network: Bitcoin.Network? { status.map { Bitcoin.Network(wasabi: $0.network) } } // nil until getstatus answered
    var target: Int { info?.anonScoreTarget ?? 5 }
    var balance: Int { info?.balance ?? coins.reduce(0) { $0 + $1.amount } }
    var walletNotLoaded: Bool { walletError?.contains("no wallet loaded") == true }
    var ready: Bool { status != nil && error == nil && walletError == nil }

    // Wasabi's buckets (SmartCoinExtensions.cs): private at/above the anon target, semi-private from 2 (Constants.SemiPrivateThreshold).
    var privacy: (private: Int, semi: Int, nonPrivate: Int) {
        coins.reduce(into: (0, 0, 0)) { acc, c in
            if c.anonymityScore >= Double(target) { acc.0 += c.amount } else if c.anonymityScore >= 2 { acc.1 += c.amount } else { acc.2 += c.amount }
        }
    }

    var privacyPercentage: Int { Self.privacyPercentage(coins, target: target) }

    // Plain words for the home tile; Wasabi's own state names stay on the Privacy screen.
    var coinjoinState: (text: String, active: Bool) {
        switch info?.coinjoinStatus {
        case "In progress", "In critical phase": ("Making your coins private…", true)
        case "In schedule": ("Waiting for the next round", true)
        case nil: ("Unknown", false)
        default: (privacyPercentage >= 100 ? "All coins are private" : "Paused", false)
        }
    }

    // Same weighting as Wallet.GetPrivacyPercentage() in Wasabi, so the number matches the desktop: semi-private coins count partly.
    nonisolated static func privacyPercentage(_ coins: [Coin], target: Int) -> Int {
        let current = coins.reduce(0.0) { sum, c in
            let cap = Double(c.anonymityScore >= Double(target) ? target - 1 : target - 2)
            return sum + Double(c.amount) * min(c.anonymityScore - 1, cap)
        }
        let max = Double(coins.reduce(0) { $0 + $1.amount }) * Double(target - 1)
        return max == 0 ? 0 : Int(current * 100 / max)
    }

    func refresh() async {
        #if DEBUG
        if Self.demo { return } // UI tests: the demo wallet is the whole truth, no link is ever tried
        #endif
        refreshing = true
        defer { refreshing = false }
        if server.transport == .onion {
            await Task.detached { Tor.start() }.value // opens Arti's state on disk; keep it off the main thread
            // Arti waits for bootstrap before connecting anyway; polling instead keeps the progress visible.
            if let progress = Tor.progress, progress < 100 {
                torProgress = progress
                return
            }
        }
        torProgress = nil
        let rpc = rpc
        let started = Date.now // a Tor request can hang for minutes, so the grace period counts from when it began
        do {
            status = try await rpc.call("getstatus")
            wallets = try await rpc.call("listwallets", as: [WalletName].self).map(\.walletName)
            do { fees = try await rpc.call("getfeerates") } catch where !RPCError.isTransport(error) {}
            error = nil
            linkError = nil
            failingSince = nil
            updated = .now
        } catch where RPCError.isTransport(error) {
            linkFailed(error, started: started)
            return
        } catch {
            self.error = error.localizedDescription // Wasabi itself said no (wrong RPC password, ...)
            return
        }

        if wallet.map(wallets.contains) != true { wallet = wallets.first }
        guard let wallet else { return }
        do {
            // getwalletinfo fails when no coordinator is configured; balance then falls back to the coin sum.
            do { info = try await rpc.call("getwalletinfo", wallet: wallet) } catch where !RPCError.isTransport(error) { info = nil }
            coins = try await rpc.call("listunspentcoins", wallet: wallet)
            history = try await rpc.call("gethistory", wallet: wallet)
            do { payments = try await rpc.call("listpaymentsincoinjoin", wallet: wallet) } catch where !RPCError.isTransport(error) { payments = [] }
            walletError = nil
            // A payment that was not on screen before: this is what "after receiving" means while the app is open.
            let incoming = Set(history.filter { $0.amount > 0 && !$0.islikelycoinjoin }.map(\.tx))
            let fresh = seenIncoming.map { $0.wallet == wallet && !incoming.subtracting($0.txs).isEmpty } ?? false
            seenIncoming = (wallet, incoming)
            if fresh, self.wallet == wallet { await autoCoinjoin() }
        } catch where RPCError.isTransport(error) {
            linkFailed(error, started: started) // keep the last coins and history on screen
        } catch {
            coins = []
            history = []
            payments = []
            walletError = error.localizedDescription
        }
    }

    // With data on screen a dropped link only greys the top-left light. Before the first answer, a slow first Tor circuit
    // (Arti reports it as a timeout) still counts as connecting for 3 minutes.
    private func linkFailed(_ error: any Error, started: Date) {
        let since = failingSince ?? started
        failingSince = since
        if status == nil, server.transport == .onion, Date.now.timeIntervalSince(since) < 180 { return }
        linkError = error.localizedDescription
    }

    // Starts a coinjoin the way the desktop's auto-coinjoin would, for hot wallets only and without ever prompting: a wallet
    // that is not logged in on the computer gets a quiet hint in the Privacy tile. Balanced stops once every coin reaches the
    // target (desktop Play); More private stays armed for whatever arrives next (desktop auto-coinjoin, Wasabi's
    // stopWhenAllMixed parameter false). Neither overrides Wasabi's stop threshold. Skipped when the desktop's own auto-coinjoin is on.
    func autoCoinjoin() async {
        #if DEBUG
        // Simulator and screenshot runs talk to the live wallet read-only; nothing may start from them.
        if Screen.current != nil || ProcessInfo.processInfo.environment["WASABI_RPC_PASSWORD"] != nil { return }
        #endif
        guard privacyStop != .cheap, let wallet, let info, info.canAutoCoinjoin, info.isAutoCoinjoin != true, !info.coinjoining else { return }
        do {
            try await rpc.call("startcoinjoin", params: ["", privacyStop == .balanced, false] as [any Sendable], wallet: wallet)
            coinjoinHint = nil
        } catch let error as RPCError where error.needsPassphrase {
            coinjoinHint = "New coins arrived. Log in to \(wallet) on your computer and they will be made private."
        } catch {
            coinjoinHint = error.localizedDescription
        }
    }

    // Runs a mutating action. If Wasabi says the wallet is not logged in, asks for the passphrase once and retries with it.
    @discardableResult
    func perform(_ title: String, password: String = "", _ action: @escaping @MainActor (String) async throws -> String?) async -> Bool {
        #if DEBUG
        if Self.demo { notice = "Demo wallet: \(title) is not performed"; return false } // UI tests never reach a real Wasabi
        #endif
        busy = true
        defer { busy = false }
        do {
            if let message = try await action(password) { notice = message }
            await refresh()
            return true
        } catch let error as RPCError where error.needsPassphrase && password.isEmpty {
            passphrase = PassphraseRequest(title: title, action: action)
        } catch {
            notice = error.localizedDescription
        }
        return false
    }

    // Discreet mode (sabi's DISCREET): keep the shape, hide the digits.
    func btc(_ sats: Int) -> String {
        let text = Bitcoin.btc(sats) + " BTC"
        return discreet ? text.replacing(/\d/, with: "*") : text
    }

    // Whole dollars, except under a dollar where bitcoin.design asks for two meaningful digits rather than "$0".
    func usd(_ sats: Int) -> String? {
        guard let rate = status?.exchangeRate, rate > 0 else { return nil }
        if discreet { return "$***" }
        let value = Double(sats) / 1e8 * rate
        let style = FloatingPointFormatStyle<Double>.Currency(code: "USD")
        return value.formatted(value < 1 ? style.precision(.significantDigits(2)) : style.precision(.fractionLength(0)))
    }

    func mask(_ address: String) -> String {
        discreet ? address.prefix(4) + String(repeating: "*", count: 8) : address
    }
}

#if DEBUG
// Screenshot runs: `SIMCTL_CHILD_WASABI_SCREEN=sendReview simctl launch ...` opens that screen, with demo data where a step would
// otherwise need a write to the RPC (new address, build). Nothing here ever reaches Wasabi.
enum Screen: String {
    case home, coins, coin, transaction, privacy, menu
    case receive, receiveLow, receiveHigh, receiveAddress
    case send, sendLow, sendHigh, sendAmount, sendAmountHigh, sendFee, sendReview, sendSuccess

    static let current = ProcessInfo.processInfo.environment["WASABI_SCREEN"].flatMap(Screen.init)
    static let demoAddress = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" // BIP173 test vector
    static let demoTaprootAddress = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0" // BIP350 test vector
    static let demoTxid = "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"

    var flow: ContentView.Flow? {
        switch self {
        case .receive, .receiveLow, .receiveHigh, .receiveAddress: .receive
        case .send, .sendLow, .sendHigh, .sendAmount, .sendAmountHigh, .sendFee, .sendReview, .sendSuccess: .send(nil)
        case .home, .coins, .coin, .transaction, .privacy, .menu: nil
        }
    }

    // Screens whose Send sheet opens pre-filled with the demo payment.
    var prefilledSend: Bool { [.sendAmount, .sendAmountHigh, .sendFee, .sendReview, .sendSuccess].contains(self) }

    // The slider stop a screenshot run forces; nil keeps the stored one.
    var stop: PrivacyStop? {
        switch self {
        case .receiveLow, .sendLow: .cheap
        case .receiveHigh, .sendHigh, .sendAmountHigh: .private
        default: nil
        }
    }
}

// UI tests: `WASABI_DEMO=1` puts a mainnet wallet on screen without any link. The JSON is in Wasabi's own response shapes.
extension Mirror {
    static let demo = ProcessInfo.processInfo.environment["WASABI_DEMO"] == "1"

    // Store screenshots and UI tests: made-up but believable data. Addresses are BIP173's public test vector, txids are
    // hashes of a counter; nothing here belongs to a real wallet.
    func loadDemo() {
        func decode<T: Decodable>(_ json: String) -> T { try! JSONDecoder().decode(T.self, from: Data(json.utf8)) }
        func txid(_ i: Int) -> String { SHA256.hash(data: Data("wasabi-preview-demo-\(i)".utf8)).map { String(format: "%02x", $0) }.joined() }
        server = Server(host: "100.64.0.1", user: "demo", password: "demo")
        status = decode(#"{"torStatus":"Running","bestBlockchainHeight":"921337","filtersLeft":0,"network":"Main","exchangeRate":95000,"peers":[{"isConnected":true},{"isConnected":true},{"isConnected":true},{"isConnected":true},{"isConnected":true},{"isConnected":true},{"isConnected":true},{"isConnected":true}]}"#)
        info = decode(#"{"loaded":true,"balance":4876543,"coinjoinStatus":"In progress","anonScoreTarget":10,"isWatchOnly":false,"isHardwareWallet":false}"#)
        wallets = ["Daily", "Savings"]
        wallet = "Daily"
        fees = ["2": 3.1, "6": 1.6, "144": 1.0]
        let coinSpecs: [(Int, Double, String?)] = [
            (1_048_576, 34, nil), (1_048_576, 21, nil), (524_288, 27, nil), (524_288, 12, nil), (262_144, 15, nil),
            (500_000, 6, "Bitcoin meetup refund"), (918_559, 1, "Salary October"), (50_000, 1, "Coffee change"),
        ]
        coins = coinSpecs.enumerated().map { i, spec in
            Coin(txid: txid(i), index: i % 3, amount: spec.0, anonymityScore: spec.1, confirmed: i != 7, confirmations: i == 7 ? 0 : 6 + 17 * i,
                 label: spec.2, address: Screen.demoAddress, excludedFromCoinjoin: false, spentBy: nil)
        }
        let historySpecs: [(Int, String, Bool)] = [
            (-21_500, "Groceries", false), (-1_237, "", true), (918_559, "Salary October", false), (-988, "", true),
            (-64_000, "Dinner with friends", false), (500_000, "Bitcoin meetup refund", false), (-1_412, "", true),
            (-15_000, "Coffee at the corner bar", false), (2_000_000, "From savings", false), (-1_105, "", true),
        ]
        history = decode("[" + historySpecs.enumerated().map { i, spec in
            #"{"datetime":\#(1_792_000_000 - i * 61_200),"height":"\#(i == 0 ? "Mempool" : String(921_337 - 9 * i))","amount":\#(spec.0),"label":"\#(spec.1)","tx":"\#(txid(100 + i))","islikelycoinjoin":\#(spec.2)}"#
        }.joined(separator: ",") + "]")
        updated = .now
    }
}
#endif

// One home screen under a NavigationStack; Receive and Send open as sheets, so pulling down always lands back home.
// Details (a transaction, a coin, the Privacy screen) push onto the stack. Everything that is not the wallet itself
// (connection, Privacy, Settings, node facts) lives in the side menu behind the ≡ button.
struct ContentView: View {
    @State private var mirror = Mirror()
    @State private var flow: Flow?
    @State private var path = NavigationPath()
    @State private var showSettings = false
    @State private var menu = false
    @State private var scanning = false
    @State private var scanDrag: CGFloat = 0 // the right-edge pan in progress; a camera glyph follows it
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    enum Flow: Identifiable, Equatable {
        case receive
        case send(Bitcoin.Payment?) // pre-filled from a scan, or empty from the action bar
        var id: String {
            switch self {
            case .receive: "receive"
            case .send: "send"
            }
        }
    }

    enum Route: Hashable { case privacy }

    init() {
        #if DEBUG
        if let flow = Screen.current?.flow { _flow = State(initialValue: flow) }
        if Screen.current == .privacy { _path = State(initialValue: NavigationPath([Route.privacy])) }
        if Screen.current == .menu { _menu = State(initialValue: true) }
        #endif
    }

    var body: some View {
        NavigationStack(path: $path) {
            HomeView(mirror: mirror) { path.append($0) }
                .safeAreaBar(edge: .bottom) { ActionBar(mirror: mirror) { flow = $0 } }
                .navigationTitle(mirror.wallet ?? "Wasabi")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) { menuButton }
                    ToolbarItem(placement: .principal) { walletMenu }.sharedBackgroundVisibility(.hidden)
                    ToolbarItem(placement: .topBarTrailing) {
                        // The findable twin of the right-edge swipe: edge gestures are neither discoverable nor usable with VoiceOver.
                        Button("Scan to pay", systemImage: "qrcode.viewfinder") { scanning = true }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { mirror.discreet.toggle() } label: {
                            Label { Text(mirror.discreet ? "Show amounts" : "Discreet mode") } icon: { Image(uiImage: (mirror.discreet ? WasabiIcon.eyeHide : .eyeShow).uiImage) }
                        }
                    }
                }
                .navigationDestination(for: HistoryItem.self) { TransactionView(mirror: mirror, item: $0) }
                .navigationDestination(for: Coin.self) { CoinView(mirror: mirror, coin: $0) }
                .navigationDestination(for: Route.self) { _ in PrivacyView(mirror: mirror) }
        }
        .tint(Wasabi.accent)
        .sideMenu(open: $menu, edgeSwipe: path.isEmpty) { menuContent }
        // Pan in from the right screen edge on the home: the scanner. A screen-edge pan only starts in the system's edge
        // strip, so coin rows keep their trailing swipe action everywhere else.
        .gesture(EdgePan(edge: .right, enabled: path.isEmpty && !menu) { scanDrag = max(0, -$0) } ended: { x, velocity in
            withAnimation(.spring(duration: 0.3)) {
                if EdgePan.projected(-x, velocity: -velocity) > EdgePan.commit { scanning = true }
                scanDrag = 0
            }
        })
        .overlay(alignment: .trailing) { if scanDrag > 0, !reduceMotion { scanHint } }
        .fullScreenCover(isPresented: $scanning) { ScannerSheet(network: mirror.network) { flow = .send($0) } }
        .sheet(isPresented: $showSettings) { SettingsView(mirror: mirror) }
        .sheet(item: $flow) { flow in
            switch flow {
            case .receive: ReceiveView(mirror: mirror)
            case .send(let payment): SendView(mirror: mirror, payment: payment)
            }
        }
        .wasabiAlerts(mirror, active: flow == nil && !showSettings)
        .overlay { if mirror.busy, flow == nil { ProgressView().padding().glassEffect() } }
        .onAppear { if mirror.server.user.isEmpty { showSettings = true } }
        .task(id: mirror.server) {
            while !Task.isCancelled {
                await mirror.refresh()
                try? await Task.sleep(for: .seconds(mirror.torProgress == nil ? 5 : 1))
            }
        }
        #if DEBUG
        .onChange(of: mirror.history.count) {
            if Screen.current == .transaction, path.isEmpty, let first = mirror.history.max(by: { $0.datetime < $1.datetime }) { path.append(first) }
        }
        .onChange(of: mirror.coins.count) {
            if Screen.current == .coin, path.isEmpty, let coin = mirror.coins.max(by: { $0.anonymityScore < $1.anonymityScore }) { path.append(coin) }
        }
        #endif
    }

    // A camera glyph peeks in with the finger, so the right-edge pan has something to follow before the scanner opens.
    private var scanHint: some View {
        Image(systemName: "qrcode.viewfinder").font(.title2)
            .frame(width: 56, height: 56)
            .glassEffect(.regular, in: .circle)
            .offset(x: max(-16, 72 - scanDrag))
            .accessibilityHidden(true)
    }

    // ≡ opens the side menu. The link light used to sit here; now a grey dot on the button says the link is down.
    private var menuButton: some View {
        // A custom view, not a Label: the toolbar renders Label icons as bare template images and drops the badge.
        Button { menu = true } label: {
            Image(systemName: "line.3.horizontal")
                .overlay(alignment: .topTrailing) {
                    if mirror.link == .failed {
                        Circle().fill(.gray).frame(width: 8, height: 8)
                            .overlay(Circle().strokeBorder(.background, lineWidth: 1))
                            .offset(x: 5, y: -4)
                    }
                }
        }
        .accessibilityLabel("Menu")
        .accessibilityValue(mirror.link == .failed ? "Link timed out" : "")
    }

    // Connection first (what the ≡ dot was about), then the two screens that left the top bar, then the node facts.
    private var menuContent: some View {
        VStack(alignment: .leading, spacing: 18) {
            LinkStatus(mirror: mirror)
            Divider()
            menuItem("Privacy", .shieldTick) { path.append(Route.privacy) }
            menuItem("Settings", .settings) { showSettings = true }
            if let status = mirror.status {
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    Text("WASABI").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                    nodeRow(.network, "Network", status.network)
                    nodeRow(.tor, "Tor", status.torStatus)
                    nodeRow(.blockHeight, "Block height", status.bestBlockchainHeight)
                    if status.filtersLeft > 0 { LabeledContent { Text("\(status.filtersLeft)") } label: { Label("Filters left", systemImage: "arrow.down.circle") } }
                    nodeRow(.peers, "Peers", "\(status.peers.filter(\.isConnected).count)")
                }
                .font(.subheadline)
            }
        }
    }

    private func menuItem(_ title: String, _ icon: WasabiIcon, _ act: @escaping () -> Void) -> some View {
        Button { menu = false; act() } label: {
            Label { Text(title) } icon: { Icon(icon, size: 20) }
                .font(.body)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 6)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }

    private func nodeRow(_ icon: WasabiIcon, _ label: String, _ value: String) -> some View {
        LabeledContent { Text(value).monospacedDigit() } label: { Label { Text(label) } icon: { Icon(icon, size: 16) } }
    }

    // The wallet name is the title; tapping it switches wallets. Toolbars render Labels icon-only, so the name lives here.
    private var walletMenu: some View {
        Menu {
            Picker("Wallet", selection: Binding { mirror.wallet } set: { mirror.wallet = $0; Task { await mirror.refresh() } }) {
                ForEach(mirror.wallets, id: \.self) { Text($0).tag(Optional($0)) }
            }
        } label: {
            HStack(spacing: 7) {
                WasabiLogo().frame(height: 17)
                Text(mirror.wallet ?? "Wasabi").font(.headline).foregroundStyle(.primary)
                Image(systemName: "chevron.down").font(.caption2.weight(.bold)).foregroundStyle(.secondary)
            }
        }
        .tint(.primary)
        .accessibilityLabel("Wallet \(mirror.wallet ?? "not chosen"), switch wallet")
    }
}

// Passphrase prompt and notices. Alerts only present from the topmost screen, so the home passes `active: false` while a
// flow sheet is up and the sheet shows them instead.
struct WasabiAlerts: ViewModifier {
    let mirror: Mirror
    let active: Bool
    @State private var typed = ""

    func body(content: Content) -> some View {
        content
            .alert(mirror.passphrase?.title ?? "", isPresented: Binding { active && mirror.passphrase != nil } set: { if !$0 { mirror.passphrase = nil } }, presenting: mirror.passphrase) { request in
                SecureField("Wallet passphrase", text: $typed)
                Button("Cancel", role: .cancel) { typed = "" }
                Button("Continue") {
                    let password = typed
                    typed = ""
                    Task { await mirror.perform(request.title, password: password, request.action) }
                }
            } message: { _ in
                Text("The wallet is not logged in on your computer. The passphrase is sent once and not stored.")
            }
            .alert("Wasabi", isPresented: Binding { active && mirror.notice != nil } set: { if !$0 { mirror.notice = nil } }, presenting: mirror.notice) { _ in
                Button("OK") {}
            } message: { Text($0) }
    }
}

extension View {
    func wasabiAlerts(_ mirror: Mirror, active: Bool = true) -> some View { modifier(WasabiAlerts(mirror: mirror, active: active)) }

    // Common list header: connection error, or the unloaded-wallet state with a Load button.
    @ViewBuilder func walletGate(_ mirror: Mirror) -> some View {
        if mirror.status == nil, mirror.error == nil, mirror.linkError == nil {
            ConnectingLoader(transport: mirror.server.transport, progress: mirror.torProgress)
        } else if let error = mirror.error ?? (mirror.status == nil ? mirror.linkError : nil) {
            Label { Text(error) } icon: { Icon(.warning) }.foregroundStyle(Wasabi.error)
        } else if let walletError = mirror.walletError, let wallet = mirror.wallet {
            Label(walletError, systemImage: "lock").foregroundStyle(.secondary)
            if mirror.walletNotLoaded {
                Button("Load \(wallet) on the computer") {
                    Task { await mirror.perform("Load wallet") { _ in try await mirror.rpc.call("loadwallet", params: [wallet]); return nil } }
                }
            }
        } else {
            self
        }
    }
}

// The home is the wallet: one card that carries its name in the caption, the balance, the coins folded behind it and
// the privacy state, then a Transactions card right under it. Both are List sections so coin rows keep their swipe
// action; the wallet card's rows share one surface so they read as a single card (bitcoin.design: balance first).
struct HomeView: View {
    let mirror: Mirror
    let open: (ContentView.Route) -> Void
    @State private var showCoins = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(mirror: Mirror, open: @escaping (ContentView.Route) -> Void) {
        self.mirror = mirror
        self.open = open
        #if DEBUG
        if Screen.current == .coins || Screen.current == .coin { _showCoins = State(initialValue: true) }
        #endif
    }

    var body: some View {
        List {
            Section {
                balance
                if mirror.ready {
                    if showCoins { coins }
                    privacy
                }
            }
            .wasabiRows(card: true)
            if mirror.ready { transactions }
        }
        .listSectionSpacing(10)
        .contentMargins(.top, 6, for: .scrollContent)
        .scrollContentBackground(.hidden)
        .background { Backdrop() }
        .refreshable { await mirror.refresh() }
    }

    private func caption(_ text: String) -> some View {
        Text(text).font(.caption2.weight(.bold)).foregroundStyle(.secondary).frame(maxWidth: .infinity)
    }

    // "Balance <wallet>" ties the number to the name above it. Tapping it folds the coins out underneath.
    private var balance: some View {
        let content = VStack(spacing: 10) {
            caption("Balance \(mirror.wallet ?? "")")
            VStack(spacing: 4) {
                Amount(sats: mirror.balance, mirror: mirror, font: .largeTitle.weight(.light)).lineLimit(1).minimumScaleFactor(0.6)
                if let usd = mirror.usd(mirror.balance) { Text(usd).foregroundStyle(.secondary).frosted(mirror.discreet) }
            }
            .walletGate(mirror)
            if mirror.ready {
                HStack(spacing: 4) {
                    Text("\(mirror.coins.count) coins")
                    Image(systemName: "chevron.down").rotationEffect(.degrees(showCoins ? 180 : 0))
                }
                .font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        return Group {
            if mirror.ready {
                Button { withAnimation(reduceMotion ? nil : .snappy(duration: 0.3)) { showCoins.toggle() } } label: { content }
                    .buttonStyle(.plain)
                    .accessibilityValue(showCoins ? "Expanded" : "Collapsed")
                    .accessibilityHint(showCoins ? "Hides the coins" : "Shows the coins in this wallet")
            } else {
                content
            }
        }
        .alignmentGuide(.listRowSeparatorLeading) { _ in 0 } // the sketch's divider runs the whole card
    }

    // The accordion: the same coin rows as the desktop list, swipe left to exclude one from coinjoins, tap for the Coin screen.
    @ViewBuilder private var coins: some View {
        ForEach(mirror.coins.sorted { $0.amount > $1.amount }) { coin in
            let tier = mirror.privacyTier(coin.anonymityScore)
            let score = coin.anonymityScore.formatted(.number.precision(.fractionLength(0)))
            NavigationLink(value: coin) {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Amount(sats: coin.amount, mirror: mirror)
                        Text(coin.label?.isEmpty == false ? coin.label! : mirror.mask(coin.address))
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                            .frosted(mirror.discreet && coin.label?.isEmpty != false)
                    }
                    Spacer(minLength: 8)
                    // Same indicators as the desktop coin list: excluded in coin-orange, pending clock, shield tier by anon score.
                    if coin.excluded { Icon(.forbidden, size: 14).foregroundStyle(Wasabi.orangeCoin).accessibilityLabel("Excluded from coinjoin") }
                    if !coin.confirmed { Icon(.clock, size: 15).foregroundStyle(.secondary).accessibilityLabel("Pending") }
                    Label { Text(score) } icon: { Icon(tier.icon, size: 16) }
                        .labelStyle(.titleAndIcon).monospacedDigit().fixedSize()
                        .foregroundStyle(tier.color)
                        .accessibilityLabel("Anonymity score \(score), \(tier.name)")
                }
            }
            // Swipe instead of a toggle per row: 77 switches crowded the list and covered the address.
            .swipeActions { ExcludeButton(mirror: mirror, coin: coin, short: true) }
        }
        Text("A green shield has reached the privacy target (\(mirror.target)). Tap a coin for details; swipe left to exclude it from coinjoins.")
            .font(.footnote).foregroundStyle(.secondary)
    }

    // The music box in plain words: shield, percentage, what Wasabi is doing, the bar, and only the private amount; the
    // semi- and non-private split stays on the Privacy screen. The whole row opens that screen.
    private var privacy: some View {
        let p = mirror.privacy
        let percentage = min(max(mirror.privacyPercentage, 0), 100)
        let state = mirror.coinjoinState
        return Button { open(.privacy) } label: {
            VStack(spacing: 10) {
                caption("PRIVACY")
                VStack(spacing: 4) {
                    HStack(spacing: 10) {
                        // Greens up with the percentage, like the bar below it fills.
                        Icon(.shieldTick, size: 34).foregroundStyle(Color.secondary.mix(with: Wasabi.privacyStrong, by: Double(percentage) / 100))
                            .animation(.easeInOut(duration: 0.6), value: percentage)
                        Text("\(percentage)%").font(.title.weight(.bold)).monospacedDigit().frosted(mirror.discreet)
                    }
                    HStack(spacing: 5) {
                        Icon(state.active ? .play : .pause, size: 11)
                        Text(state.text)
                    }
                    .font(.subheadline).foregroundStyle(state.active ? Wasabi.accent : .secondary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(percentage) percent private, \(state.text)")
                PrivacyBar(private: p.private, semi: p.semi, total: mirror.balance, discreet: mirror.discreet)
                HStack(spacing: 6) {
                    Circle().fill(Wasabi.privacyStrong).frame(width: 8, height: 8)
                    Text("Private").foregroundStyle(.secondary)
                    Amount(sats: p.private, mirror: mirror, font: .subheadline)
                }
                .font(.subheadline)
                if let hint = mirror.coinjoinHint {
                    Label { Text(hint) } icon: { Image(systemName: "lock") }.font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .overlay(alignment: .topTrailing) {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary).padding(.top, 10)
            }
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the Privacy screen")
    }

    // Transaction rows as bitcoin.design describes them: direction icon, label, date, signed amount; tap for details.
    private var transactions: some View {
        Section {
            caption("TRANSACTIONS").listRowSeparator(.hidden).padding(.top, 4)
            if mirror.history.isEmpty {
                Text("No transactions yet").foregroundStyle(.secondary)
            }
            ForEach(mirror.history.sorted { $0.datetime > $1.datetime }) { item in
                NavigationLink(value: item) {
                    HStack(spacing: 12) {
                        Icon(item.islikelycoinjoin ? .shield : item.amount > 0 ? .receive : .send, size: 16).foregroundStyle(.secondary)
                        VStack(alignment: .leading) {
                            Text(item.title).lineLimit(1)
                            Text(item.datetime.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        // One line always: a pending clock next to a long amount used to wrap "BTC" onto a second line.
                        Amount(sats: item.amount, mirror: mirror, signed: true).lineLimit(1).minimumScaleFactor(0.7).layoutPriority(1)
                        if item.pending { Icon(.clock, size: 15).foregroundStyle(.secondary).accessibilityLabel("Pending") }
                    }
                }
            }
        }
        .wasabiRows(card: true)
    }
}

extension HistoryItem {
    var title: String { islikelycoinjoin ? "Privacy round" : (label?.isEmpty == false ? label! : amount > 0 ? "Received" : "Sent") }
    var pending: Bool { height == "Mempool" }
}

// Receive | Send, pinned under the home: the two things a wallet is for. Coinjoin is a setting of those flows (the privacy
// slider) and a status on the Privacy tile, not a third verb.
struct ActionBar: View {
    let mirror: Mirror
    let open: (ContentView.Flow) -> Void
    @Environment(\.appTheme) private var theme

    var body: some View {
        GlassEffectContainer(spacing: 12) {
            HStack(spacing: 12) {
                action("Receive", .receive) { open(.receive) }
                action("Send", .send) { open(.send(nil)) }
            }
        }
        .padding(.horizontal)
        .padding(.top, 4)
        .padding(.bottom, 2)
        .background {
            // Rows recede into the window colour under the bar instead of peeking between its buttons.
            LinearGradient(colors: [Wasabi.window(theme).opacity(0), Wasabi.window(theme).opacity(0.94)], startPoint: .top, endPoint: .bottom)
                .padding(.top, -36)
                .ignoresSafeArea()
        }
        .disabled(!mirror.ready)
    }

    private func action(_ title: String, _ icon: WasabiIcon, _ act: @escaping () -> Void) -> some View {
        Button(action: act) { label(title, icon) }.buttonStyle(.glass)
    }

    private func label(_ title: String, _ icon: WasabiIcon) -> some View {
        VStack(spacing: 3) {
            Icon(icon, size: 22)
            Text(title).font(.caption.weight(.semibold))
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
    }
}

// The desktop privacy bar: neutral track, semi-private + private in the medium green, private on top in the strong green.
// Discreet mode fades the proportions out and leaves the neutral track, as Wasabi does.
struct PrivacyBar: View {
    let `private`: Int, semi: Int, total: Int, discreet: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Wasabi.privacyNone)
                if total > 0 {
                    Capsule().fill(Wasabi.privacyMedium).frame(width: geo.size.width * Double(semi + `private`) / Double(total))
                    Capsule().fill(Wasabi.privacyStrong).frame(width: geo.size.width * Double(`private`) / Double(total))
                }
            }
            .opacity(discreet ? 0 : 1)
            .background(Capsule().fill(.primary.opacity(0.2)))
            .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: discreet)
        }
        .frame(height: 8)
        .accessibilityHidden(true)
    }
}

// Transaction details, pushed from the Transactions card: the amount, then the facts, the txid last (bitcoin.design keeps
// technical details reachable but out of the way).
struct TransactionView: View {
    let mirror: Mirror
    let item: HistoryItem

    var body: some View {
        List {
            Section {
                VStack(spacing: 6) {
                    Icon(item.islikelycoinjoin ? .shield : item.amount > 0 ? .receive : .send, size: 30).foregroundStyle(.secondary)
                    Amount(sats: item.amount, mirror: mirror, signed: true, font: .title.weight(.light))
                    if let usd = mirror.usd(abs(item.amount)) { Text(usd).foregroundStyle(.secondary).frosted(mirror.discreet) }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .tileSurface()
            }
            .glassRow()
            Section {
                LabeledContent("Type", value: item.islikelycoinjoin ? "Coinjoin round" : item.amount > 0 ? "Received" : "Sent")
                if let label = item.label, !label.isEmpty { LabeledContent("Label", value: label) }
                LabeledContent("Date", value: item.datetime.formatted(date: .long, time: .shortened))
                LabeledContent("Status") {
                    if item.pending {
                        Label { Text("Pending in the mempool") } icon: { Icon(.clock, size: 14) }
                    } else {
                        Text("Confirmed in block \(item.height)")
                    }
                }
            }
            .wasabiRows()
            Section("Transaction ID") {
                Text(item.tx).font(.footnote.monospaced()).textSelection(.enabled)
                CopyButton("Copy transaction ID", text: item.tx)
            }
            .wasabiRows()
        }
        .scrollContentBackground(.hidden)
        .background { Backdrop() }
        .navigationTitle(item.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct SettingsView: View {
    let mirror: Mirror
    @State private var server = Server()
    @AppStorage("theme") private var theme = AppTheme.wasabi
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Picker("Connection", selection: $server.transport) {
                    Text("Tailscale").tag(Server.Transport.tailscale)
                    Text("Tor onion").tag(Server.Transport.onion)
                }
                .pickerStyle(.segmented)
                .wasabiRows()
                if server.transport == .onion {
                    Section {
                        TextField("xyz.onion", text: $server.onion).keyboardType(.URL)
                        SecureField("Client auth key", text: $server.onionKey)
                        TextField("Wasabi RPC port", value: $server.port, format: .number.grouping(.never)).keyboardType(.numberPad)
                    } header: {
                        Text("Onion service")
                    } footer: {
                        Text("The onion from the computer's HiddenServiceDir/hostname and the x25519 private key from tor-client.auth_private (the part after x25519:, or the whole line). Both stay in the Keychain; Tor runs inside this app.")
                    }
                    .wasabiRows()
                } else {
                    Section {
                        TextField("Host", text: $server.host).keyboardType(.URL)
                        TextField("Port", value: $server.port, format: .number.grouping(.never)).keyboardType(.numberPad)
                    } header: {
                        Text("Computer")
                    } footer: {
                        Text("Tailscale IP or name of the computer running Wasabi.")
                    }
                    .wasabiRows()
                }
                Section("JSON-RPC login") {
                    TextField("User", text: $server.user)
                    SecureField("Password", text: $server.password)
                }
                .wasabiRows()
                Section {
                    Picker("Theme", selection: $theme) {
                        Text("Wasabi").tag(AppTheme.wasabi)
                        Text("Black").tag(AppTheme.black)
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("Black keeps most of the screen pure black, so OLED pixels stay off and use less energy. Applies right away.")
                }
                .wasabiRows()
            }
            .scrollContentBackground(.hidden)
            .background { Backdrop() }
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .dismissableKeyboard()
            .navigationTitle("Settings")
            .toolbar {
                Button("Save") {
                    server.onion = server.onion.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                    Keychain.save(server)
                    mirror.server = server
                    dismiss()
                }
            }
            .onAppear { server = mirror.server }
        }
    }
}
