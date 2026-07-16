use core::num::NonZeroUsize;
/// Fill `dest` with random bytes from the system's preferred random number
/// source.
///
/// This function returns an error on any failure, including partial reads. We
/// make no guarantees regarding the contents of `dest` on error. If `dest` is
/// empty, `getrandom` immediately returns success, making no calls to the
/// underlying operating system.
///
/// Blocking is possible, at least during early boot; see module documentation.
///
/// In general, `getrandom` will be fast enough for interactive usage, though
/// significantly slower than a user-space CSPRNG; for the latter consider
/// [`rand::thread_rng`](https://docs.rs/rand/*/rand/fn.thread_rng.html).
use core::sync::atomic::AtomicU32;
use core::sync::atomic::Ordering;

static TRNG_CONN: AtomicU32 = AtomicU32::new(0);

fn ensure_trng_conn() {
    if TRNG_CONN.load(Ordering::SeqCst) == 0 {
        TRNG_CONN.store(
            xous::connect(xous::SID::from_bytes(b"trng-server").unwrap())
                .expect("Can't connect to TRNG server"),
            Ordering::SeqCst,
        );
    }
}

pub fn getrandom_inner(dest: &mut [u8]) -> Result<(), crate::error::Error> {
    if dest.is_empty() {
        return Ok(());
    }
    ensure_trng_conn();
    fill_bytes(dest);
    Ok(())
}

fn fill_bytes(data: &mut [u8]) {
    let aligned_buffer =
        xous::map_memory(None, None, (data.len()).next_multiple_of(4096), xous::MemoryFlags::W).unwrap();
    xous::send_message(
        TRNG_CONN.load(Ordering::SeqCst),
        xous::Message::MutableBorrow(xous::MemoryMessage {
            id: 1, /* FillTrng */
            buf: aligned_buffer,
            offset: None,
            valid: NonZeroUsize::new(data.len().next_multiple_of(4) / 4),
        }),
    )
    .unwrap();
    data.copy_from_slice(&aligned_buffer.as_slice()[0..data.len()]);
    xous::unmap_memory(aligned_buffer).unwrap();
}
