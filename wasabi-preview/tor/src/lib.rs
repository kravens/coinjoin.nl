//! Minimal C ABI around arti-client for Wasabi Preview Mobile: bootstrap once, then POST JSON-RPC
//! to a client-authorized v3 onion service over one reused HTTP/1.1 keep-alive stream.
//!
//! Wasabi's HttpListener only answers to its own prefix, so the Host header is supplied by
//! the caller (127.0.0.1:<rpc port>) instead of the onion name.

use std::ffi::{c_char, c_void, CStr};
use std::future::Future;
use std::path::PathBuf;
use std::str::FromStr;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use arti_client::config::{CfgPath, TorClientConfigBuilder};
use arti_client::{DataStream, TorClient};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::runtime::Runtime;
use tor_hsclient::HsClientDescEncKeypairSpecifier;
use tor_hscrypto::pk::{HsClientDescEncKeypair, HsId};
use tor_keymgr::config::ArtiKeystoreKind;
use tor_keymgr::KeystoreSelector;
use tor_llcrypto::pk::curve25519::{PublicKey, StaticKeypair, StaticSecret};
use tor_rtcompat::PreferredRuntime;

type Client = Arc<TorClient<PreferredRuntime>>;
type Conn = (String, BufReader<DataStream>);

struct Tor {
    rt: Runtime,
    client: Client,
    conn: Arc<tokio::sync::Mutex<Option<Conn>>>,
}

static TOR: OnceLock<Result<Tor, String>> = OnceLock::new();
static START_ERROR: Mutex<Option<String>> = Mutex::new(None);

fn start(dir: PathBuf) -> Result<Tor, String> {
    let rt = Runtime::new().map_err(|e| e.to_string())?;
    let mut cfg = TorClientConfigBuilder::default();
    cfg.storage()
        .cache_dir(CfgPath::new_literal(dir.join("cache")))
        .state_dir(CfgPath::new_literal(dir.join("state")));
    // The iOS app container is already private to this app; fs-mistrust's owner/mode checks don't apply.
    cfg.storage().permissions().dangerously_trust_everyone();
    // The client-auth x25519 key is inserted from the Keychain at call time and never written to disk.
    cfg.storage().keystore().primary().kind(tor_config::ExplicitOrAuto::Explicit(ArtiKeystoreKind::Ephemeral));
    cfg.address_filter().allow_onion_addrs(true);
    let cfg = cfg.build().map_err(|e| e.to_string())?;

    let client = rt.block_on(async {
        TorClient::with_runtime(PreferredRuntime::current().map_err(|e| e.to_string())?)
            .config(cfg)
            .create_unbootstrapped_async()
            .await
            .map_err(|e| e.to_string())
    })?;
    let bootstrapping = client.clone();
    rt.spawn(async move {
        if let Err(e) = bootstrapping.bootstrap().await {
            *START_ERROR.lock().unwrap() = Some(e.to_string());
        }
    });
    Ok(Tor { rt, client, conn: Default::default() })
}

fn tor() -> Result<&'static Tor, String> {
    match TOR.get() {
        Some(Ok(t)) => Ok(t),
        Some(Err(e)) => Err(format!("Tor failed to start: {e}")),
        None => Err("Tor not started".into()),
    }
}

/// Parses C tor's `<onion>:descriptor:x25519:<base32 secret>` and installs the key in the in-memory keystore.
fn install_key(client: &Client, auth: &str) -> Result<String, String> {
    let parts: Vec<&str> = auth.trim().split(':').collect();
    let [onion, "descriptor", "x25519", encoded] = parts[..] else {
        return Err("Client auth key must look like <onion>:descriptor:x25519:<key>".into());
    };
    let bytes = data_encoding::BASE32_NOPAD.decode(encoded.to_uppercase().as_bytes()).map_err(|e| e.to_string())?;
    let secret_bytes: [u8; 32] = bytes.try_into().map_err(|_| "Client auth key is not 32 bytes".to_string())?;
    let secret = StaticSecret::from(secret_bytes);
    let public = PublicKey::from(&secret);
    let keypair: HsClientDescEncKeypair = StaticKeypair { secret, public }.into();
    let onion = format!("{onion}.onion");
    let hsid = HsId::from_str(&onion).map_err(|e| e.to_string())?;
    client
        .keymgr()
        .map_err(|e| e.to_string())?
        .insert(keypair, &HsClientDescEncKeypairSpecifier::new(hsid), KeystoreSelector::Primary, true)
        .map_err(|e| e.to_string())?;
    Ok(onion)
}

struct Request<'a> {
    onion: String,
    port: u16,
    path: &'a str,
    host: &'a str,
    authorization: &'a str,
    body: &'a [u8],
    retry: bool, // only for requests that are safe to send twice
}

/// Writes one HTTP/1.1 request and reads one response (Content-Length or chunked).
/// Returns `Ok(None)` when the server closed the connection before answering, so the caller can reconnect.
async fn exchange<S: AsyncRead + AsyncWrite + Unpin>(stream: &mut BufReader<S>, r: &Request<'_>) -> Result<Option<(u16, Vec<u8>, bool)>, String> {
    let head = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nAuthorization: Basic {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        r.path, r.host, r.authorization, r.body.len()
    );
    let io = |e: std::io::Error| e.to_string();
    stream.write_all(head.as_bytes()).await.map_err(io)?;
    stream.write_all(r.body).await.map_err(io)?;
    stream.flush().await.map_err(io)?;

    let mut line = String::new();
    if stream.read_line(&mut line).await.map_err(io)? == 0 {
        return Ok(None);
    }
    let status: u16 = line.split_whitespace().nth(1).and_then(|s| s.parse().ok()).ok_or_else(|| format!("Bad status line {line:?}"))?;
    let (mut length, mut chunked, mut close) = (None, false, false);
    loop {
        line.clear();
        if stream.read_line(&mut line).await.map_err(io)? == 0 {
            return Err("Connection closed in headers".into());
        }
        let Some((name, value)) = line.trim_end().split_once(':') else { break };
        let value = value.trim();
        match name.to_ascii_lowercase().as_str() {
            "content-length" => length = value.parse::<usize>().ok(),
            "transfer-encoding" => chunked = value.eq_ignore_ascii_case("chunked"),
            "connection" => close = value.eq_ignore_ascii_case("close"),
            _ => {}
        }
    }
    let mut body = Vec::new();
    if chunked {
        loop {
            line.clear();
            stream.read_line(&mut line).await.map_err(io)?;
            let size = usize::from_str_radix(line.trim().split(';').next().unwrap_or(""), 16).map_err(|_| format!("Bad chunk size {line:?}"))?;
            if size == 0 {
                // Trailers, then the final empty line.
                loop {
                    line.clear();
                    if stream.read_line(&mut line).await.map_err(io)? == 0 || line == "\r\n" || line == "\n" {
                        break;
                    }
                }
                break;
            }
            let start = body.len();
            body.resize(start + size, 0);
            stream.read_exact(&mut body[start..]).await.map_err(io)?;
            line.clear();
            stream.read_line(&mut line).await.map_err(io)?; // CRLF after the chunk
        }
    } else if let Some(n) = length {
        body.resize(n, 0);
        stream.read_exact(&mut body).await.map_err(io)?;
    } else {
        stream.read_to_end(&mut body).await.map_err(io)?;
        close = true;
    }
    Ok(Some((status, body, close)))
}

async fn send<S, F, C>(slot: &mut Option<(String, BufReader<S>)>, r: &Request<'_>, connect: F) -> Result<(u16, Vec<u8>), String>
where
    S: AsyncRead + AsyncWrite + Unpin,
    F: Fn() -> C,
    C: Future<Output = Result<S, String>>,
{
    // A reused stream can die after Wasabi acted on the request, and then only a read may go out again.
    // Anything else gets a stream of its own and one attempt.
    if !r.retry {
        *slot = None;
    }
    let mut fresh = false;
    for _ in 0..2 {
        // Out of the slot while in use: a request dropped by the timeout takes its stream along,
        // instead of leaving its late response for the next caller to read.
        let (onion, mut stream) = match slot.take() {
            Some(conn) if conn.0 == r.onion => conn,
            _ => {
                fresh = true;
                (r.onion.clone(), BufReader::new(connect().await?))
            }
        };
        match exchange(&mut stream, r).await {
            Ok(Some((status, body, close))) => {
                if !close {
                    *slot = Some((onion, stream));
                }
                return Ok((status, body));
            }
            // A kept-alive stream the server has since closed: reconnect once. A fresh one failing is a real error.
            Ok(None) if !fresh => {}
            Ok(None) => return Err("Onion service closed the connection".into()),
            Err(_) if !fresh => {}
            Err(e) => return Err(e),
        }
    }
    Err("Could not reach the onion service".into())
}

async fn post(tor: &Tor, r: Request<'_>) -> Result<(u16, Vec<u8>), String> {
    let mut slot = tor.conn.lock().await;
    send(&mut slot, &r, || async {
        if let Some(e) = START_ERROR.lock().unwrap().clone() {
            return Err(format!("Tor bootstrap failed: {e}"));
        }
        tor.client.connect((r.onion.as_str(), r.port)).await.map_err(|e| e.to_string())
    })
    .await
}

unsafe fn cstr<'a>(p: *const c_char) -> &'a str {
    if p.is_null() { "" } else { CStr::from_ptr(p).to_str().unwrap_or("") }
}

/// Starts Arti with its state and cache under `dir`. Idempotent. Returns 0, or -1 if Tor could not start.
#[no_mangle]
pub unsafe extern "C" fn wasabitor_start(dir: *const c_char) -> i32 {
    let dir = PathBuf::from(cstr(dir));
    match TOR.get_or_init(|| start(dir)) {
        Ok(_) => 0,
        Err(_) => -1,
    }
}

/// Bootstrap progress 0..100, or -1 when Tor is not running / failed.
#[no_mangle]
pub extern "C" fn wasabitor_progress() -> i32 {
    if START_ERROR.lock().unwrap().is_some() {
        return -1;
    }
    match tor() {
        Ok(t) => (t.client.bootstrap_status().as_frac() * 100.0).round() as i32,
        Err(_) => -1,
    }
}

pub type Callback = unsafe extern "C" fn(ctx: *mut c_void, status: i32, body: *const u8, len: usize, error: *const c_char);

/// POSTs `body` to `path` on the onion named in `auth_key` (C tor `.auth_private` line), port `port`.
/// Calls `cb` exactly once from a Tor thread with (status, body) or (0, null, error). Buffers are only valid during the callback.
#[no_mangle]
pub unsafe extern "C" fn wasabitor_post(
    auth_key: *const c_char,
    port: u16,
    path: *const c_char,
    host: *const c_char,
    authorization: *const c_char,
    body: *const u8,
    len: usize,
    retry: bool,
    ctx: *mut c_void,
    cb: Callback,
) {
    let auth = cstr(auth_key).to_owned();
    let path = cstr(path).to_owned();
    let host = cstr(host).to_owned();
    let authorization = cstr(authorization).to_owned();
    let body = if body.is_null() { Vec::new() } else { std::slice::from_raw_parts(body, len).to_vec() };
    let ctx = ctx as usize;

    let finish = move |result: Result<(u16, Vec<u8>), String>| match result {
        Ok((status, body)) => cb(ctx as *mut c_void, i32::from(status), body.as_ptr(), body.len(), std::ptr::null()),
        Err(e) => {
            let e = std::ffi::CString::new(e.replace('\0', " ")).unwrap();
            cb(ctx as *mut c_void, 0, std::ptr::null(), 0, e.as_ptr())
        }
    };

    let tor = match tor() {
        Ok(t) => t,
        Err(e) => return finish(Err(e)),
    };
    tor.rt.spawn(async move {
        let work = async {
            let onion = install_key(&tor.client, &auth)?;
            let r = Request { onion, port, path: &path, host: &host, authorization: &authorization, body: &body, retry };
            post(tor, r).await
        };
        let result = tokio::time::timeout(Duration::from_secs(180), work)
            .await
            .unwrap_or_else(|_| Err("Timed out waiting for the onion service".into()));
        finish(result);
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
    use tokio::io::{duplex, DuplexStream};

    const BODY: &[u8] = b"{\"method\":\"send\"}";
    const OK: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok";
    type Slot = Option<(String, BufReader<DuplexStream>)>;

    fn request(retry: bool) -> Request<'static> {
        Request { onion: "a.onion".into(), port: 80, path: "/", host: "h", authorization: "x", body: BODY, retry }
    }

    /// The server side of a stream: counts whole requests, answering `answers` of them and then closing or hanging.
    fn server(answers: usize, hang: bool) -> (DuplexStream, tokio::task::JoinHandle<usize>) {
        let (client, mut server) = duplex(4096);
        let task = tokio::spawn(async move {
            let mut received = 0;
            'requests: loop {
                let mut seen = Vec::new();
                let mut buf = [0; 1024];
                while !seen.ends_with(BODY) {
                    match server.read(&mut buf).await {
                        Ok(n) if n > 0 => seen.extend_from_slice(&buf[..n]),
                        _ => break 'requests,
                    }
                }
                received += 1;
                if received <= answers {
                    server.write_all(OK).await.unwrap();
                } else if hang {
                    continue;
                } else {
                    break;
                }
            }
            received
        });
        (client, task)
    }

    fn run(test: impl Future<Output = ()>) {
        Runtime::new().unwrap().block_on(test);
    }

    fn stale_slot(stream: DuplexStream) -> Slot {
        Some(("a.onion".into(), BufReader::new(stream)))
    }

    #[test]
    fn a_read_reconnects_after_a_stale_stream_and_keeps_the_new_one() {
        run(async {
            let (stale, stale_server) = server(0, false);
            let (next, next_server) = server(1, false);
            let next = Mutex::new(Some(next));
            let mut slot = stale_slot(stale);
            let result = send(&mut slot, &request(true), || async { Ok(next.lock().unwrap().take().unwrap()) }).await;
            assert_eq!(result.unwrap(), (200, b"ok".to_vec()));
            assert!(slot.is_some());
            drop(slot);
            assert_eq!(stale_server.await.unwrap(), 1);
            assert_eq!(next_server.await.unwrap(), 1);
        });
    }

    #[test]
    fn a_payment_goes_out_once_on_its_own_stream() {
        run(async {
            let (kept, kept_server) = server(0, false);
            let (own, own_server) = server(0, false);
            let own = Mutex::new(Some(own));
            let connects = AtomicUsize::new(0);
            let mut slot = stale_slot(kept);
            let result = send(&mut slot, &request(false), || async {
                connects.fetch_add(1, SeqCst);
                Ok(own.lock().unwrap().take().unwrap())
            })
            .await;
            assert!(result.is_err()); // no answer: reported, never sent again
            assert_eq!(connects.load(SeqCst), 1);
            assert_eq!(kept_server.await.unwrap(), 0);
            assert_eq!(own_server.await.unwrap(), 1);
        });
    }

    #[test]
    fn a_timed_out_request_leaves_no_stream_behind() {
        run(async {
            let (kept, kept_server) = server(0, true);
            let mut slot = stale_slot(kept);
            let late = tokio::time::timeout(Duration::from_millis(50), send(&mut slot, &request(true), || async { Err("no reconnect expected".to_string()) })).await;
            assert!(late.is_err());
            assert!(slot.is_none());
            assert_eq!(kept_server.await.unwrap(), 1);
        });
    }
}
