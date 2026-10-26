import SwiftUI

// The privacy slider: desktop Wasabi's three strategies (WalletCoinJoinSettingsView: Reduce Costs, Default Strategy,
// Enhance Privacy) as one 3-stop control shared by Receive and Send. bitcoin.design keeps coin control and coinjoin out
// of the default path and warns where the risk is, at the moment of requesting or sending, so the choice lives there.
enum PrivacyStop: String, CaseIterable {
    case cheap, balanced, `private`

    var name: String {
        switch self {
        case .cheap: "Lower fees"
        case .balanced: "Balanced"
        case .private: "More private"
        }
    }

    // The Privacy screen shows the stop as one toggle: off is Lower fees, on is Balanced, and a More private stop chosen in
    // Receive or Send stays as it is (shown as on) rather than being quietly downgraded.
    var automatic: Bool { self != .cheap }
    func setting(automatic on: Bool) -> PrivacyStop { on ? (self == .cheap ? .balanced : self) : .cheap }

    #if DEBUG
    static func selfCheck() {
        assert(PrivacyStop.cheap.setting(automatic: true) == .balanced && PrivacyStop.private.setting(automatic: true) == .private)
        assert(PrivacyStop.balanced.setting(automatic: false) == .cheap && PrivacyStop.private.setting(automatic: false) == .cheap)
        assert(!PrivacyStop.cheap.automatic && PrivacyStop.balanced.automatic && PrivacyStop.private.automatic)
    }
    #endif
}

struct PrivacySlider: View {
    let mirror: Mirror
    let explanation: String

    var body: some View {
        let stop = Binding { mirror.privacyStop } set: { mirror.privacyStop = $0 }
        VStack(alignment: .leading, spacing: 6) {
            Slider(value: Binding { Double(PrivacyStop.allCases.firstIndex(of: stop.wrappedValue)!) } set: { stop.wrappedValue = PrivacyStop.allCases[Int($0.rounded())] }, in: 0...2, step: 1)
                .tint(Wasabi.accent)
                .accessibilityLabel("Privacy")
                .accessibilityValue(stop.wrappedValue.name)
            HStack {
                ForEach(PrivacyStop.allCases, id: \.self) { s in
                    Text(s.name)
                        .font(.caption.weight(s == stop.wrappedValue ? .semibold : .regular))
                        .foregroundStyle(s == stop.wrappedValue ? .primary : .secondary)
                        .frame(maxWidth: .infinity, alignment: s == .cheap ? .leading : s == .private ? .trailing : .center)
                        .onTapGesture { stop.wrappedValue = s }
                        .accessibilityHidden(true)
                }
            }
            Text(explanation).font(.footnote).foregroundStyle(.secondary).padding(.top, 4)
        }
        .padding(.vertical, 4)
        .sensoryFeedback(.selection, trigger: stop.wrappedValue)
    }
}

// Exclude a coin from coinjoins, or let it back in (excludefromcoinjoin). Short titles fit a swipe action.
struct ExcludeButton: View {
    let mirror: Mirror
    let coin: Coin
    var short = false

    var body: some View {
        Button(coin.excluded ? (short ? "Include" : "Include in coinjoin") : (short ? "Exclude" : "Exclude from coinjoin"), systemImage: coin.excluded ? "arrow.triangle.2.circlepath" : "nosign") {
            let exclude = !coin.excluded
            Task { await mirror.perform("Exclude coin") { _ in try await mirror.rpc.call("excludefromcoinjoin", params: [coin.txid, coin.index, exclude] as [any Sendable], wallet: mirror.wallet); return nil } }
        }
        .tint(coin.excluded ? Wasabi.accent : Wasabi.orangeCoin)
    }
}

// The Privacy screen, pushed from the home tile: the music box with its controls, the wallet's slider stop, the facts
// (target, who signs, the desktop's own auto-start), sweep, and low-fee payments waiting in a round. Technical terms are
// fine here; this is where power users come.
struct PrivacyView: View {
    let mirror: Mirror
    @State private var sweepTo = ""

    var body: some View {
        List {
            Section { musicBox.glassRow() }.walletGate(mirror)
            if mirror.walletError == nil {
                Section {
                    Toggle("Make received coins private automatically", isOn: Binding { mirror.privacyStop.automatic } set: { mirror.privacyStop = mirror.privacyStop.setting(automatic: $0) })
                        .tint(Wasabi.accent)
                } header: {
                    Text(mirror.wallet ?? "This wallet")
                } footer: {
                    Text(toggleFooter)
                }
                .wasabiRows()
                Section {
                    LabeledContent("Privacy target (anonymity score)", value: "\(mirror.target)")
                    LabeledContent("Automatic start on the computer", value: mirror.info?.isAutoCoinjoin == true ? "On" : "Off")
                    if let info = mirror.info, info.isHardwareWallet {
                        LabeledContent("Signing", value: info.deviceSigns ? "Hardware device" : "Watch-only")
                    }
                    if mirror.info?.coinjoining != true {
                        Button { start(stopWhenAllPrivate: false) } label: { Label { Text("Keep making new coins private") } icon: { Icon(.coinjoin) } }
                    }
                } footer: {
                    Text("Start runs coinjoins until every coin reaches the target, then stops. Keep making new coins private stays on and picks up whatever arrives later, like Wasabi's automatic coinjoin. The target itself is set in Wasabi on your computer; the RPC cannot change it.")
                }
                .wasabiRows()
                if mirror.wallets.count > 1 {
                    Section {
                        Picker("Sweep into", selection: $sweepTo) {
                            Text("Choose wallet").tag("")
                            ForEach(mirror.wallets.filter { $0 != mirror.wallet }, id: \.self) { Text($0).tag($0) }
                        }
                        Button("Sweep via coinjoin") {
                            let target = sweepTo
                            Task { await mirror.perform("Sweep") { pw in try await mirror.rpc.call("startcoinjoinsweep", params: [pw, target], wallet: mirror.wallet); return nil } }
                        }
                        .disabled(sweepTo.isEmpty)
                    } footer: {
                        Text("Coinjoins every coin and sends the outputs to another wallet on your computer.")
                    }
                    .wasabiRows()
                }
                if !mirror.payments.isEmpty {
                    Section {
                        ForEach(mirror.payments) { payment in
                            VStack(alignment: .leading) {
                                HStack {
                                    Amount(sats: payment.amount, mirror: mirror)
                                    Spacer()
                                    Text(payment.status).foregroundStyle(.secondary)
                                }
                                Text(mirror.mask(payment.address ?? "")).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                                    .frosted(mirror.discreet)
                            }
                            .swipeActions {
                                Button("Cancel", role: .destructive) {
                                    Task { await mirror.perform("Cancel payment") { _ in try await mirror.rpc.call("cancelpaymentincoinjoin", params: [payment.id], wallet: mirror.wallet); return nil } }
                                }
                            }
                        }
                    } header: {
                        Label { Text("Low-fee payments waiting for a round") } icon: { Icon(.embeddedPayment, size: 14) }
                    } footer: {
                        Text("Swipe left to cancel a payment that has not been paid yet.")
                    }
                    .wasabiRows()
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background { Backdrop() }
        .navigationTitle("Privacy")
        .navigationBarTitleDisplayMode(.inline)
    }

    // Fees versus privacy in plain words. Receive and Send still show the same stop as a 3-stop slider.
    private var toggleFooter: String {
        switch mirror.privacyStop {
        case .cheap: "Off: coins stay as they arrive and no coinjoin fees are paid. You can still start a coinjoin by hand above."
        case .balanced: "On: when a payment arrives while this app is open, Wasabi runs it through coinjoins until it reaches the privacy target (\(mirror.target)). Each round costs a share of its mining fee. With the app closed this needs \"Automatically start coinjoin\" in Wasabi on your computer."
        case .private: "On, set to More private from Receive or Send: coinjoin stays on for whatever arrives, now and later. Each round costs a share of its mining fee."
        }
    }

    // Desktop Wasabi's "music box": logo, status line and progress strip on glass, with the play or stop control beside it.
    private var musicBox: some View {
        let coinjoining = mirror.info?.coinjoining == true
        return HStack(spacing: 12) {
            HStack(spacing: 14) {
                WasabiLogo().frame(width: 36, height: 36)
                VStack(alignment: .leading, spacing: 8) {
                    Text(mirror.coinjoinState.text).font(.subheadline).lineLimit(2)
                    Text("Wasabi: \(mirror.info?.coinjoinStatus ?? "Unknown")").font(.caption.monospaced()).foregroundStyle(.secondary)
                    Capsule().fill(.secondary.opacity(0.4)).frame(height: 4)
                        .overlay(alignment: .leading) { if coinjoining { Capsule().fill(Wasabi.accent.opacity(0.6)) } }
                        .accessibilityHidden(true)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .tileSurface()
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Coinjoin status: \(mirror.coinjoinState.text)")

            if coinjoining {
                Button(role: .destructive) {
                    Task { await mirror.perform("Stop coinjoin") { _ in try await mirror.rpc.call("stopcoinjoin", wallet: mirror.wallet); return nil } }
                } label: {
                    Label { Text("Stop coinjoin") } icon: { Icon(.stop, size: 22) }.labelStyle(.iconOnly).frame(width: 44, height: 44)
                }
                .buttonStyle(.glassProminent)
                .tint(Wasabi.error)
            } else {
                Button { start(stopWhenAllPrivate: true) } label: {
                    Label { Text("Start coinjoin") } icon: { Icon(.play, size: 22) }.labelStyle(.iconOnly).frame(width: 44, height: 44)
                }
                .buttonStyle(.glassProminent)
                .tint(Wasabi.accent)
            }
        }
        .buttonBorderShape(.circle)
        .padding(.vertical, 4)
    }

    // Wasabi's startcoinjoin [password, stopWhenAllMixed, overridePlebStop]. An explicit tap overrides the stop threshold, as
    // the desktop's second click on "click to continue" does; the automatic start in Mirror.autoCoinjoin never does.
    private func start(stopWhenAllPrivate: Bool) {
        Task {
            await mirror.perform("Start coinjoin") { pw in
                try await mirror.rpc.call("startcoinjoin", params: [pw, stopWhenAllPrivate, true] as [any Sendable], wallet: mirror.wallet)
                return nil
            }
        }
    }
}

// One coin: what it is worth, how private it is and why, where it came from, and whether it is spent. The anonymity set
// is drawn from Wasabi's own score, since no Wasabi RPC returns the transaction's outputs and asking a block explorer
// would tell that explorer which transactions are ours.
struct CoinView: View {
    let mirror: Mirror
    let coin: Coin

    var body: some View {
        let live = mirror.coins.first { $0.id == coin.id } ?? coin
        let full = mirror.allCoins.first { $0.id == coin.id } // listcoins: adds spentBy
        let own = Bitcoin.ownEqualOutputs(of: coin, among: mirror.allCoins.isEmpty ? [coin] : mirror.allCoins)
        let tier = mirror.privacyTier(live.anonymityScore)
        let score = live.anonymityScore.formatted(.number.precision(.fractionLength(0)))
        let origin = mirror.history.first { $0.tx == coin.txid }
        List {
            Section {
                VStack(spacing: 6) {
                    Icon(tier.icon, size: 30).foregroundStyle(tier.color)
                    Amount(sats: coin.amount, mirror: mirror, font: .title.weight(.light))
                    if let usd = mirror.usd(coin.amount) { Text(usd).foregroundStyle(.secondary).frosted(mirror.discreet) }
                    Text("\(tier.name.capitalized) · anonymity score \(score), target \(mirror.target)").font(.subheadline).foregroundStyle(tier.color)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .tileSurface()
            }
            .glassRow()
            Section {
                AnonymitySetGrid(score: live.anonymityScore, own: own.count)
            } header: {
                Text("Who else looks like this coin")
            } footer: {
                Text(own.count > 1
                    ? "Wasabi's score estimates how many equal outputs this coin blends in with; \(own.count) of them are yours from the same transaction. The exact crowd needs the full transaction, which Wasabi's RPC does not return, and this app never asks a block explorer."
                    : "Wasabi's score estimates how many equal outputs this coin blends in with. The exact crowd needs the full transaction, which Wasabi's RPC does not return, and this app never asks a block explorer.")
            }
            .wasabiRows()
            Section("Coin") {
                if let label = live.label, !label.isEmpty { LabeledContent("Label", value: label) }
                VStack(alignment: .leading, spacing: 6) {
                    Text("Address").foregroundStyle(.secondary).font(.subheadline)
                    Text(mirror.mask(coin.address)).font(.footnote.monospaced()).textSelection(.enabled).frosted(mirror.discreet)
                    CopyButton("Copy address", text: coin.address).font(.subheadline)
                }
                LabeledContent("Status") {
                    if live.confirmed {
                        Text(live.confirmations.map { "Confirmed, \($0) confirmations" } ?? "Confirmed")
                    } else {
                        Label { Text("Pending in the mempool") } icon: { Icon(.clock, size: 14) }
                    }
                }
                if let spender = full?.spentBy {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Spent by").foregroundStyle(.secondary).font(.subheadline)
                        Text(spender).font(.footnote.monospaced()).textSelection(.enabled)
                        CopyButton("Copy transaction ID", text: spender).font(.subheadline)
                    }
                } else {
                    LabeledContent("Spent", value: "No")
                }
                LabeledContent("Coinjoin") {
                    Label { Text(live.excluded ? "Excluded" : "Included") } icon: { if live.excluded { Icon(.forbidden, size: 14) } }
                        .foregroundStyle(live.excluded ? Wasabi.orangeCoin : .primary)
                }
                ExcludeButton(mirror: mirror, coin: live)
            }
            .wasabiRows()
            Section {
                if let origin {
                    NavigationLink(value: origin) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(origin.title)
                            Text(origin.datetime.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    LabeledContent("Block", value: origin.pending ? "Pending" : origin.height)
                }
                Text(coin.txid).font(.footnote.monospaced()).textSelection(.enabled)
                CopyButton("Copy transaction ID", text: coin.txid)
            } header: {
                Text("Came from transaction, output \(coin.index)")
            }
            .wasabiRows()
        }
        .scrollContentBackground(.hidden)
        .background { Backdrop() }
        .navigationTitle("Coin")
        .navigationBarTitleDisplayMode(.inline)
        .task { await mirror.loadAllCoins() }
    }
}

// The anonymity set as a crowd of equal outputs: Wasabi's score rounded to a count, ours in Wasabi green (this coin
// outlined), the rest neutral. Capped at 120 dots; the rest is said in words.
struct AnonymitySetGrid: View {
    let score: Double
    let own: Int
    private static let cap = 120

    var body: some View {
        let count = max(1, Int(score.rounded()))
        let shown = min(count, Self.cap)
        VStack(alignment: .leading, spacing: 10) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 14), spacing: 6)], spacing: 6) {
                ForEach(0..<shown, id: \.self) { i in
                    Circle()
                        .fill(i < own ? Wasabi.privacyStrong : Wasabi.privacyNone)
                        .overlay { if i == 0 { Circle().strokeBorder(.primary, lineWidth: 1.5) } }
                        .frame(width: 14, height: 14)
                }
            }
            HStack(spacing: 14) {
                legend(Wasabi.privacyStrong, own > 1 ? "Yours (\(own))" : "Yours", outlined: true)
                legend(Wasabi.privacyNone, count > shown ? "Others, \(count - shown) more not drawn" : "Others, estimated by Wasabi", outlined: false)
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("About \(count) equal outputs, \(own) of them yours")
    }

    private func legend(_ color: Color, _ text: String, outlined: Bool) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).overlay { if outlined { Circle().strokeBorder(.primary, lineWidth: 1.5) } }.frame(width: 10, height: 10)
            Text(text)
        }
    }
}

extension Mirror {
    // listcoins: every coin the wallet ever had, with what spent it. Read-only; only the coin screen needs it.
    func loadAllCoins() async {
        guard let wallet, allCoins.isEmpty else { return }
        do { allCoins = try await rpc.call("listcoins", wallet: wallet) } catch { allCoins = [] }
    }
}
