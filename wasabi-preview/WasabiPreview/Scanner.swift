import SwiftUI
import VisionKit

// Camera QR scanner; calls `found` with each newly seen QR payload and keeps scanning, so the sheet decides what counts.
struct Scanner: UIViewControllerRepresentable {
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])], qualityLevel: .balanced, isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let parent: Scanner
        init(_ parent: Scanner) { self.parent = parent }

        func dataScanner(_ scanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems: [RecognizedItem]) {
            guard case .barcode(let code)? = addedItems.first, let payload = code.payloadStringValue else { return }
            parent.found(payload)
        }
    }
}

// Scan to pay: the camera when there is one, the fallback text otherwise; Paste and Close in both cases. Only a payable
// on-chain payment is handed back; anything else is explained in place and scanning goes on. Reduce Motion is honoured
// by the system presentation; the success haptic is the one effect that always plays.
struct ScannerSheet: View {
    let network: Bitcoin.Network?
    let found: (Bitcoin.Payment) -> Void
    @State private var problem: String?
    @State private var done = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    Scanner(found: accept).ignoresSafeArea()
                } else {
                    ContentUnavailableView("Camera unavailable", systemImage: "camera", description: Text("Paste the address or payment link instead."))
                }
            }
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: 14) {
                    if let problem {
                        Label { Text(problem) } icon: { Icon(.warning, size: 14) }
                            .font(.footnote).foregroundStyle(Wasabi.error).multilineTextAlignment(.leading)
                            .padding(12).glassEffect(.regular, in: .rect(cornerRadius: 14))
                            .accessibilityAddTraits(.isStaticText)
                    }
                    // PasteButton reads the clipboard without the "allow paste" prompt and shows what it will do.
                    PasteButton(payloadType: String.self) { strings in accept(strings.first ?? "") }
                        .buttonBorderShape(.capsule)
                        .labelStyle(.titleAndIcon)
                        .controlSize(.large)
                }
                .padding()
            }
            .navigationTitle("Scan to pay")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close", systemImage: "xmark") { dismiss() } } }
        }
        .tint(Wasabi.accent)
    }

    private func accept(_ text: String) {
        guard !done else { return }
        do {
            let payment = try Bitcoin.parse(text, network: network)
            done = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            dismiss()
            found(payment)
        } catch {
            problem = error.description
        }
    }
}
