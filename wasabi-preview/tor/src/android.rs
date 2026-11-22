//! JNI entry points for the Android app (class nl.kravens.wasabipreview.Tor), over the same Arti client the C ABI uses.
//! `post` blocks its calling thread (Kotlin calls it on Dispatchers.IO) and returns the HTTP status as 4 big-endian
//! bytes followed by the body; failures become a java.io.IOException.

use std::path::PathBuf;
use std::time::Duration;

use jni::objects::{JByteArray, JClass, JString};
use jni::sys::{jboolean, jbyteArray, jint, JNI_TRUE};
use jni::JNIEnv;

use crate::{install_key, post, start, tor, Request, START_ERROR, TOR};

fn string(env: &mut JNIEnv, s: &JString) -> String {
    env.get_string(s).map(Into::into).unwrap_or_default()
}

#[no_mangle]
pub extern "system" fn Java_nl_kravens_wasabipreview_Tor_start(mut env: JNIEnv, _: JClass, dir: JString) -> jint {
    let dir = PathBuf::from(string(&mut env, &dir));
    // rustls needs a process-wide crypto provider before Arti builds its TLS connector, or it aborts.
    let _ = rustls::crypto::ring::default_provider().install_default();
    match TOR.get_or_init(|| start(dir)) {
        Ok(_) => 0,
        Err(_) => -1,
    }
}

#[no_mangle]
pub extern "system" fn Java_nl_kravens_wasabipreview_Tor_progress(_: JNIEnv, _: JClass) -> jint {
    crate::wasabitor_progress()
}

#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub extern "system" fn Java_nl_kravens_wasabipreview_Tor_post(
    mut env: JNIEnv,
    _: JClass,
    auth: JString,
    port: jint,
    path: JString,
    host: JString,
    authorization: JString,
    body: JByteArray,
    retry: jboolean,
) -> jbyteArray {
    let auth = string(&mut env, &auth);
    let path = string(&mut env, &path);
    let host = string(&mut env, &host);
    let authorization = string(&mut env, &authorization);
    let body = env.convert_byte_array(&body).unwrap_or_default();
    let result = tor().and_then(|tor| {
        tor.rt.block_on(async {
            let work = async {
                if let Some(e) = START_ERROR.lock().unwrap().clone() {
                    return Err(format!("Tor bootstrap failed: {e}"));
                }
                let onion = install_key(&tor.client, &auth)?;
                let r = Request { onion, port: port as u16, path: &path, host: &host, authorization: &authorization, body: &body, retry: retry == JNI_TRUE };
                post(tor, r).await
            };
            tokio::time::timeout(Duration::from_secs(180), work)
                .await
                .unwrap_or_else(|_| Err("Timed out waiting for the onion service".into()))
        })
    });
    match result {
        Ok((status, reply)) => {
            let mut out = (u32::from(status)).to_be_bytes().to_vec();
            out.extend_from_slice(&reply);
            env.byte_array_from_slice(&out).map(|a| a.into_raw()).unwrap_or(std::ptr::null_mut())
        }
        Err(e) => {
            let _ = env.throw_new("java/io/IOException", e);
            std::ptr::null_mut()
        }
    }
}
