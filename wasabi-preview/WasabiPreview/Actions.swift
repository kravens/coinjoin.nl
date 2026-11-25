import LocalAuthentication
import SwiftUI

// Receive as bitcoin.design's payment request: who is paying and an optional amount first, then one big QR with copy and share.
struct ReceiveView: View {
    let mirror: Mirror
    @State private var label = "" // what is being typed; a comma or Return turns it into a chip
    @State private var chips: [String] = [] // labels chosen so far; Wasabi stores them as one comma-separated set
    @State private var keys: [WalletKey] = [] // listkeys: earlier labels and addresses awaiting payment
    @State private var amount = ""
    @State private var unit = "BTC"
    @AppStorage("taproot") private var taproot = true // Settings › Sending and receiving
    @State private var address: String?
    @State private var brightness: CGFloat?
    @Environment(\.dismiss) private var dismiss

    init(mirror: Mirror) {
        self.mirror = mirror
        #if DEBUG
        if Screen.current == .receiveAddress { _chips = State(initialValue: ["Alice", "concert tickets"]); _amount = State(initialValue: "0.001"); _address = State(initialValue: Screen.demoTaprootAddress) }
        // The demo coins sit on demoAddress, so its request shows the "Payment received" state.
        if Screen.current == .receivePaid { _chips = State(initialValue: ["Bitcoin meetup refund"]); _address = State(initialValue: Screen.demoAddress) }
        #endif
    }

    private var sats: Int? { unit == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) }
    private var uri: String? { address.map { Bitcoin.uri(address: $0, sats: sats) } }
    private var labels: [String] { chips + Labels.split(label) }
    private var fullLabel: String { labels.joined(separator: ", ") }

    private func commit() {
        for part in Labels.split(label) where !chips.contains(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) { chips.append(part) }
        label = ""
    }
    private var screen: UIScreen? { (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.screen }

    var body: some View {
        NavigationStack {
            Group {
                if let address, let uri { request(address, uri) } else { form }
            }
            .flowScreen(address == nil ? "Receive" : "Payment request") { dismiss() }
        }
        .flowSheet(mirror)
    }

    private var form: some View {
        List {
            Section {
                if !chips.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(chips, id: \.self) { chip in
                                Button { chips.removeAll { $0 == chip } } label: { Label(chip, systemImage: "xmark").labelStyle(ChipLabel()) }
                                    .buttonStyle(.borderedProminent).tint(Wasabi.accent.opacity(0.85)).controlSize(.small)
                                    .accessibilityLabel("\(chip), remove")
                            }
                        }
                    }
                }
                TextField(chips.isEmpty ? "Who is paying you" : "Add another label", text: $label)
                    .submitLabel(.next)
                    .onSubmit { commit() }
                    .onChange(of: label) { if label.contains(",") { commit() } }
                let suggestions = Labels.suggestions(Labels.ranked(keys), typed: label, chosen: labels)
                if !suggestions.isEmpty {
                    // Earlier labels, like desktop Wasabi's suggestions: tap one to add it.
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(suggestions, id: \.self) { suggestion in
                                Button(suggestion) { label = ""; chips.append(suggestion) }
                                    .buttonStyle(.bordered).tint(Wasabi.accent).controlSize(.small)
                            }
                        }
                    }
                }
                HStack {
                    TextField("Amount (optional)", text: $amount).keyboardType(.decimalPad)
                    Picker("Unit", selection: $unit) { Text("BTC").tag("BTC"); Text("sats").tag("sats") }.pickerStyle(.segmented).fixedSize()
                }
                if let sats, let usd = mirror.usd(sats) { Text(usd).font(.caption).foregroundStyle(.secondary).frosted(mirror.discreet) }
            } footer: {
                Text("Only you see the label: Wasabi keeps it so you know later where the coins came from. Separate people or reasons with a comma. Leave the amount empty and the sender fills it in.")
            }
            .wasabiRows()
            .walletGate(mirror)
            let waiting = Labels.waiting(keys)
            if !waiting.isEmpty {
                Section {
                    ForEach(waiting, id: \.address) { key in
                        // Show this address again instead of handing out a new one to someone who has not paid yet.
                        Button { chips = key.labels; label = ""; amount = ""; address = key.address } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(key.labels.joined(separator: ", ")).lineLimit(1)
                                Text(mirror.mask(key.address)).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                                    .frosted(mirror.discreet)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text("Waiting for payment")
                } footer: {
                    Text("Addresses you handed out that have not been paid yet. Tap one to show it again.")
                }
                .wasabiRows()
            }
        }
        .task { keys = await mirror.loadKeys() }
        .dismissableKeyboard()
        .onChange(of: unit) { old, new in // converts, like Send; keeping the digits would turn 1 BTC into 1 sat
            guard let value = old == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) else { amount = ""; return }
            amount = new == "BTC" ? Bitcoin.btc(value) : String(value)
        }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: "Create address", icon: .receive, disabled: labels.isEmpty || mirror.busy) {
                commit()
                let (label, taproot) = (fullLabel, taproot)
                // sabi.py: getnewaddress [label, taproot]; Wasabi refuses an empty label.
                Task {
                    await mirror.perform("New address") { _ in
                        address = try await mirror.rpc.call("getnewaddress", params: [label, taproot] as [any Sendable], wallet: mirror.wallet, as: NewAddress.self).address
                        return nil
                    }
                }
            }
        }
    }

    private func request(_ address: String, _ uri: String) -> some View {
        // The refresh loop keeps running under this sheet, so a coin on this address shows up here within seconds.
        let paid = mirror.coins.filter { $0.address == address }
        return ScrollView {
            VStack(spacing: 14) {
                if !paid.isEmpty {
                    HStack(spacing: 12) {
                        Icon(.check, size: 26).foregroundStyle(Wasabi.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Payment received").font(.headline)
                            Amount(sats: paid.reduce(0) { $0 + $1.amount }, mirror: mirror)
                            Text(paid.allSatisfy(\.confirmed) ? "Confirmed" : "Pending, waiting for a block").font(.footnote).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(14)
                    .tileSurface()
                    .accessibilityElement(children: .combine)
                    .transition(.scale(scale: 0.95).combined(with: .opacity))
                }
                VStack(spacing: 14) {
                    if let qr = Bitcoin.qr(uri) {
                        // Wasabi draws the QR on a white card; keep it scannable in dark mode and under discreet frosting.
                        Image(uiImage: qr).interpolation(.none).resizable().scaledToFit()
                            .padding(12).background(.white, in: .rect(cornerRadius: 12))
                            .frame(maxWidth: 260)
                            .frosted(mirror.discreet)
                            .accessibilityLabel("QR code of the payment request")
                    }
                    Text(mirror.spaced(address)).font(.footnote.monospaced()).multilineTextAlignment(.center) // Copy and Share give it whole
                        .frosted(mirror.discreet)
                    HStack(spacing: 12) {
                        CopyButton(text: uri)
                        ShareLink(item: uri) { Label { Text("Share") } icon: { Icon(.share) } }
                    }
                    .buttonStyle(.bordered)
                }
                .padding(14)
                .frame(maxWidth: .infinity)
                .tileSurface()
                Tile("REQUEST") {
                    LabeledContent("From", value: fullLabel)
                    if let sats, sats > 0 { LabeledContent("Amount") { Amount(sats: sats, mirror: mirror) } }
                    // From the address itself: a reopened one keeps the type it was made with, whatever Settings says now.
                    LabeledContent("Address type", value: Bitcoin.isTaproot(address) ? "Taproot" : "SegWit")
                }
                .font(.subheadline)
                Text("Each address is used once. Create a new one for the next payment.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal)
            }
            .padding()
        }
        .animation(.snappy(duration: 0.3), value: paid.count)
        // Done also arms the coinjoin for Balanced and More private; autoCoinjoin is quiet and does nothing for Lower fees.
        .safeAreaInset(edge: .bottom) { PrimaryAction(title: "Done", system: "checkmark") { Task { await mirror.autoCoinjoin() }; dismiss() } }
        // bitcoin.design: brighten the screen while a QR is shown so it scans first time.
        .onAppear { brightness = screen?.brightness; screen?.brightness = 1 }
        .onDisappear { if let brightness { screen?.brightness = brightness } }
    }
}

// A label chip: the title, then a small cross that says "tap to remove".
struct ChipLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) { configuration.title; configuration.icon.font(.caption2.weight(.bold)) }
    }
}

// Send as bitcoin.design's step-by-step flow: where to, how much, how fast, then review, Face ID and a success screen.
struct SendView: View {
    let mirror: Mirror
    @State private var path: [Step] = []
    @State private var address = ""
    @State private var amount = ""
    @State private var unit = "BTC"
    @State private var label = ""
    @State private var feeTarget = "6"
    @State private var customRate = 1
    @AppStorage("customFee") private var customFee = false // Settings › Sending and receiving
    @State private var scanning = false
    @State private var review: Review?
    @State private var sent: String? // txid, or the payment id when paid in coinjoin
    @Environment(\.dismiss) private var dismiss

    enum Step: Hashable { case amount, fee, review, success }

    struct Review: Identifiable {
        let id = UUID()
        let address: String
        let sats: Int
        let fee: Int?
        let inputs: Int
        let toxic: Bool
        let inCoinjoin: Bool
        let onlyPrivate: Bool
        let password: String // from the build step; lives here only until the flow closes
        let send: @MainActor (String) async throws -> String?
    }

    // `payment` comes from the home's scanner: the flow opens where there is still something to decide. The amount step
    // when the link has no (usable) amount, the fee step when it has; the review and Face ID are never skipped.
    init(mirror: Mirror, payment: Bitcoin.Payment? = nil) {
        self.mirror = mirror
        if mirror.info?.deviceSigns != true { _feeTarget = State(initialValue: "private") } // the mobile default
        if let payment {
            _address = State(initialValue: payment.address)
            _label = State(initialValue: payment.label ?? payment.message ?? "")
            if let sats = payment.sats, sats > 0 { _amount = State(initialValue: Bitcoin.btc(sats)) }
            if mirror.ready { _path = State(initialValue: amountValid ? [.amount, .fee] : [.amount]) }
        }
        #if DEBUG
        guard payment == nil, let screen = Screen.current, screen.prefilledSend else { return }
        _address = State(initialValue: Screen.demoAddress)
        _label = State(initialValue: "Demo")
        _amount = State(initialValue: "0.0015")
        let demo = Review(address: Screen.demoAddress, sats: 150_000, fee: 1_234, inputs: 2, toxic: true, inCoinjoin: false, onlyPrivate: false, password: "") { _ in nil }
        switch screen {
        case .sendAmount, .sendAmountHigh: _path = State(initialValue: [.amount])
        case .sendFee: _path = State(initialValue: [.amount, .fee])
        case .sendReview: _review = State(initialValue: demo); _path = State(initialValue: [.amount, .fee, .review])
        case .sendSuccess: _review = State(initialValue: demo); _sent = State(initialValue: Screen.demoTxid); _path = State(initialValue: [.amount, .fee, .review, .success])
        default: break
        }
        #endif
    }

    // A private payment (payincoinjoin) is the default. Not for a device-signed wallet, whose rounds need the device
    // (payincoinjoin refuses), and not for Send all: a round cannot pay out the whole balance.
    private var privateAllowed: Bool { mirror.info?.deviceSigns != true && !sendAll }
    private var inCoinjoin: Bool { feeTarget == "private" && privateAllowed }
    private var onlyPrivate: Bool { mirror.privacyStop == .private } // regular transactions only
    private var sats: Int? { unit == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) }
    private var spendable: [Coin] { mirror.coins.filter { $0.confirmed && (!onlyPrivate || $0.anonymityScore >= Double(mirror.target)) } }
    private var available: Int { spendable.reduce(0) { $0 + $1.amount } }
    private var confirmed: Int { mirror.coins.filter(\.confirmed).reduce(0) { $0 + $1.amount } }
    private var notYetPrivate: Int { confirmed - available } // confirmed but not yet private
    private var feeTargets: [String] { Bitcoin.feeChoices(mirror.fees) }
    // Only a send-all pays its fee out of the amount; anything less leaves room for the fee on top.
    private var sendAll: Bool { sats == available }
    private var subtractFee: Bool { sendAll }
    // A round can draw on any confirmed coin; a regular transaction only on what the privacy setting lets it spend.
    private var amountValid: Bool { (sats ?? 0) > 0 && sats! <= (inCoinjoin ? confirmed : available) }

    var body: some View {
        NavigationStack(path: $path) {
            destination
                .navigationDestination(for: Step.self) { step in
                    switch step {
                    case .amount: amountStep
                    case .fee: feeStep
                    case .review: if let review { ReviewView(mirror: mirror, review: review).flowScreen("Review") { dismiss() } }
                    case .success: success
                    }
                }
        }
        .flowSheet(mirror)
        .interactiveDismissDisabled(mirror.busy) // a half-finished build or send should not be swiped away
        .sheet(isPresented: $scanning) { ScannerSheet(network: mirror.network, found: apply) }
        .onChange(of: review?.id) { if review != nil { path.append(.review) } }
        // On `sent` rather than on perform's result: a retry from the passphrase prompt succeeds outside the Review screen.
        .onChange(of: sent) { if sent != nil { path.append(.success) } }
        // Fee rates may arrive after the view appears; keep the picker on a target Wasabi actually offers.
        .onChange(of: feeTargets, initial: true) { keepFeeTargetUsable() }
        .onChange(of: privateAllowed) { keepFeeTargetUsable() }
    }

    // Step 1: where to. Scanning is the primary way in, as the guide suggests; paste and typing are right there too.
    private var destination: some View {
        List {
            Section {
                HStack(spacing: 12) {
                    Button { scanning = true } label: { HStack(spacing: 8) { Icon(.qr); Text("Scan QR") }.frame(maxWidth: .infinity) }
                        .buttonStyle(.borderedProminent)
                    Button { paste(UIPasteboard.general.string ?? "") } label: { HStack(spacing: 8) { Icon(.paste); Text("Paste") }.frame(maxWidth: .infinity) }
                        .buttonStyle(.bordered)
                }
                .controlSize(.large)
                .lineLimit(1)
                .glassRow()
                .padding(.bottom, 4)
                TextField("Address or bitcoin: link", text: $address, axis: .vertical)
                    .font(.callout.monospaced()).autocorrectionDisabled().textInputAutocapitalization(.never)
                if let addressError {
                    Label { Text(addressError) } icon: { Icon(.warning, size: 14) }.font(.caption).foregroundStyle(Wasabi.error)
                }
            } header: {
                Text("To")
            }
            .wasabiRows()
            .walletGate(mirror)
            Section {
                TextField("Who are you paying", text: $label)
            } header: {
                Text("Label")
            } footer: {
                Text("Stored with the transaction so the history keeps its meaning.")
            }
            .wasabiRows()
        }
        .dismissableKeyboard()
        .flowScreen("Send") { dismiss() }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: "Continue", system: "chevron.right", disabled: address.isEmpty || addressError != nil) { path.append(.amount) }
        }
    }

    // Checksums, address type and the wallet's network, in the words Bitcoin.address gives; nil while the field is empty or fine.
    private var addressError: String? {
        guard !address.isEmpty else { return nil }
        do { _ = try Bitcoin.address(address, network: mirror.network); return nil } catch { return error.description }
    }

    // Step 2: how much, in BTC or sats with the fiat value underneath; switching units converts instead of clearing.
    private var amountStep: some View {
        List {
            Section {
                HStack {
                    TextField("0", text: $amount).keyboardType(.decimalPad).font(.title2.weight(.medium)).monospacedDigit()
                    Picker("Unit", selection: $unit) { Text("BTC").tag("BTC"); Text("sats").tag("sats") }.pickerStyle(.segmented).fixedSize()
                }
                if let sats, let usd = mirror.usd(sats) { Text(usd).foregroundStyle(.secondary).frosted(mirror.discreet) }
                if let sats, sats > 0, !amountValid { overBalance }
            } footer: {
                if inCoinjoin {
                    Text("Available: \(mirror.btc(confirmed)) in confirmed coins")
                } else if onlyPrivate {
                    Text("Private and ready: \(mirror.btc(available)) in \(spendable.count) coins." + (notYetPrivate > 0 ? " Another \(mirror.btc(notYetPrivate)) is still being made private." : ""))
                } else {
                    Text("Available: \(mirror.btc(available)) in \(spendable.count) confirmed coins")
                }
            }
            .wasabiRows()
            Section {
                Button {
                    amount = unit == "BTC" ? Bitcoin.btc(available) : String(available) // in the unit on screen; switching it would re-parse
                } label: {
                    HStack(spacing: 0) { Text("Send all ("); Amount(sats: available, mirror: mirror); Text(verbatim: ")") }
                }
            } footer: {
                Text("Send all is a regular transaction: it spends every \(onlyPrivate ? "private" : "confirmed") coin and takes the mining fee out of the amount.")
            }
            .wasabiRows()
        }
        .dismissableKeyboard()
        .onChange(of: unit) { old, new in
            guard let value = old == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) else { amount = ""; return }
            amount = new == "BTC" ? Bitcoin.btc(value) : String(value)
        }
        .flowScreen("Amount") { dismiss() }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: "Continue", system: "chevron.right", disabled: !amountValid || mirror.busy) { path.append(.fee) }
        }
    }

    private var overBalance: some View {
        Label { Text(!inCoinjoin && onlyPrivate ? "More than the private balance" : "More than the confirmed balance") } icon: { Icon(.warning, size: 14) }
            .font(.caption).foregroundStyle(Wasabi.error)
    }

    // A fee choice the screen still offers, else about an hour.
    private func keepFeeTargetUsable() {
        let usable = feeTargets.contains(feeTarget) || (customFee && feeTarget == "custom") || (feeTarget == "private" && privateAllowed)
        if !usable, let hour = feeTargets.min(by: { abs(Int($0)! - 6) < abs(Int($1)! - 6) }) { feeTarget = hour }
    }

    // Step 3: how to pay. A private payment first, as the default; then regular transactions by rough wait time, because
    // that is what people choose by. Two pickers on one selection, so each section keeps its own header.
    private var feeStep: some View {
        List {
            if privateAllowed {
                Section {
                    Picker("Private payment", selection: $feeTarget) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Private payment")
                            Text("With one of the next privacy rounds, usually within hours").font(.caption).foregroundStyle(.secondary)
                        }
                        .tag("private")
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                } footer: {
                    Text("No separate mining fee, and the receiver cannot tell which of the round's coins paid them.")
                }
                .wasabiRows()
            }
            Section {
                Picker("Regular transaction", selection: $feeTarget) {
                    ForEach(feeTargets, id: \.self) { target in
                        HStack {
                            Text(Self.wait(blocks: Int(target) ?? 0))
                            Spacer()
                            Text("\(target) blocks · \(mirror.fees[target]!.formatted(.number.precision(.fractionLength(1)))) sat/vB").foregroundStyle(.secondary)
                        }
                        .tag(target)
                    }
                    if customFee { Text("Custom rate").tag("custom") }
                }
                .pickerStyle(.inline)
                .labelsHidden()
                if customFee, feeTarget == "custom" { Stepper("\(customRate) sat/vB", value: $customRate, in: 1...500) }
                if !inCoinjoin, !amountValid { overBalance }
            } header: {
                Text("Regular transaction")
            } footer: {
                if mirror.info?.deviceSigns == true {
                    Text("A device signs this wallet's rounds, so a private payment is not possible here. The exact mining fee is shown on the next screen.")
                } else {
                    Text("Confirms sooner, as its own transaction. The exact mining fee is shown on the next screen before anything is sent.")
                }
            }
            .wasabiRows()
        }
        .flowScreen("How to pay") { dismiss() }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: inCoinjoin ? "Review payment" : "Review transaction", icon: .send, disabled: !amountValid || mirror.busy) { prepare() }
        }
    }

    // "about 20 min", "about 1 hr", "about 1 day": a block every ten minutes.
    private static func wait(blocks: Int) -> String {
        "about " + Duration.seconds(blocks * 600).formatted(.units(allowed: [.days, .hours, .minutes], width: .abbreviated, maximumUnitCount: 1))
    }

    // Step 5: done. The proof (txid) is right here to copy or share; Done pulls the whole flow down.
    private var success: some View {
        ScrollView {
            VStack(spacing: 18) {
                Icon(.check, size: 56).foregroundStyle(Wasabi.accent).padding(.top, 24)
                Text(review?.inCoinjoin == true ? "Queued" : "Sent").font(.title2.weight(.semibold))
                if let review {
                    Amount(sats: review.sats, mirror: mirror, font: .title.weight(.light))
                    Text(mirror.spaced(review.address)).font(.footnote.monospaced()).multilineTextAlignment(.center).frosted(mirror.discreet)
                }
                if let sent {
                    Tile(review?.inCoinjoin == true ? "PAYMENT ID" : "TRANSACTION ID", alignment: .center) {
                        Text(sent).font(.footnote.monospaced()).textSelection(.enabled)
                        HStack(spacing: 12) {
                            CopyButton(text: sent)
                            ShareLink(item: sent) { Label { Text("Share") } icon: { Icon(.share) } }
                        }
                        .buttonStyle(.bordered)
                    }
                }
                Text(review?.inCoinjoin == true ? "It goes out with one of the next privacy rounds, usually within hours. Until then it is listed on the Privacy screen, where you can still cancel it." : "It shows as pending in Transactions until a block confirms it.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
        }
        .navigationBarBackButtonHidden()
        .flowScreen("") { dismiss() }
        .safeAreaInset(edge: .bottom) { PrimaryAction(title: "Done", system: "checkmark") { dismiss() } }
    }

    // A scanned or pasted payment: a link fills amount and label too, a bare address just the address.
    private func apply(_ payment: Bitcoin.Payment) {
        address = payment.address
        if let paid = payment.sats, paid > 0 { unit = "BTC"; amount = Bitcoin.btc(paid) }
        if let l = payment.label ?? payment.message, label.isEmpty { label = l }
    }

    // Pasted text that does not parse lands in the field as it is, so the inline message can say what is wrong with it.
    private func paste(_ text: String) {
        if let payment = try? Bitcoin.parse(text, network: mirror.network) { apply(payment) } else { address = text.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    private func prepare() {
        guard let sats else { return }
        let address = address, label = label, inCoinjoin = inCoinjoin, onlyPrivate = onlyPrivate, subtractFee = subtractFee
        if inCoinjoin {
            // Wasabi PayInCoinJoin(address, amount, password); the passphrase is re-checked at the call. A queued payment
            // only goes out while the wallet is in coinjoin and Wasabi does not start one for it, so this does, like the
            // desktop's Play; stopWhenAllMixed follows the privacy setting and PlebStop is overridden for the payment's sake.
            review = Review(address: address, sats: sats, fee: nil, inputs: 0, toxic: false, inCoinjoin: true, onlyPrivate: false, password: "") { pw in
                let id: String = try await mirror.rpc.call("payincoinjoin", params: [address, sats, pw] as [any Sendable], wallet: mirror.wallet)
                sent = id
                guard mirror.info?.coinjoining != true else { return nil }
                do {
                    try await mirror.rpc.call("startcoinjoin", params: [pw, mirror.privacyStop != .private, true] as [any Sendable], wallet: mirror.wallet)
                    if mirror.privacyStop == .cheap { mirror.stopAfterPayments = true }
                } catch {
                    return "The payment is queued, but coinjoin did not start (\(error.localizedDescription)). Start it from the Privacy screen."
                }
                return nil
            }
            return
        }
        let need = subtractFee ? sats : sats + max(5_000, sats * 3 / 1000)
        let (coins, toxic) = Bitcoin.pickCoins(from: mirror.coins, target: mirror.target, need: need, onlyPrivate: onlyPrivate)
        if onlyPrivate, coins.reduce(0, { $0 + $1.amount }) < need {
            // Said here rather than as Wasabi's "insufficient funds": the private coins cover the amount but not the fee too.
            mirror.notice = "Your private coins cover the amount but not the mining fee as well. Send a little less, wait until more coins are private (\(mirror.btc(notYetPrivate)) is on its way), or choose Balanced."
            return
        }
        let fee: [String: any Sendable] = feeTarget == "custom" ? ["feeRate": customRate] : ["feeTarget": Int(feeTarget) ?? 6]
        func params(_ password: String) -> [String: any Sendable] {
            // sabi.py do_send(): named params; coins as {transactionid, index} (OutPointAsTxoRefJsonConverter is case-insensitive).
            var p: [String: any Sendable] = [
                "payments": [["sendto": address, "amount": sats, "label": label, "subtractFee": subtractFee] as [String: any Sendable]],
                "coins": coins.map { ["transactionid": $0.txid, "index": $0.index] as [String: any Sendable] },
                "password": password,
            ]
            fee.forEach { p[$0] = $1 }
            return p
        }
        Task {
            // `build` first: it needs the same passphrase and signing ability as `send`, and the hex gives the exact fee.
            await mirror.perform("Build transaction") { pw in
                let hex: String = try await mirror.rpc.call("build", params: params(pw), wallet: mirror.wallet)
                let parsed = Bitcoin.inspect(hex: hex)
                let byId = Dictionary(uniqueKeysWithValues: coins.map { ($0.id, $0.amount) })
                let inputs = parsed?.inputs.compactMap { byId[$0] }
                let fee = inputs.flatMap { $0.count == parsed?.inputs.count ? $0.reduce(0, +) - parsed!.outputs : nil }
                review = Review(address: address, sats: sats, fee: fee, inputs: parsed?.inputs.count ?? coins.count, toxic: toxic, inCoinjoin: false, onlyPrivate: onlyPrivate, password: pw) { pw in
                    let result: SendResult = try await mirror.rpc.call("send", params: params(pw), wallet: mirror.wallet)
                    sent = result.txid
                    return nil
                }
                return nil
            }
        }
    }
}

// Step 4: the compact summary the guide asks for, anything to notice (toxic merge) highlighted, Face ID as the final consent.
struct ReviewView: View {
    let mirror: Mirror
    let review: SendView.Review
    @State private var authError: String?

    var body: some View {
        List {
            Section(review.inCoinjoin ? "Private payment" : "Transaction") {
                LabeledContent("To") { Text(mirror.spaced(review.address)).font(.footnote.monospaced()).multilineTextAlignment(.trailing).frosted(mirror.discreet) }
                LabeledContent("Amount") { Amount(sats: review.sats, mirror: mirror) }
                if let usd = mirror.usd(review.sats) { LabeledContent("Value") { Text(usd).frosted(mirror.discreet) } }
                if review.inCoinjoin {
                    LabeledContent("Mining fee", value: "None separately; paid within the round")
                } else {
                    LabeledContent("Mining fee") { if let fee = review.fee { Amount(sats: fee, mirror: mirror) } else { Text("Unknown") } }
                    LabeledContent("Coins spent", value: review.onlyPrivate ? "\(review.inputs), all private" : "\(review.inputs)")
                }
            }
            .wasabiRows()
            if review.toxic {
                Label { Text("This spend combines private and non-private coins, which undoes their privacy. Choose More private on the Privacy screen to avoid it.") } icon: { Icon(.warning) }
                    .foregroundStyle(Wasabi.warning)
                    .wasabiRows()
            }
            if let authError { Text(authError).foregroundStyle(Wasabi.error).wasabiRows() }
            if mirror.info?.isWatchOnly == true, mirror.info?.isHardwareWallet == true, !review.inCoinjoin {
                // Wasabi signs through the device on the computer ("no Trezor device found" when it is unplugged).
                Label { Text("Your hardware wallet signs this payment. Connect it to your computer and confirm on the device after Face ID.") } icon: { Icon(.wallet) }
                    .font(.footnote).foregroundStyle(.secondary).wasabiRows()
            }
        }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: review.inCoinjoin ? "Confirm and queue" : "Confirm and send", system: "faceid", disabled: mirror.busy) { confirm() }
        }
    }

    private func confirm() {
        Task {
            do {
                guard try await LAContext().evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Send \(Bitcoin.btc(review.sats)) BTC") else { return }
            } catch {
                authError = error.localizedDescription
                return
            }
            await mirror.perform(review.inCoinjoin ? "Private payment" : "Send", password: review.password, review.send)
        }
    }
}
