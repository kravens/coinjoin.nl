//! Live check against the real onion: `WASABI_AUTH_FILE=... WASABI_RPC_PASSWORD=... cargo test --release -- --ignored --nocapture`.
//! Read-only RPC (listwallets) only.
use std::ffi::{c_void, CString};
use std::sync::mpsc;
use std::time::Instant;

unsafe extern "C" fn done(ctx: *mut c_void, status: i32, body: *const u8, len: usize, error: *const std::ffi::c_char) {
    let tx = &*(ctx as *const mpsc::Sender<(i32, Vec<u8>, String)>);
    let body = if body.is_null() { vec![] } else { std::slice::from_raw_parts(body, len).to_vec() };
    let error = if error.is_null() { String::new() } else { std::ffi::CStr::from_ptr(error).to_string_lossy().into_owned() };
    tx.send((status, body, error)).unwrap();
}

#[test]
#[ignore]
fn listwallets_over_onion() {
    let auth = std::fs::read_to_string(std::env::var("WASABI_AUTH_FILE").unwrap()).unwrap();
    let password = std::env::var("WASABI_RPC_PASSWORD").unwrap();
    let dir = std::env::temp_dir().join("wasabitor-test");
    let dir_c = CString::new(dir.to_str().unwrap()).unwrap();
    assert_eq!(unsafe { wasabitor::wasabitor_start(dir_c.as_ptr()) }, 0);

    let started = Instant::now();
    while wasabitor::wasabitor_progress() < 100 {
        assert!(started.elapsed().as_secs() < 300, "bootstrap stuck at {}", wasabitor::wasabitor_progress());
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
    eprintln!("bootstrapped in {:?}", started.elapsed());

    let (tx, rx) = mpsc::channel::<(i32, Vec<u8>, String)>();
    let auth_c = CString::new(auth.trim()).unwrap();
    let path = CString::new("/").unwrap();
    let host = CString::new("127.0.0.1:37128").unwrap();
    let authz = CString::new(data_encoding::BASE64.encode(format!("preview:{password}").as_bytes())).unwrap();
    for i in 0..3 {
        let t = Instant::now();
        let body = br#"{"jsonrpc":"2.0","id":"1","method":"listwallets","params":[]}"#;
        unsafe {
            wasabitor::wasabitor_post(auth_c.as_ptr(), 80, path.as_ptr(), host.as_ptr(), authz.as_ptr(), body.as_ptr(), body.len(), &tx as *const _ as *mut c_void, done);
        }
        let (status, body, error) = rx.recv_timeout(std::time::Duration::from_secs(200)).unwrap();
        let text = String::from_utf8_lossy(&body);
        eprintln!("call {i}: HTTP {status} in {:?} err={error:?} body={}", t.elapsed(), text.chars().take(80).collect::<String>());
        assert_eq!(status, 200, "{error}");
        assert!(text.contains("walletName"));
    }
}
