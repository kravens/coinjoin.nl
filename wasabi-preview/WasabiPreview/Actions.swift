import LocalAuthentication
import SwiftUI

// Receive as bitcoin.design's payment request: who is paying and an optional amount first, then one big QR with copy and share.
struct ReceiveView: View {
    let mirror: Mirror
    @State private var label = ""
    @State private var amount = ""
    @State private var unit = "BTC"
    @State private var taproot = true
    @State private var address: String?
    @State private var brightness: CGFloat?
    @Environment(\.dismiss) private var dismiss

    init(mirror: Mirror) {
        self.mirror = mirror
        #if DEBUG
        if Screen.current == .receiveAddress { _label = State(initialValue: "Alice, concert tickets"); _amount = State(initialValue: "0.001"); _address = State(initialValue: Screen.demoTaprootAddress) }
        #endif
    }

    private var sats: Int? { unit == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) }
    private var uri: String? { address.map { Bitcoin.uri(address: $0, sats: sats, label: label) } }
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
                TextField("Who is paying you", text: $label)
                HStack {
                    TextField("Amount (optional)", text: $amount).keyboardType(.decimalPad)
                    Picker("Unit", selection: $unit) { Text("BTC").tag("BTC"); Text("sats").tag("sats") }.pickerStyle(.segmented).fixedSize()
                }
                if let sats, let usd = mirror.usd(sats) { Text(usd).font(.caption).foregroundStyle(.secondary).frosted(mirror.discreet) }
                Toggle("Taproot address", isOn: $taproot)
            } footer: {
                Text("Wasabi labels every address so you know later where the coins came from. Leave the amount empty and the sender fills it in.")
            }
            .wasabiRows()
            .walletGate(mirror)
            if mirror.walletError == nil {
                Section {
                    PrivacySlider(mirror: mirror, explanation: explanation)
                } header: {
                    Text("After the money arrives")
                }
                .wasabiRows()
            }
        }
        .dismissableKeyboard()
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: "Create address", icon: .receive, disabled: label.trimmingCharacters(in: .whitespaces).isEmpty || mirror.busy) {
                let (label, taproot) = (label, taproot)
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
        ScrollView {
            VStack(spacing: 14) {
                VStack(spacing: 14) {
                    if let qr = Bitcoin.qr(uri) {
                        // Wasabi draws the QR on a white card; keep it scannable in dark mode and under discreet frosting.
                        Image(uiImage: qr).interpolation(.none).resizable().scaledToFit()
                            .padding(12).background(.white, in: .rect(cornerRadius: 12))
                            .frame(maxWidth: 260)
                            .frosted(mirror.discreet)
                            .accessibilityLabel("QR code of the payment request")
                    }
                    Text(mirror.mask(address)).font(.footnote.monospaced()).textSelection(.enabled).multilineTextAlignment(.center)
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
                    LabeledContent("From", value: label)
                    if let sats, sats > 0 { LabeledContent("Amount") { Amount(sats: sats, mirror: mirror) } }
                    LabeledContent("Address type", value: taproot ? "Taproot" : "SegWit")
                }
                .font(.subheadline)
                Text("Each address is used once. Create a new one for the next payment.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal)
            }
            .padding()
        }
        // Done also arms the coinjoin for Balanced and More private; autoCoinjoin is quiet and does nothing for Lower fees.
        .safeAreaInset(edge: .bottom) { PrimaryAction(title: "Done", system: "checkmark") { Task { await mirror.autoCoinjoin() }; dismiss() } }
        // bitcoin.design: brighten the screen while a QR is shown so it scans first time.
        .onAppear { brightness = screen?.brightness; screen?.brightness = 1 }
        .onDisappear { if let brightness { screen?.brightness = brightness } }
    }

    // What each stop does once a payment lands, in the words of what the RPC can really do.
    private var explanation: String {
        let target = mirror.target
        switch mirror.privacyStop {
        case .cheap:
            return "Nothing more happens and no coinjoin fees are paid. The coins stay as they arrive; you can make them private later from the Privacy screen."
        case .balanced:
            if mirror.info?.isAutoCoinjoin == true { return "Wasabi on your computer already starts making coins private by itself and stops once every coin reaches the privacy target (\(target))." }
            if mirror.info?.canAutoCoinjoin == false { return "This wallet's keys are on a device, so a coinjoin has to be started by hand from the Privacy screen; Wasabi then asks the device." }
            return "When the money arrives while this app is open, Wasabi starts making it private and stops once every coin reaches the privacy target (\(target)). Each round costs a share of its mining fee."
        case .private:
            if mirror.info?.isAutoCoinjoin == true { return "Wasabi on your computer already keeps making coins private by itself. The privacy target (\(target)) is set there." }
            if mirror.info?.canAutoCoinjoin == false { return "This wallet's keys are on a device, so a coinjoin has to be started by hand from the Privacy screen; Wasabi then asks the device." }
            return "Wasabi keeps making private whatever arrives, now and later, and stays on. The privacy target (\(target)) itself is set in Wasabi on your computer."
        }
    }
}

// Send as bitcoin.design's step-by-step flow: where to, how much, how fast, then review, Face ID and a success screen.
struct SendView: View {
    let mirror: Mirror
    @State private var path: [Step] = []
    @State private var address = ""
    @State private var amount = ""
    @State private var unit = "BTC"
    @State private var subtractFee = false
    @State private var label = ""
    @State private var feeTarget = "6"
    @State private var customRate = 1
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
        if let payment {
            _address = State(initialValue: payment.address)
            _label = State(initialValue: payment.label ?? payment.message ?? "")
            if let sats = payment.sats, sats > 0 { _amount = State(initialValue: Bitcoin.btc(sats)) }
            if mirror.ready { _path = State(initialValue: amountValid && !inCoinjoin ? [.amount, .fee] : [.amount]) }
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

    // The slider stop, as far as this wallet allows it: a device-signed wallet cannot pay inside a round (payincoinjoin
    // refuses), so Lower fees falls back to a normal transaction and the copy says so.
    private var inCoinjoin: Bool { mirror.privacyStop == .cheap && mirror.info?.deviceSigns != true }
    private var onlyPrivate: Bool { mirror.privacyStop == .private }
    private var sats: Int? { unit == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) }
    private var spendable: [Coin] { mirror.coins.filter { $0.confirmed && (!onlyPrivate || $0.anonymityScore >= Double(mirror.target)) } }
    private var available: Int { spendable.reduce(0) { $0 + $1.amount } }
    private var notYetPrivate: Int { mirror.coins.filter(\.confirmed).reduce(0) { $0 + $1.amount } - available } // confirmed but not yet private
    private var feeTargets: [String] { mirror.fees.keys.sorted { Int($0) ?? 0 < Int($1) ?? 0 } }
    private var amountValid: Bool { (sats ?? 0) > 0 && (inCoinjoin || subtractFee || sats! <= available) }

    private var explanation: String {
        switch mirror.privacyStop {
        case .cheap:
            mirror.info?.deviceSigns == true
                ? "A hardware device signs this wallet's rounds, so a payment inside a round is not possible. This sends a normal transaction instead."
                : "Paid out of one of Wasabi's next privacy rounds, so there is no separate transaction fee. Slower: usually hours rather than minutes."
        case .balanced:
            "A normal transaction that spends your most private coins first. You are warned before private and non-private coins would be combined."
        case .private:
            "Spends only coins that already reached the privacy target (\(mirror.target)). If they do not cover the amount, wait until more coins are private or choose Balanced."
        }
    }

    var body: some View {
        NavigationStack(path: $path) {
            destination
                .navigationDestination(for: Step.self) { step in
                    switch step {
                    case .amount: amountStep
                    case .fee: feeStep
                    case .review: if let review { ReviewView(mirror: mirror, review: review) { path.append(.success) }.flowScreen("Review") { dismiss() } }
                    case .success: success
                    }
                }
        }
        .flowSheet(mirror)
        .interactiveDismissDisabled(mirror.busy) // a half-finished build or send should not be swiped away
        .sheet(isPresented: $scanning) { ScannerSheet(network: mirror.network, found: apply) }
        .onChange(of: review?.id) { if review != nil { path.append(.review) } }
        // Fee rates may arrive after the view appears; keep the picker on a target Wasabi actually offers.
        .onChange(of: feeTargets, initial: true) {
            if feeTarget != "custom", !feeTargets.contains(feeTarget), let first = feeTargets.first { feeTarget = first }
        }
    }

    // Step 1: where to. Scanning is the primary way in, as the guide suggests; paste and typing are right there too.
    private var destination: some View {
        List {
            Section {
                PrivacySlider(mirror: mirror, explanation: explanation)
            } header: {
                Text("Privacy")
            }
            .wasabiRows()
            .walletGate(mirror)
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
                if let sats, sats > 0, !inCoinjoin, !subtractFee, sats > available {
                    Label { Text(onlyPrivate ? "More than the private balance" : "More than the confirmed balance") } icon: { Icon(.warning, size: 14) }.font(.caption).foregroundStyle(Wasabi.error)
                }
            } footer: {
                if onlyPrivate {
                    Text("Private and ready: \(mirror.btc(available)) in \(spendable.count) coins." + (notYetPrivate > 0 ? " Another \(mirror.btc(notYetPrivate)) is still being made private." : ""))
                } else {
                    Text("Available: \(mirror.btc(available)) in \(spendable.count) confirmed coins")
                }
            }
            .wasabiRows()
            if !inCoinjoin {
                Section {
                    Toggle("Subtract fee from amount", isOn: $subtractFee)
                    Button {
                        unit = "BTC"
                        amount = Bitcoin.btc(available)
                        subtractFee = true
                    } label: {
                        HStack(spacing: 0) { Text("Send all ("); Amount(sats: available, mirror: mirror); Text(verbatim: ")") }
                    }
                } footer: {
                    Text(onlyPrivate ? "Send all spends every private coin and takes the mining fee out of the amount." : "Send all spends every confirmed coin and takes the mining fee out of the amount.")
                }
                .wasabiRows()
            }
        }
        .dismissableKeyboard()
        .onChange(of: unit) { old, new in
            guard let value = old == "BTC" ? Bitcoin.sats(btc: amount) : Int(amount) else { amount = ""; return }
            amount = new == "BTC" ? Bitcoin.btc(value) : String(value)
        }
        .flowScreen("Amount") { dismiss() }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: inCoinjoin ? "Review payment" : "Continue", system: inCoinjoin ? nil : "chevron.right", disabled: !amountValid || mirror.busy) {
                if inCoinjoin { prepare() } else { path.append(.fee) }
            }
        }
    }

    // Step 3: how fast. Wasabi offers block targets; the rough wait time comes first because that is what people choose by.
    private var feeStep: some View {
        List {
            Section {
                Picker("Confirm within", selection: $feeTarget) {
                    ForEach(feeTargets, id: \.self) { target in
                        HStack {
                            Text(Self.wait(blocks: Int(target) ?? 0))
                            Spacer()
                            Text("\(target) blocks · \(mirror.fees[target]!.formatted(.number.precision(.fractionLength(1)))) sat/vB").foregroundStyle(.secondary)
                        }
                        .tag(target)
                    }
                    Text("Custom rate").tag("custom")
                }
                .pickerStyle(.inline)
                .labelsHidden()
                if feeTarget == "custom" { Stepper("\(customRate) sat/vB", value: $customRate, in: 1...500) }
            } header: {
                Text("Confirm within")
            } footer: {
                Text("The exact mining fee is shown on the next screen before anything is sent.")
            }
            .wasabiRows()
        }
        .flowScreen("Fee") { dismiss() }
        .safeAreaInset(edge: .bottom) {
            PrimaryAction(title: "Review transaction", icon: .send, disabled: mirror.busy) { prepare() }
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
                    Text(mirror.mask(review.address)).font(.footnote.monospaced()).multilineTextAlignment(.center).frosted(mirror.discreet)
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
            // Wasabi PayInCoinJoin(address, amount, password); the passphrase is re-checked at the call.
            review = Review(address: address, sats: sats, fee: nil, inputs: 0, toxic: false, inCoinjoin: true, onlyPrivate: false, password: "") { pw in
                let id: String = try await mirror.rpc.call("payincoinjoin", params: [address, sats, pw] as [any Sendable], wallet: mirror.wallet)
                sent = id
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
    let done: () -> Void
    @State private var authError: String?

    var body: some View {
        List {
            Section(review.inCoinjoin ? "Low-fee payment" : "Transaction") {
                LabeledContent("To") { Text(mirror.mask(review.address)).font(.footnote.monospaced()).multilineTextAlignment(.trailing).frosted(mirror.discreet) }
                LabeledContent("Amount") { Amount(sats: review.sats, mirror: mirror) }
                if let usd = mirror.usd(review.sats) { LabeledContent("Value") { Text(usd).frosted(mirror.discreet) } }
                if review.inCoinjoin {
                    LabeledContent("Fee", value: "None on top; paid from a privacy round")
                } else {
                    LabeledContent("Mining fee") { if let fee = review.fee { Amount(sats: fee, mirror: mirror) } else { Text("Unknown") } }
                    LabeledContent("Coins spent", value: review.onlyPrivate ? "\(review.inputs), all private" : "\(review.inputs)")
                }
            }
            .wasabiRows()
            if review.toxic {
                Label { Text("This spend combines private and non-private coins, which undoes their privacy. Choose More private to avoid it.") } icon: { Icon(.warning) }
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
            let sent = await mirror.perform(review.inCoinjoin ? "Pay in coinjoin" : "Send", password: review.password, review.send)
            if sent { done() }
        }
    }
}
