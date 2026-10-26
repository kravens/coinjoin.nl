import Foundation
import WasabiTor

// Arti (Rust) embedded as tor/WasabiTor.xcframework; see tor/src/lib.rs for the C ABI.
enum Tor {
    // Idempotent. Arti keeps its directory cache and guard state under Application Support/tor.
    static func start() {
        let dir = URL.applicationSupportDirectory.appending(path: "tor")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        wasabitor_start(dir.path)
    }

    /// 0...100 while bootstrapping, nil when Tor is not running or failed.
    static var progress: Int? {
        let p = wasabitor_progress()
        return p < 0 ? nil : Int(p)
    }

    private final class Reply {
        let continuation: CheckedContinuation<(Int, Data), any Error>
        init(_ c: CheckedContinuation<(Int, Data), any Error>) { continuation = c }
    }

    /// One HTTP POST to port 80 of the onion named in `authLine`; Arti runs the request on its own threads.
    static func post(authLine: String, path: String, host: String, authorization: String, body: Data) async throws -> (Int, Data) {
        start()
        return try await withCheckedThrowingContinuation { continuation in
            let reply = Unmanaged.passRetained(Reply(continuation)).toOpaque()
            body.withUnsafeBytes { bytes in
                wasabitor_post(authLine, 80, path, host, authorization, bytes.baseAddress, bytes.count, reply) { ctx, status, body, length, error in
                    let reply = Unmanaged<Reply>.fromOpaque(ctx!).takeRetainedValue()
                    if let error {
                        reply.continuation.resume(throwing: RPCError(String(cString: error), transport: true))
                    } else {
                        reply.continuation.resume(returning: (Int(status), body.map { Data(bytes: $0, count: length) } ?? Data()))
                    }
                }
            }
        }
    }
}
