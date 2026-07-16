//! Bridge between the Slint `Cj` global and the real `wallet_rpc_core` engine.
//!
//! The engine, policy checks, and SLIP-0019 proofs are the exact code from the
//! KeyOS `feature/passport-coinjoin` branch. Only the edges are mocked for the
//! UI mockup: the seed is a fixed TEST vector (never a user seed) and policy
//! approval is the slide gesture instead of the trusted-display prompt.

use core::cell::RefCell;

use slint_keyos_platform::slint::ComponentHandle;
use wallet_rpc_core::coinjoin::Policy;
use wallet_rpc_core::protocol::{Backend, Engine};

use wallet_rpc_core::ngwallet::bdk_wallet::bitcoin::Network;
use wallet_rpc_core::ngwallet::bdk_wallet::keys::bip39::Mnemonic;

/// SLIP-0019 spec test seed ("all all ..."). A published test vector — no funds.
fn test_seed() -> Vec<u8> {
    Mnemonic::parse("all all all all all all all all all all all all")
        .expect("static test mnemonic")
        .to_seed("")
        .to_vec()
}

struct MockBackend;

impl Backend for MockBackend {
    fn firmware_version(&self) -> String {
        "mockup-0.1.0".into()
    }
    fn seed(&mut self) -> Option<Vec<u8>> {
        // ponytail: fixed test vector; the real app gets this from os/security
        Some(test_seed())
    }
    fn approve_policy(&mut self, _policy: &Policy) -> bool {
        // The slide-to-authorize gesture already happened in the UI.
        true
    }
}

fn policy() -> Policy {
    Policy {
        network: Network::Bitcoin,
        account: 0,
        coordinator_id: b"coinjoin.nl".to_vec(),
        max_fee_contribution: 1000,
        max_rounds: 10,
        valid_for_secs: 12 * 60 * 60,
    }
}

/// Commitment data as Wasabi sends it: length-prefixed coordinator id, then the
/// round's 32-byte commitment.
fn commitment() -> Vec<u8> {
    let id = b"coinjoin.nl";
    let mut data = vec![id.len() as u8];
    data.extend_from_slice(id);
    data.extend_from_slice(&[0x42; 32]);
    data
}

const H: u32 = 0x8000_0000;

pub fn init(ui: &crate::AppWindow) {
    let state: &'static RefCell<(Engine<MockBackend>, Option<u32>, u32)> =
        Box::leak(Box::new(RefCell::new((Engine::new(MockBackend), None, 0))));

    let cj = ui.global::<crate::Cj>();

    let ui_weak = ui.as_weak();
    cj.on_authorize(move || {
        let ui = ui_weak.unwrap();
        let mut s = state.borrow_mut();
        match s.0.authorize(policy()) {
            Ok(id) => {
                s.1 = Some(id);
                s.2 = 0;
                let cj = ui.global::<crate::Cj>();
                cj.set_session_active(true);
                cj.set_rounds(0);
                cj.set_last_result("".into());
                log::info!("coinjoin session {id} authorized");
            }
            Err(e) => log::warn!("authorize failed: {e:?}"),
        }
    });

    let ui_weak = ui.as_weak();
    cj.on_simulate_round(move || {
        let ui = ui_weak.unwrap();
        let mut s = state.borrow_mut();
        let Some(session) = s.1 else {
            log::warn!("simulate round without session");
            return;
        };
        let index = s.2;
        // Real SLIP-0019 ownership proof from the session engine (P2WPKH path;
        // an 86' purpose here would produce the taproot proof instead).
        match s.0.ownership_proof(session, &[84 | H, H, H, 1, index], &commitment()) {
            Ok(proof) => {
                s.2 += 1;
                let cj = ui.global::<crate::Cj>();
                cj.set_rounds(cj.get_rounds() + 1);
                cj.set_last_result(
                    format!("SLIP-19 proof for m/84'/0'/0'/1/{index} · {} bytes · fee within cap", proof.len())
                        .into(),
                );
            }
            Err(e) => {
                let cj = ui.global::<crate::Cj>();
                cj.set_last_result(format!("round rejected: {e:?}").into());
            }
        }
    });

    let ui_weak = ui.as_weak();
    cj.on_revoke(move || {
        let ui = ui_weak.unwrap();
        let mut s = state.borrow_mut();
        if let Some(session) = s.1.take() {
            let _ = s.0.revoke(session);
            log::info!("coinjoin session {session} revoked");
        }
        s.2 = 0;
        let cj = ui.global::<crate::Cj>();
        cj.set_session_active(false);
        cj.set_rounds(0);
        cj.set_last_result("".into());
    });
}
