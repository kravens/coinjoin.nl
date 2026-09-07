// SPDX-FileCopyrightText: 2026 Kevin Ravensberg <kevinravensberg@proton.me>
// SPDX-License-Identifier: MIT OR GPL-3.0-or-later

//! SLIP-0019 proof of ownership (P2WPKH + P2TR), Trezor-compatible.
//!
//! `proof = proofBody || bip322Signature` where
//! `proofBody = 0x534c0019 || flags || varint(n) || ownership_id * n` and the
//! signed digest is `SHA256(proofBody || varint(len(spk)) || spk ||
//! varint(len(commitment)) || commitment)`. The ownership id is
//! `HMAC-SHA256(k, spk)` with `k` the SLIP-0021 node
//! `m/"SLIP-0019"/"Ownership identification key"` of the BIP-0039 seed.
//!
//! P2WPKH signs the digest with ECDSA (witness `[der_sig || 0x01, pubkey]`);
//! P2TR key-spends with the BIP-86 tweaked key (witness `[64-byte schnorr]`,
//! SIGHASH_DEFAULT so no trailing sighash byte).
//!
//! Two entry points: the `*_with_key` functions take the SLIP-0021 ownership
//! node plus an already-derived key, which is what a live session uses (the
//! seed is derived once at authorization and dropped); the seed-taking wrappers
//! stay because the SLIP-0019 spec vectors are stated in terms of a seed.
//!
//! Purely functional: keys in, proof out. No KeyOS server dependencies.

use ngwallet::bdk_wallet::bitcoin::{
    bip32::{ChildNumber, DerivationPath, Xpriv},
    hashes::{hmac::HmacEngine, sha256, sha512, Hash, HashEngine, Hmac},
    key::{Keypair, TapTweak, XOnlyPublicKey},
    secp256k1::{All, Message, Secp256k1},
    CompressedPublicKey, Network, ScriptBuf,
};

/// Script type of the input a proof is requested for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScriptType {
    /// Segwit v0 pay-to-witness-pubkey-hash (BIP-84 keys).
    P2wpkh,
    /// Taproot key-spend with the BIP-86 tweak (BIP-86 keys).
    P2tr,
}

/// BIP-43 purpose of a taproot (BIP-86) account, hardened.
pub const PURPOSE_TR: u32 = 86 | 0x8000_0000;

impl ScriptType {
    /// The script type a BIP-43 purpose implies: 86' is taproot, anything else
    /// in policy scope is segwit v0.
    pub fn for_purpose(purpose: u32) -> Self {
        if purpose == PURPOSE_TR {
            ScriptType::P2tr
        } else {
            ScriptType::P2wpkh
        }
    }
}

pub const FLAG_USER_CONFIRMATION: u8 = 0x01;
const VERSION_MAGIC: [u8; 4] = [0x53, 0x4c, 0x00, 0x19];

#[derive(Debug, thiserror::Error)]
pub enum Slip19Error {
    #[error("bip32 derivation failed: {0}")]
    Bip32(#[from] ngwallet::bdk_wallet::bitcoin::bip32::Error),
}

fn push_varint(value: u64, out: &mut Vec<u8>) {
    match value {
        0..=0xfc => out.push(value as u8),
        0xfd..=0xffff => {
            out.push(0xfd);
            out.extend_from_slice(&(value as u16).to_le_bytes());
        }
        0x1_0000..=0xffff_ffff => {
            out.push(0xfe);
            out.extend_from_slice(&(value as u32).to_le_bytes());
        }
        _ => {
            out.push(0xff);
            out.extend_from_slice(&value.to_le_bytes());
        }
    }
}

/// SLIP-0021 node derivation. Master node from the BIP-0039 seed, then one
/// child step per label. Returns the node's 32-byte key.
fn slip21_key(seed: &[u8], labels: &[&[u8]]) -> [u8; 32] {
    let mut node = hmac_sha512(b"Symmetric key seed", seed);
    for label in labels {
        let mut msg = Vec::with_capacity(1 + label.len());
        msg.push(0x00);
        msg.extend_from_slice(label);
        node = hmac_sha512(&node[..32], &msg);
    }
    let mut key = [0u8; 32];
    key.copy_from_slice(&node[32..]);
    key
}

fn hmac_sha512(key: &[u8], msg: &[u8]) -> [u8; 64] {
    let mut engine = HmacEngine::<sha512::Hash>::new(key);
    engine.input(msg);
    Hmac::<sha512::Hash>::from_engine(engine).to_byte_array()
}

fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut engine = HmacEngine::<sha256::Hash>::new(key);
    engine.input(msg);
    Hmac::<sha256::Hash>::from_engine(engine).to_byte_array()
}

/// The SLIP-0019 ownership identification key of a seed: SLIP-0021 node
/// `m/"SLIP-0019"/"Ownership identification key"`. Derived once per session so
/// proofs never need the seed again.
pub fn ownership_key(seed: &[u8]) -> [u8; 32] {
    slip21_key(seed, &[b"SLIP-0019", b"Ownership identification key"])
}

/// The device's ownership id for a scriptPubKey (SLIP-0019 § Ownership identifier).
pub fn ownership_id(seed: &[u8], script_pubkey: &[u8]) -> [u8; 32] {
    ownership_id_with_key(&ownership_key(seed), script_pubkey)
}

/// Ownership id from an already-derived ownership key.
pub fn ownership_id_with_key(key: &[u8; 32], script_pubkey: &[u8]) -> [u8; 32] {
    hmac_sha256(key, script_pubkey)
}

/// scriptPubKey the key at `xpriv` pays to under `script_type`. Taproot uses
/// the BIP-86 tweak (no script tree), matching what Wasabi derives host-side.
pub fn script_pubkey(secp: &Secp256k1<All>, xpriv: &Xpriv, script_type: ScriptType) -> ScriptBuf {
    match script_type {
        ScriptType::P2wpkh => {
            let pubkey = CompressedPublicKey(xpriv.private_key.public_key(secp));
            ScriptBuf::new_p2wpkh(&pubkey.wpubkey_hash())
        }
        ScriptType::P2tr => {
            let keypair = Keypair::from_secret_key(secp, &xpriv.private_key);
            let (internal, _) = XOnlyPublicKey::from_keypair(&keypair);
            ScriptBuf::new_p2tr(secp, internal, None)
        }
    }
}

/// Generate a SLIP-0019 ownership proof for the P2WPKH key at `path`.
/// (Spec-vector wrapper; live sessions use [`ownership_proof_with_key`].)
pub fn ownership_proof(
    secp: &Secp256k1<All>,
    seed: &[u8],
    network: Network,
    path: &[u32],
    commitment_data: &[u8],
    user_confirmation: bool,
) -> Result<Vec<u8>, Slip19Error> {
    ownership_proof_for(
        secp,
        seed,
        network,
        ScriptType::P2wpkh,
        path,
        commitment_data,
        user_confirmation,
    )
}

/// Generate a SLIP-0019 ownership proof for the key at `path`, from a seed.
#[allow(clippy::too_many_arguments)]
pub fn ownership_proof_for(
    secp: &Secp256k1<All>,
    seed: &[u8],
    network: Network,
    script_type: ScriptType,
    path: &[u32],
    commitment_data: &[u8],
    user_confirmation: bool,
) -> Result<Vec<u8>, Slip19Error> {
    let derivation: DerivationPath =
        path.iter().map(|&i| ChildNumber::from(i)).collect::<Vec<_>>().into();
    let xpriv = Xpriv::new_master(network, seed)?.derive_priv(secp, &derivation)?;
    Ok(ownership_proof_with_key(
        secp,
        &ownership_key(seed),
        &xpriv,
        script_type,
        commitment_data,
        user_confirmation,
    ))
}

/// Generate a SLIP-0019 ownership proof from session key material: the
/// SLIP-0021 ownership node and the already-derived key for the address.
pub fn ownership_proof_with_key(
    secp: &Secp256k1<All>,
    ownership_key: &[u8; 32],
    xpriv: &Xpriv,
    script_type: ScriptType,
    commitment_data: &[u8],
    user_confirmation: bool,
) -> Vec<u8> {
    let script = script_pubkey(secp, xpriv, script_type);
    let spk = script.as_bytes();

    // Proof body
    let mut proof = Vec::new();
    proof.extend_from_slice(&VERSION_MAGIC);
    proof.push(if user_confirmation { FLAG_USER_CONFIRMATION } else { 0 });
    push_varint(1, &mut proof);
    proof.extend_from_slice(&ownership_id_with_key(ownership_key, spk));

    // Sighash = SHA256(proofBody || proofFooter)
    let mut preimage = proof.clone();
    push_varint(spk.len() as u64, &mut preimage);
    preimage.extend_from_slice(spk);
    push_varint(commitment_data.len() as u64, &mut preimage);
    preimage.extend_from_slice(commitment_data);
    let sighash = sha256::Hash::hash(&preimage);
    let msg = Message::from_digest(sighash.to_byte_array());

    // BIP-322 "simple" signature: empty scriptSig + the script type's witness.
    proof.push(0x00); // empty scriptSig
    match script_type {
        ScriptType::P2wpkh => {
            // witness stack: [der_sig || SIGHASH_ALL, pubkey]
            let pubkey = CompressedPublicKey(xpriv.private_key.public_key(secp));
            let signature = secp.sign_ecdsa(&msg, &xpriv.private_key);
            let mut der = signature.serialize_der().to_vec();
            der.push(0x01); // SIGHASH_ALL
            push_varint(2, &mut proof);
            push_varint(der.len() as u64, &mut proof);
            proof.extend_from_slice(&der);
            push_varint(33, &mut proof);
            proof.extend_from_slice(&pubkey.to_bytes());
        }
        ScriptType::P2tr => {
            // witness stack: [64-byte schnorr sig] (SIGHASH_DEFAULT, no sighash byte)
            let keypair = Keypair::from_secret_key(secp, &xpriv.private_key);
            let tweaked = keypair.tap_tweak(secp, None).to_keypair();
            let signature = secp.sign_schnorr_no_aux_rand(&msg, &tweaked);
            push_varint(1, &mut proof);
            push_varint(64, &mut proof);
            proof.extend_from_slice(signature.as_ref());
        }
    }

    proof
}

#[cfg(test)]
mod tests {
    use ngwallet::bdk_wallet::keys::bip39::Mnemonic;

    use super::*;

    // SLIP-0019 spec P2WPKH test vector ("all all ..." seed, m/84'/0'/0'/1/0, empty commitment).
    const H: u32 = 0x8000_0000;

    fn test_seed() -> Vec<u8> {
        Mnemonic::parse("all all all all all all all all all all all all")
            .unwrap()
            .to_seed("")
            .to_vec()
    }

    #[test]
    fn spec_ownership_id() {
        let spk = hex("0014b2f771c370ccf219cd3059cda92bdf7f00cf2103");
        assert_eq!(
            ownership_id(&test_seed(), &spk).to_vec(),
            hex("a122407efc198211c81af4450f40b235d54775efd934d16b9e31c6ce9bad5707"),
        );
    }

    #[test]
    fn spec_p2wpkh_proof() {
        let secp = Secp256k1::new();
        let proof = ownership_proof(
            &secp,
            &test_seed(),
            Network::Bitcoin,
            &[84 | H, H, H, 1, 0],
            &[],
            false,
        )
        .unwrap();
        assert_eq!(
            proof,
            hex(
                "534c00190001a122407efc198211c81af4450f40b235d54775efd934d16b9e31c6ce9bad5707\
                 0002483045022100c0dc28bb563fc5fea76cacff75dba9cb4122412faae01937cdebccfb065f9a70\
                 02202e980bfbd8a434a7fc4cd2ca49da476ce98ca097437f8159b1a386b41fcdfac50121032ef683\
                 18c8f6aaa0adec0199c69901f0db7d3485eb38d9ad235221dc3d61154b"
            ),
        );
    }

    /// The session path (cached ownership node + derived key) must produce the
    /// exact same proof as the seed path — that equality is what lets the device
    /// drop the seed after authorization.
    #[test]
    fn key_path_matches_seed_path() {
        let secp = Secp256k1::new();
        let seed = test_seed();
        let from_seed = ownership_proof(
            &secp,
            &seed,
            Network::Bitcoin,
            &[84 | H, H, H, 1, 0],
            b"commitment",
            true,
        )
        .unwrap();

        let derivation: DerivationPath = [84 | H, H, H, 1u32, 0]
            .iter()
            .map(|&i| ChildNumber::from(i))
            .collect::<Vec<_>>()
            .into();
        let xpriv = Xpriv::new_master(Network::Bitcoin, &seed)
            .unwrap()
            .derive_priv(&secp, &derivation)
            .unwrap();
        let from_keys = ownership_proof_with_key(
            &secp,
            &ownership_key(&seed),
            &xpriv,
            ScriptType::P2wpkh,
            b"commitment",
            true,
        );
        assert_eq!(from_seed, from_keys);
    }

    #[test]
    fn p2tr_spk_matches_bip86_vector() {
        // BIP-86 test vector: "abandon ... about" seed, m/86'/0'/0'/0/0,
        // tweaked output key a60869f0...49dc684c.
        let secp = Secp256k1::new();
        let seed = Mnemonic::parse(
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
        )
        .unwrap()
        .to_seed("");
        let proof = ownership_proof_for(
            &secp,
            &seed,
            Network::Bitcoin,
            ScriptType::P2tr,
            &[86 | H, H, H, 0, 0],
            &[],
            false,
        )
        .unwrap();
        // ownership id is HMAC(spk); recompute with the vector's spk — equal ids
        // prove the derived spk matched the BIP-86 vector byte-for-byte.
        let mut spk = hex("5120a60869f0dbcf1dc659c9cecbaf8050135ea9e8cdc487053f1dc6880949dc684c");
        assert_eq!(&proof[6..38], &ownership_id(&seed, &spk)[..]);
        // and the witness is a single 64-byte schnorr signature
        assert_eq!(&proof[38..41], &[0x00, 0x01, 0x40]);
        assert_eq!(proof.len(), 41 + 64);

        // Round-trip: the signature verifies against the tweaked output key over
        // the recomputed SLIP-0019 digest.
        use ngwallet::bdk_wallet::bitcoin::secp256k1::schnorr::Signature;
        let mut preimage = proof[..38].to_vec();
        push_varint(spk.len() as u64, &mut preimage);
        preimage.append(&mut spk);
        push_varint(0, &mut preimage);
        let digest = sha256::Hash::hash(&preimage);
        let xonly = XOnlyPublicKey::from_slice(&hex(
            "a60869f0dbcf1dc659c9cecbaf8050135ea9e8cdc487053f1dc6880949dc684c",
        ))
        .unwrap();
        let sig = Signature::from_slice(&proof[41..]).unwrap();
        secp.verify_schnorr(&sig, &Message::from_digest(digest.to_byte_array()), &xonly)
            .unwrap();
    }

    #[test]
    fn user_confirmation_flag_set() {
        let secp = Secp256k1::new();
        let proof = ownership_proof(
            &secp,
            &test_seed(),
            Network::Bitcoin,
            &[84 | H, H, H, 1, 0],
            b"commitment",
            true,
        )
        .unwrap();
        assert_eq!(proof[4], FLAG_USER_CONFIRMATION);
        // commitment data must change the signature vs the spec vector
        assert_ne!(&proof[38..], &hex("0002483045022100c0dc28bb")[..]);
    }

    fn hex(s: &str) -> Vec<u8> {
        (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
    }
}
