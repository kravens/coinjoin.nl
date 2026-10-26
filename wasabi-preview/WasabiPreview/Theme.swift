import CoreImage.CIFilterBuiltins
import SwiftUI

// Desktop Wasabi's palette, WalletWasabi.Fluent/Styles/Themes/{Base,Dark,Light}.axaml.
enum Wasabi {
    static let accent = Color(dark: 0x29903B, light: 0x1AB248) // SystemAccentColor
    static let region = Color(dark: 0x1F1F1F, light: 0xFFFFFF) // RegionColor, the window background
    static let tile = Color(dark: 0x262626, light: 0xEFEFEF) // TileRegionColor
    static let privacyStrong = Color(dark: 0x5A8F4B, light: 0x7BA170) // PrivacyLevelStrongBrush
    static let privacyMedium = Color(dark: 0x536A4B, light: 0xB0C7A8) // PrivacyLevelMediumBrush
    static let privacyNone = Color(dark: 0x383838, light: 0xE0E0E0) // PrivacyLevelNoneBrush
    static let positive = Color(hex: 0x58A553) // AmountControl "+" run
    static let negative = Color(hex: 0xB43434) // AmountControl "-" run
    static let warning = Color(hex: 0xDDA73E) // WarningMessageForeground
    static let error = Color(hex: 0xDD4343) // DismissColor
    static let orangeCoin = Color(hex: 0xF2994A) // OrangeCoinColor, also the excluded-coin indicator
    static let tor = Color(dark: 0xB58BD8, light: 0x7D4698) // Tor Project purple, lightened on dark for contrast
    static let tailscale = accent // Tailscale's mark is monochrome; Wasabi green says "link is up"
    static var logoGradient: LinearGradient { // LogoAccentColor
        LinearGradient(colors: [Color(hex: 0x00FF4D), Color(hex: 0x17C04A)], startPoint: .topTrailing, endPoint: .bottomLeading)
    }
}

// Wasabi: the desktop look with glass over a green glow. Black: OLED-friendly, pure black everywhere it can be, so most
// pixels stay off; tiles are near-black cards instead of glass.
enum AppTheme: String, CaseIterable {
    case wasabi, black
}

extension EnvironmentValues {
    @Entry var appTheme: AppTheme = .wasabi
}

extension Wasabi {
    static func window(_ theme: AppTheme) -> Color { theme == .black ? .black : region }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double(hex >> 16 & 0xFF) / 255, green: Double(hex >> 8 & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }

    init(dark: UInt32, light: UInt32) {
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(Color(hex: dark)) : UIColor(Color(hex: light)) })
    }
}

// SVG / Avalonia path mini-language: M L H V C S Q T Z, absolute and relative, implicit repeats, exponents, "F1" fill rule.
struct SVGPath: Sendable {
    enum Element: Sendable {
        case move(CGPoint), line(CGPoint), curve(CGPoint, CGPoint, CGPoint), quad(CGPoint, CGPoint), close
    }

    let elements: [Element]
    let evenOdd: Bool // Avalonia's default; "F1" switches to nonzero
    let bounds: CGRect

    init(_ data: String) {
        let s = Array(data.utf8)
        var i = 0
        var command: UInt8 = 0, previous: UInt8 = 0
        var current = CGPoint.zero, start = CGPoint.zero, control = CGPoint.zero
        var elements: [Element] = []
        var evenOdd = true

        func skipSeparators() {
            while i < s.count, s[i] == UInt8(ascii: " ") || s[i] == UInt8(ascii: ",") || s[i] == UInt8(ascii: "\n") || s[i] == UInt8(ascii: "\t") || s[i] == UInt8(ascii: "\r") { i += 1 }
        }
        func number() -> CGFloat? {
            skipSeparators()
            let begin = i
            if i < s.count, s[i] == UInt8(ascii: "+") || s[i] == UInt8(ascii: "-") { i += 1 }
            var dot = false
            while i < s.count {
                let c = s[i]
                if c >= UInt8(ascii: "0"), c <= UInt8(ascii: "9") {
                    i += 1
                } else if c == UInt8(ascii: "."), !dot { // "1.5.3" is 1.5 then .3
                    dot = true; i += 1
                } else if c == UInt8(ascii: "e") || c == UInt8(ascii: "E") {
                    i += 1
                    if i < s.count, s[i] == UInt8(ascii: "+") || s[i] == UInt8(ascii: "-") { i += 1 }
                } else {
                    break
                }
            }
            guard i > begin, let value = Double(String(decoding: s[begin..<i], as: UTF8.self)) else { i = begin; return nil }
            return value
        }
        func point(_ relative: Bool) -> CGPoint? {
            guard let x = number(), let y = number() else { return nil }
            return relative ? CGPoint(x: current.x + x, y: current.y + y) : CGPoint(x: x, y: y)
        }
        // S and T mirror the previous control point, but only right after a curve of the same family.
        func reflected(after family: String) -> CGPoint {
            family.utf8.contains(previous) ? CGPoint(x: 2 * current.x - control.x, y: 2 * current.y - control.y) : current
        }

        parse: while i < s.count {
            skipSeparators()
            guard i < s.count else { break }
            let c = s[i]
            if (c | 0x20) >= UInt8(ascii: "a"), (c | 0x20) <= UInt8(ascii: "z") {
                command = c; i += 1
            } else if command == 0 || command | 0x20 == UInt8(ascii: "z") {
                break // coordinates with nothing to apply them to
            }
            let relative = command >= UInt8(ascii: "a")
            switch command | 0x20 {
            case UInt8(ascii: "f"):
                guard let rule = number() else { break parse }
                evenOdd = rule == 0
            case UInt8(ascii: "m"):
                guard let p = point(relative) else { break parse }
                elements.append(.move(p)); current = p; start = p
                command = relative ? UInt8(ascii: "l") : UInt8(ascii: "L") // further pairs are line-tos
            case UInt8(ascii: "l"):
                guard let p = point(relative) else { break parse }
                elements.append(.line(p)); current = p
            case UInt8(ascii: "h"):
                guard let x = number() else { break parse }
                current.x = relative ? current.x + x : x; elements.append(.line(current))
            case UInt8(ascii: "v"):
                guard let y = number() else { break parse }
                current.y = relative ? current.y + y : y; elements.append(.line(current))
            case UInt8(ascii: "c"):
                guard let c1 = point(relative), let c2 = point(relative), let p = point(relative) else { break parse }
                elements.append(.curve(c1, c2, p)); control = c2; current = p
            case UInt8(ascii: "s"):
                let c1 = reflected(after: "CcSs")
                guard let c2 = point(relative), let p = point(relative) else { break parse }
                elements.append(.curve(c1, c2, p)); control = c2; current = p
            case UInt8(ascii: "q"):
                guard let c1 = point(relative), let p = point(relative) else { break parse }
                elements.append(.quad(c1, p)); control = c1; current = p
            case UInt8(ascii: "t"):
                let c1 = reflected(after: "QqTt")
                guard let p = point(relative) else { break parse }
                elements.append(.quad(c1, p)); control = c1; current = p
            case UInt8(ascii: "z"):
                elements.append(.close); current = start
            default:
                break parse // arcs are not used by Wasabi's icons; selfCheck asserts every icon parsed
            }
            previous = command
        }

        self.elements = elements
        self.evenOdd = evenOdd
        bounds = Self.path(elements, transform: .identity).boundingRect
    }

    // The geometry scaled uniformly into `rect` and centred, like Avalonia's PathIcon. `canvas` lets several
    // geometries share one frame (the logo mark and its accent square).
    func path(in rect: CGRect, canvas: CGRect? = nil) -> Path {
        let box = canvas ?? bounds
        guard box.width > 0, box.height > 0 else { return Path() }
        let scale = min(rect.width / box.width, rect.height / box.height)
        let transform = CGAffineTransform(translationX: rect.midX - box.midX * scale, y: rect.midY - box.midY * scale).scaledBy(x: scale, y: scale)
        return Self.path(elements, transform: transform)
    }

    private static func path(_ elements: [Element], transform t: CGAffineTransform) -> Path {
        var path = Path()
        for element in elements {
            switch element {
            case .move(let p): path.move(to: p.applying(t))
            case .line(let p): path.addLine(to: p.applying(t))
            case .curve(let a, let b, let p): path.addCurve(to: p.applying(t), control1: a.applying(t), control2: b.applying(t))
            case .quad(let a, let p): path.addQuadCurve(to: p.applying(t), control: a.applying(t))
            case .close: path.closeSubpath()
            }
        }
        return path
    }
}

extension WasabiIcon {
    private static let geometries = Dictionary(uniqueKeysWithValues: allCases.map { ($0, SVGPath($0.rawValue)) })
    var geometry: SVGPath { Self.geometries[self]! }

    // Template image for the slots that only take an Image: tab bar and toolbar.
    @MainActor private static var images: [WasabiIcon: UIImage] = [:]
    @MainActor var uiImage: UIImage {
        if let cached = Self.images[self] { return cached }
        let size = CGSize(width: 24, height: 24)
        let image = UIGraphicsImageRenderer(size: size).image { context in
            context.cgContext.addPath(geometry.path(in: CGRect(origin: .zero, size: size).insetBy(dx: 1, dy: 1)).cgPath)
            context.cgContext.fillPath(using: geometry.evenOdd ? .evenOdd : .winding)
        }.withRenderingMode(.alwaysTemplate)
        Self.images[self] = image
        return image
    }

    #if DEBUG
    static func selfCheck() {
        let square = SVGPath("M0 0 H10 V10 H0 Z")
        assert(square.bounds == CGRect(x: 0, y: 0, width: 10, height: 10) && square.evenOdd && square.elements.count == 5)
        assert(SVGPath("m1,1 l2 0 0 2 z").bounds == CGRect(x: 1, y: 1, width: 2, height: 2)) // relative, implicit repeat
        assert(SVGPath("M1e1 0L2E0,1").bounds == CGRect(x: 2, y: 0, width: 8, height: 1)) // exponents
        assert(SVGPath("M0 0C0 0 10 0 10 0S20 0 20 0Q25 0 30 0T40 0").bounds.width == 40) // C/S/Q/T chain
        assert(SVGPath("F1 M0,0 L5,0 L5,5 Z").evenOdd == false)
        assert(SVGPath("M0 0 L1 1 A1 1 0 0 1 2 2").elements.count == 2) // stops at an unsupported command instead of looping
        assert(SVGPath("M0 0 L10 10").path(in: CGRect(x: 0, y: 0, width: 5, height: 10)).boundingRect == CGRect(x: 0, y: 2.5, width: 5, height: 5)) // fit, centred
        for icon in allCases { assert(!icon.geometry.elements.isEmpty && icon.geometry.bounds.width > 0, "\(icon) did not parse") }
        assert(WasabiIcon.logo.geometry.evenOdd == false) // the logo carries "F1"
    }
    #endif
}

struct IconShape: Shape {
    let icon: WasabiIcon
    var canvas: CGRect? = nil

    func path(in rect: CGRect) -> Path { icon.geometry.path(in: rect, canvas: canvas) }
}

// A Wasabi icon as a template: fills with the current foreground style, scales with Dynamic Type.
struct Icon: View {
    let icon: WasabiIcon
    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 17

    init(_ icon: WasabiIcon, size: CGFloat = 17) {
        self.icon = icon
        _size = ScaledMetric(wrappedValue: size, relativeTo: .body)
    }

    var body: some View {
        IconShape(icon: icon).fill(style: FillStyle(eoFill: icon.geometry.evenOdd)).frame(width: size, height: size)
    }
}

// wasabi_logo_dynamic: the mark in LogoBaseColor (primary) with the accent square in the LogoAccentColor gradient.
struct WasabiLogo: View {
    private static let canvas = WasabiIcon.logo.geometry.bounds.union(WasabiIcon.logoAccent.geometry.bounds)

    var body: some View {
        ZStack {
            IconShape(icon: .logo, canvas: Self.canvas).fill(.primary)
            IconShape(icon: .logoAccent, canvas: Self.canvas).fill(Wasabi.logoGradient)
        }
        .aspectRatio(Self.canvas.width / Self.canvas.height, contentMode: .fit)
        .accessibilityLabel("Wasabi")
    }
}

// Something for the glass to refract: Wasabi's window colour, a soft accent glow in the shape of the logo, and a
// whisper of noise so the glow does not band on OLED.
struct Backdrop: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.appTheme) private var theme

    var body: some View {
        if theme == .black {
            Color.black.ignoresSafeArea().accessibilityHidden(true)
        } else {
            glow
        }
    }

    private var glow: some View {
        ZStack(alignment: .topTrailing) {
            Wasabi.region
            IconShape(icon: .logo).fill(Wasabi.accent)
                .frame(width: 440, height: 340)
                .blur(radius: 70)
                .opacity(scheme == .dark ? 0.32 : 0.22)
                .offset(x: 120, y: -80)
            Image(uiImage: Self.noise).resizable(resizingMode: .tile).opacity(scheme == .dark ? 0.035 : 0.02).blendMode(.overlay)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }

    @MainActor private static let noise: UIImage = {
        let filter = CIFilter.colorControls()
        filter.inputImage = CIFilter.randomGenerator().outputImage
        filter.saturation = 0
        let image = filter.outputImage!.cropped(to: CGRect(x: 0, y: 0, width: 128, height: 128))
        return UIImage(cgImage: CIContext().createCGImage(image, from: image.extent)!)
    }()
}

// Desktop Wasabi's TileControl: small bold caption, 15 pt corners; here on Liquid Glass. The caption is always centred;
// hero tiles (balance, privacy) centre their content too, tiles made of label/value rows keep them edge to edge.
struct Tile<Content: View>: View {
    let title: String
    let alignment: HorizontalAlignment
    let content: Content

    init(_ title: String, alignment: HorizontalAlignment = .leading, @ViewBuilder content: () -> Content) {
        self.title = title
        self.alignment = alignment
        self.content = content()
    }

    var body: some View {
        VStack(alignment: alignment, spacing: 10) {
            Text(title).font(.caption2.weight(.bold)).foregroundStyle(.secondary).frame(maxWidth: .infinity)
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity)
        .tileSurface()
    }
}

// Glass on the Wasabi theme; on Black a near-black card, since glass over black still lights pixels.
struct TileSurface: ViewModifier {
    @Environment(\.appTheme) private var theme

    func body(content: Content) -> some View {
        if theme == .black {
            // A hairline got clipped at the corners by List rows; a near-black fill reads as a card and keeps pixels almost off.
            content.background(Color(white: 0.055), in: .rect(cornerRadius: 15))
        } else {
            content.glassEffect(.regular, in: .rect(cornerRadius: 15))
        }
    }
}

// Discreet mode: digits are already starred; on top the text is blurred under a frosted capsule.
struct Frosted: ViewModifier {
    let on: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        content
            .blur(radius: on ? 2.5 : 0)
            .overlay { if on { Color.clear.glassEffect(.regular, in: .capsule).padding(.horizontal, -6).padding(.vertical, -2) } }
            .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: on)
    }
}

extension View {
    func frosted(_ on: Bool) -> some View { modifier(Frosted(on: on)) }

    // Translucent list rows so the backdrop glow shows through, matching the glass tiles. `card` rows stay visible on Black.
    func wasabiRows(card: Bool = false) -> some View { modifier(WasabiRows(card: card)) }

    func tileSurface() -> some View { modifier(TileSurface()) }

    // A glass tile living in a List row: no row chrome, the tile draws itself.
    func glassRow() -> some View { listRowBackground(Color.clear).listRowInsets(EdgeInsets()).listRowSeparator(.hidden) }

    // Forms: drag the list down or tap Done to put the keyboard away.
    func dismissableKeyboard() -> some View {
        scrollDismissesKeyboard(.interactively)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil) }
                }
            }
    }

    // One screen of a flow sheet: backdrop, inline title, and the iOS 26 Close circle at the top trailing. Pushed steps keep
    // their Back button; Close leaves the whole flow. `close` must come from the sheet root, a pushed view's dismiss only pops.
    func flowScreen(_ title: String, close: @escaping () -> Void) -> some View {
        scrollContentBackground(.hidden)
            .background { Backdrop() }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close", systemImage: "xmark", action: close) } }
    }

    // The sheet root of a flow: the drag indicator says "pull down to go home", alerts and the busy spinner sit above the sheet.
    func flowSheet(_ mirror: Mirror) -> some View {
        presentationDragIndicator(.visible)
            .tint(Wasabi.accent) // sheets do not inherit the home's tint
            .wasabiAlerts(mirror)
            .overlay { if mirror.busy { ProgressView().padding().glassEffect() } }
    }
}

// The one primary action of a screen, pinned above the home indicator so it never scrolls under a bar.
struct PrimaryAction: View {
    let title: String
    var icon: WasabiIcon? = nil
    var system: String? = nil
    var disabled = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let icon { Icon(icon) } else if let system { Image(systemName: system) }
                Text(title).fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
        }
        .buttonStyle(.glassProminent)
        .tint(Wasabi.accent)
        .disabled(disabled)
        .padding(.horizontal)
        .padding(.bottom, 8)
    }
}

// Wasabi's AmountControl: leading "0.00…" dimmed, significant digits bold, an optional sign in colour.
struct Amount: View {
    let sats: Int
    let mirror: Mirror
    var signed = false
    var font: Font = .body

    var body: some View {
        let text = mirror.btc(abs(sats)) // "0.00123456 BTC" or "*.******** BTC"
        let dim = text.prefix { "0. ".contains($0) } // nothing is dimmed from 1 BTC up, as on the desktop
        let sign = signed ? Text(sats > 0 ? "+" : sats < 0 ? "-" : "").foregroundStyle(sats > 0 ? Wasabi.positive : Wasabi.negative) : Text(verbatim: "")
        return Text("\(sign)\(Text(dim).foregroundStyle(.secondary))\(Text(text.dropFirst(dim.count)).bold())")
            .font(font)
            .monospacedDigit()
            .frosted(mirror.discreet)
    }
}

extension Mirror {
    // Wasabi's PrivacyLevel per coin (SmartCoinExtensions.GetPrivacyLevel): private at the target, semi-private from 2.
    func privacyTier(_ score: Double) -> (icon: WasabiIcon, color: Color, name: String) {
        if score >= Double(target) { return (.shieldTick, Wasabi.privacyStrong, "private") }
        if score >= 2 { return (.shield, Wasabi.privacyMedium, "semi-private") }
        return (.shield, Color.secondary, "non-private")
    }
}

// Tor's purple onion while Arti bootstraps (ring fills with progress) or builds the circuit to the onion service
// (indeterminate arc). Occasional and 5 to 30 s long, so a calm loop; Reduce Motion swaps the spin for a slow fade.
struct OnionLoader: View {
    let progress: Int? // nil: bootstrapped, reaching the onion service
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().stroke(Wasabi.tor.opacity(0.2), lineWidth: 3)
                if let progress {
                    Circle().trim(from: 0, to: CGFloat(progress) / 100)
                        .stroke(Wasabi.tor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: progress)
                } else {
                    TimelineView(.animation) { context in
                        let t = context.date.timeIntervalSinceReferenceDate
                        Circle().trim(from: 0, to: 0.28)
                            .stroke(Wasabi.tor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            .rotationEffect(.degrees(reduceMotion ? -90 : t.truncatingRemainder(dividingBy: 1.4) / 1.4 * 360 - 90))
                            .opacity(reduceMotion ? 0.55 + 0.45 * sin(t * .pi / 1.5) : 1)
                    }
                }
                Icon(.tor, size: 22).foregroundStyle(Wasabi.tor)
            }
            .frame(width: 44, height: 44)
            VStack(alignment: .leading, spacing: 2) {
                Text(progress.map { "Connecting to Tor, \($0)%" } ?? "Reaching your computer over Tor")
                Text(progress == nil ? "Building a private circuit to the onion service" : "Tor runs inside this app")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

// Copy with proof: the icon and label flip to a check for 1.5 s with a success haptic, so it's clear the address or
// txid actually landed on the clipboard. Occasional action, so a short crossfade; Reduce Motion keeps only the haptic.
struct CopyButton: View {
    let title: String
    let text: String
    @State private var copied = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(_ title: String = "Copy", text: String) {
        self.title = title
        self.text = text
    }

    var body: some View {
        Button {
            UIPasteboard.general.string = text
            copied = true
        } label: {
            Label {
                Text(copied ? "Copied" : title).contentTransition(.opacity)
            } icon: {
                ZStack {
                    Icon(.copy).opacity(copied ? 0 : 1)
                    Icon(.check).opacity(copied ? 1 : 0).scaleEffect(copied || reduceMotion ? 1 : 0.8)
                }
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: copied)
        .sensoryFeedback(.success, trigger: copied) { _, now in now }
        .task(id: copied) {
            guard copied else { return }
            try? await Task.sleep(for: .seconds(1.5))
            copied = false
        }
    }
}

struct WasabiRows: ViewModifier {
    let card: Bool
    @Environment(\.appTheme) private var theme

    func body(content: Content) -> some View {
        // Black keeps plain rows pure black; the home's two cards need an edge, so they get the tiles' near-black.
        content.listRowBackground(theme == .black ? Color(white: card ? 0.055 : 0) : Wasabi.tile.opacity(0.72))
    }
}

// First load with nothing to show yet: the purple onion for Tor, the Tailscale mark otherwise.
struct ConnectingLoader: View {
    let transport: Server.Transport
    let progress: Int?

    var body: some View {
        if transport == .onion {
            OnionLoader(progress: progress)
        } else {
            HStack(spacing: 14) {
                LinkGlyph(onion: false, color: Wasabi.tailscale, pulsing: true).frame(width: 44, height: 44)
                Text("Reaching your computer over Tailscale")
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

// Tailscale's mark: a 3 by 3 grid of dots with the T (middle row and bottom centre) solid, the rest faded.
struct TailscaleMark: View {
    var body: some View {
        Canvas { context, size in
            let step = min(size.width, size.height) / 3
            for row in 0..<3 {
                for col in 0..<3 {
                    var dot = context
                    dot.opacity = row == 1 || (row == 2 && col == 1) ? 1 : 0.35
                    let frame = CGRect(x: Double(col) * step, y: Double(row) * step, width: step, height: step).insetBy(dx: step * 0.12, dy: step * 0.12)
                    dot.fill(Circle().path(in: frame), with: .foreground)
                }
            }
        }
        .accessibilityHidden(true)
    }
}

// The link mark itself: onion or Tailscale dots, breathing while connecting or retrying. Reduce Motion holds it dimmed.
struct LinkGlyph: View {
    let onion: Bool
    let color: Color
    let pulsing: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: !pulsing || reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let breath = reduceMotion ? 0.6 : 0.45 + 0.55 * (0.5 + 0.5 * sin(t * 2 * .pi / 1.6))
            Group {
                if onion { Icon(.tor, size: 20) } else { TailscaleMark().frame(width: 17, height: 17) }
            }
            .foregroundStyle(color)
            .opacity(pulsing ? breath : 1)
        }
    }
}

// The link light and its facts, at the top of the side menu: purple onion (Tor) or green Tailscale mark when the last
// call worked, grey after a timeout, breathing while connecting or retrying. The home keeps its last data meanwhile.
struct LinkStatus: View {
    let mirror: Mirror

    private var onion: Bool { mirror.server.transport == .onion }
    private var name: String { onion ? "Tor" : "Tailscale" }
    private var color: Color { mirror.link == .failed ? .gray : onion ? Wasabi.tor : Wasabi.tailscale }
    private var pulsing: Bool { mirror.link == .connecting || (mirror.link == .failed && mirror.refreshing) }
    private var state: String {
        switch mirror.link {
        case .connecting: mirror.torProgress.map { "Connecting, \($0)%" } ?? "Connecting"
        case .connected: "Connected"
        case .failed: mirror.refreshing ? "Timed out, retrying" : "Timed out"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label { Text(name).font(.headline) } icon: { LinkGlyph(onion: onion, color: color, pulsing: pulsing) }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(name) link: \(state)")
            Text(state).foregroundStyle(.secondary)
            if let error = mirror.linkError { Text(error).font(.caption).foregroundStyle(.secondary) }
            if let updated = mirror.updated {
                Text("Last update \(updated, style: .relative) ago").font(.caption).foregroundStyle(.secondary)
            }
            Button("Retry now") { Task { await mirror.refresh() } }
                .buttonStyle(.glass)
                .disabled(mirror.refreshing)
        }
    }
}

// A UIKit screen-edge pan for SwiftUI. It only begins inside the system's edge strip and fails for vertical movement, and
// UIKit makes the List's scroll and row-swipe pans wait for it to fail first, something a SwiftUI DragGesture laid over
// a List never gets. `x` is the finger's horizontal travel in points, positive to the right.
struct EdgePan: UIGestureRecognizerRepresentable {
    let edge: UIRectEdge
    var enabled = true
    let changed: (_ x: CGFloat) -> Void
    let ended: (_ x: CGFloat, _ velocity: CGFloat) -> Void

    static let commit: CGFloat = 120 // 40 % of the 300 pt drawer; the scanner swipe uses the same feel

    func makeUIGestureRecognizer(context: Context) -> UIScreenEdgePanGestureRecognizer {
        let pan = UIScreenEdgePanGestureRecognizer()
        pan.edges = edge
        return pan
    }

    func updateUIGestureRecognizer(_ pan: UIScreenEdgePanGestureRecognizer, context: Context) { pan.isEnabled = enabled }

    func handleUIGestureRecognizerAction(_ pan: UIScreenEdgePanGestureRecognizer, context: Context) {
        let x = pan.translation(in: pan.view).x
        switch pan.state {
        case .changed: changed(x)
        case .ended: ended(x, pan.velocity(in: pan.view).x)
        case .cancelled, .failed: ended(0, 0)
        default: break
        }
    }

    // WWDC18 "Designing Fluid Interfaces": where the travel would coast to at UIScrollView's normal deceleration, so a
    // short flick commits and a long drag pulled back does not.
    static func projected(_ x: CGFloat, velocity: CGFloat) -> CGFloat {
        let rate = UIScrollView.DecelerationRate.normal.rawValue
        return x + velocity / 1000 * rate / (1 - rate)
    }
}

extension View {
    func sideMenu<Menu: View>(open: Binding<Bool>, edgeSwipe: Bool, @ViewBuilder content: @escaping () -> Menu) -> some View {
        modifier(SideMenu(open: open, edgeSwipe: edgeSwipe, menu: content))
    }
}

// A left-hand menu over the whole screen. ≡ or a pan in from the left screen edge opens it (on the home only, so pushed
// screens keep the system back swipe); it follows the finger and commits past 40 % or on a flick. Swiping it back,
// tapping the dimmed area or picking an item closes it. Reduce Motion fades instead of sliding. VoiceOver treats the
// open panel as modal, lands inside it and closes it with the escape gesture. Liquid Glass on Wasabi, near-black on Black.
struct SideMenu<Menu: View>: ViewModifier {
    @Binding var open: Bool
    let edgeSwipe: Bool
    @ViewBuilder let menu: () -> Menu
    @State private var opening: CGFloat = 0 // the edge pan while closed; the panel follows it in from off-screen
    @GestureState(resetTransaction: Transaction(animation: .spring(duration: 0.3))) private var closing: CGFloat = 0
    @Environment(\.appTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AccessibilityFocusState private var focused: Bool
    private let width: CGFloat = 300

    func body(content: Content) -> some View {
        content
            .gesture(EdgePan(edge: .left, enabled: edgeSwipe && !open) { opening = max(0, $0) } ended: { x, velocity in
                withAnimation(.spring(duration: 0.3)) {
                    if EdgePan.projected(x, velocity: velocity) > EdgePan.commit { open = true }
                    opening = 0
                }
            })
            .overlay { drawer }
    }

    private var drawer: some View {
        ZStack(alignment: .leading) {
            if open {
                Color.black.opacity(0.35).ignoresSafeArea()
                    .onTapGesture { open = false }
                    .transition(.opacity)
                    .accessibilityHidden(true)
            }
            if open || (!reduceMotion && opening > 0) {
                panel
                    .offset(x: reduceMotion ? 0 : open ? min(0, closing) : opening - width)
                    .transition(reduceMotion ? .opacity : .move(edge: .leading))
                    .gesture(DragGesture(minimumDistance: 15)
                        .updating($closing) { value, state, _ in state = min(0, value.translation.width) }
                        .onEnded { if EdgePan.projected($0.translation.width, velocity: $0.velocity.width) < -EdgePan.commit { open = false } })
                    .accessibilityAddTraits(.isModal)
                    .accessibilityAction(.escape) { open = false }
                    .onAppear { focused = true }
            }
        }
        // Fill the overlay. Before, a closed menu held only a 16 pt strip, so the ZStack was 16 pt wide and the overlay
        // centred it mid-screen: the "edge" strip never touched the edge, on any device.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .animation(reduceMotion ? .easeOut(duration: 0.2) : .spring(duration: 0.35), value: open)
    }

    private var panel: some View {
        ScrollView {
            menu()
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .accessibilityFocused($focused)
        }
        .scrollBounceBehavior(.basedOnSize)
        .frame(width: width)
        .frame(maxHeight: .infinity)
        .background {
            let shape = UnevenRoundedRectangle(bottomTrailingRadius: 28, topTrailingRadius: 28)
            Group {
                if theme == .black {
                    shape.fill(Color(white: 0.055))
                        .overlay { shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.5) }
                } else {
                    Color.clear.glassEffect(.regular, in: shape)
                }
            }
            .ignoresSafeArea()
        }
    }
}
