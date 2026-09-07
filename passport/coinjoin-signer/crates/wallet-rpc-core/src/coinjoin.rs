// SPDX-FileCopyrightText: 2026 Kevin Ravensberg <kevinravensberg@proton.me>
// SPDX-License-Identifier: MIT OR GPL-3.0-or-later

//! Coinjoin session policy and policy-enforced PSBT signing.
//!
//! A session is approved once by the user on the device (via `AuthorizeCoinjoin`).
//! After that, ownership proofs and signatures for rounds that conform to the
//! policy are issued without further interaction; anything non-conforming is
//! rejected outright (never escalated mid-round — WabiSabi phase deadlines
//! don't allow waiting for a human).
//!
//! The session holds derived account keys, never the seed: at authorization the
//! seed is read once, the two account xprivs (84' and 86') and the SLIP-0019
//! ownership node are derived from it, and the seed is dropped. A disclosure
//! bug in the app therefore exposes one account, not the master secret.
//!
//! Purely functional over (keys, policy, psbt) — no KeyOS server dependencies.

use std::time::Instant;

use ngwallet::bdk_wallet::bitcoin::{
    bip32::{ChildNumber, DerivationPath, Fingerprint, Xpriv},
    ecdsa,
    hashes::Hash,
    key::{Keypair, TapTweak},
    psbt::Psbt,
    secp256k1::{All, Message, Secp256k1},
    sighash::{Prevouts, SighashCache},
    taproot, Amount, CompressedPublicKey, EcdsaSighashType, Network, ScriptBuf, TapSighashType,
    TxOut, Witness,
};
use zeroize::Zeroizing;

use crate::slip19::{self, ScriptType};

/// Purpose level of the BIP-84 (segwit v0) account the policy covers.
pub const PURPOSE: u32 = 84;
/// BIP-86 taproot purpose — Wasabi coinjoins are taproot-first.
pub const PURPOSE_TR: u32 = 86;
const HARDENED: u32 = 0x8000_0000;

/// Serialized length of an extended private key (BIP-32).
const XPRIV_ENCODED_LEN: usize = 78;

#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub network: Network,
    /// Account index (unhardened notation), shared by the 84' and 86' accounts.
    pub account: u32,
    /// Coordinator identifier, as committed into ownership proofs (ASCII).
    pub coordinator_id: Vec<u8>,
    /// Total sats this wallet may lose over the whole session (mining fee share
    /// plus coordination fee), i.e. the running sum of
    /// `sum(our inputs) - sum(our outputs)` across every round signed under it.
    /// The user approves this number once, so it has to be the total exposure —
    /// a per-round cap would silently multiply by `max_rounds`.
    pub fee_budget_sats: u64,
    /// Maximum number of rounds this session may sign.
    pub max_rounds: u16,
    /// Session lifetime in seconds from approval.
    pub valid_for_secs: u32,
}

impl Policy {
    /// Wire format, little-endian:
    /// `[network u8 (0=main,1=test)][account u32][coord_len u8][coord bytes]`
    /// `[fee_budget_sats u64][max_rounds u16][valid_for_secs u32]`
    pub fn parse(payload: &[u8]) -> Option<(Policy, usize)> {
        let coord_len = *payload.get(5)? as usize;
        let total = 6 + coord_len + 8 + 2 + 4;
        if payload.len() < total {
            return None;
        }
        let network = match payload[0] {
            0 => Network::Bitcoin,
            1 => Network::Testnet,
            _ => return None,
        };
        let account = u32::from_le_bytes(payload[1..5].try_into().ok()?);
        let coordinator_id = payload[6..6 + coord_len].to_vec();
        let rest = &payload[6 + coord_len..];
        Some((
            Policy {
                network,
                account,
                coordinator_id,
                fee_budget_sats: u64::from_le_bytes(rest[..8].try_into().ok()?),
                max_rounds: u16::from_le_bytes(rest[8..10].try_into().ok()?),
                valid_for_secs: u32::from_le_bytes(rest[10..14].try_into().ok()?),
            },
            total,
        ))
    }

    pub fn serialize(&self) -> Vec<u8> {
        let mut out = Vec::new();
        out.push(match self.network {
            Network::Bitcoin => 0,
            _ => 1,
        });
        out.extend_from_slice(&self.account.to_le_bytes());
        out.push(self.coordinator_id.len() as u8);
        out.extend_from_slice(&self.coordinator_id);
        out.extend_from_slice(&self.fee_budget_sats.to_le_bytes());
        out.extend_from_slice(&self.max_rounds.to_le_bytes());
        out.extend_from_slice(&self.valid_for_secs.to_le_bytes());
        out
    }

    pub fn coin_type(&self) -> u32 {
        match self.network {
            Network::Bitcoin => 0,
            _ => 1,
        }
    }

    /// A derivation path is in scope iff it is exactly
    /// `purpose'/coin'/account'/change/index` with the policy's coin + account,
    /// purpose 84' (segwit v0) or 86' (taproot), and change ∈ {0, 1}.
    pub fn path_in_scope(&self, path: &[u32]) -> bool {
        path.len() == 5
            && (path[0] == (PURPOSE | HARDENED) || path[0] == (PURPOSE_TR | HARDENED))
            && path[1] == (self.coin_type() | HARDENED)
            && path[2] == (self.account | HARDENED)
            && (path[3] == 0 || path[3] == 1)
            && path[4] < HARDENED
    }
}

/// Key material a session needs, derived once at authorization from a seed that
/// is then dropped: the master fingerprint (public, used to spot our PSBT
/// entries), the SLIP-0019 ownership node, and the two account xprivs.
///
/// The xprivs are held in their serialized form inside `Zeroizing` so the
/// secrets are wiped when the session ends; `Xpriv` itself is `Copy` and cannot
/// zeroize on drop.
pub struct SessionKeys {
    pub fingerprint: Fingerprint,
    network: Network,
    ownership_key: Zeroizing<[u8; 32]>,
    account_wpkh: Zeroizing<[u8; XPRIV_ENCODED_LEN]>,
    account_tr: Zeroizing<[u8; XPRIV_ENCODED_LEN]>,
}

impl core::fmt::Debug for SessionKeys {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        // Never render key material, not even in a panic path.
        f.debug_struct("SessionKeys").field("fingerprint", &self.fingerprint).finish_non_exhaustive()
    }
}

impl SessionKeys {
    /// Derive from a BIP-39 seed. The caller drops the seed right after.
    pub fn derive(
        secp: &Secp256k1<All>,
        network: Network,
        account: u32,
        seed: &[u8],
    ) -> Result<Self, CoinjoinError> {
        let master = Xpriv::new_master(network, seed).map_err(|_| CoinjoinError::Derivation)?;
        let coin_type = match network {
            Network::Bitcoin => 0,
            _ => 1,
        };
        let account_key = |purpose: u32| -> Result<Zeroizing<[u8; XPRIV_ENCODED_LEN]>, CoinjoinError> {
            let path: DerivationPath = [purpose | HARDENED, coin_type | HARDENED, account | HARDENED]
                .iter()
                .map(|&i| ChildNumber::from(i))
                .collect::<Vec<_>>()
                .into();
            let xpriv = master.derive_priv(secp, &path).map_err(|_| CoinjoinError::Derivation)?;
            Ok(Zeroizing::new(xpriv.encode()))
        };
        Ok(SessionKeys {
            fingerprint: master.fingerprint(secp),
            network,
            ownership_key: Zeroizing::new(slip19::ownership_key(seed)),
            account_wpkh: account_key(PURPOSE)?,
            account_tr: account_key(PURPOSE_TR)?,
        })
    }

    pub fn ownership_key(&self) -> &[u8; 32] {
        &self.ownership_key
    }

    pub fn network(&self) -> Network {
        self.network
    }

    /// The address key for an in-scope path, derived from the cached account
    /// key rather than a master key. `path` must have passed `path_in_scope`.
    pub fn address_key(
        &self,
        secp: &Secp256k1<All>,
        path: &[u32],
    ) -> Result<(Xpriv, ScriptType), CoinjoinError> {
        let purpose = *path.first().ok_or(CoinjoinError::Derivation)?;
        let script_type = ScriptType::for_purpose(purpose);
        let encoded = match script_type {
            ScriptType::P2wpkh => &self.account_wpkh,
            ScriptType::P2tr => &self.account_tr,
        };
        let account = Xpriv::decode(&encoded[..]).map_err(|_| CoinjoinError::Derivation)?;
        let tail: DerivationPath = path[3..]
            .iter()
            .map(|&i| ChildNumber::from(i))
            .collect::<Vec<_>>()
            .into();
        let xpriv = account.derive_priv(secp, &tail).map_err(|_| CoinjoinError::Derivation)?;
        Ok((xpriv, script_type))
    }
}

#[derive(Debug)]
pub struct Session {
    /// Random session token. Unguessable on purpose: on a shared transport any
    /// host process that can reach the app's handler could otherwise burn
    /// rounds or fee budget under someone else's session.
    pub token: [u8; TOKEN_LEN],
    pub policy: Policy,
    pub authorized_at: Instant,
    pub rounds_used: u16,
    /// Sats given up so far across the session, checked against
    /// `policy.fee_budget_sats` before each signature.
    pub fee_spent: u64,
    pub keys: SessionKeys,
}

/// Length of a session token in bytes.
pub const TOKEN_LEN: usize = 16;

impl Session {
    pub fn is_expired(&self) -> bool {
        self.authorized_at.elapsed().as_secs() > self.policy.valid_for_secs as u64
            || self.rounds_used >= self.policy.max_rounds
    }

    /// Fee budget still available to spend.
    pub fn fee_remaining(&self) -> u64 {
        self.policy.fee_budget_sats.saturating_sub(self.fee_spent)
    }
}

#[derive(Debug, thiserror::Error, PartialEq)]
pub enum CoinjoinError {
    #[error("malformed PSBT")]
    MalformedPsbt,
    #[error("session expired or round budget exhausted")]
    SessionExpired,
    #[error("input {0} claims our key but the derivation is out of policy scope")]
    InputOutOfScope(usize),
    #[error("input {0} derivation does not match its scriptPubKey")]
    InputKeyMismatch(usize),
    #[error("input {0} is missing its witness utxo")]
    MissingWitnessUtxo(usize),
    #[error("output {0} derivation does not match its scriptPubKey")]
    OutputKeyMismatch(usize),
    #[error("no inputs of ours in this transaction")]
    NothingToSign,
    #[error("fee contribution {actual} exceeds the authorized session budget {max}")]
    FeeExceeded { actual: u64, max: u64 },
    #[error("bip32 derivation failed")]
    Derivation,
}

/// Outcome of a conforming signing run.
#[derive(Debug)]
pub struct SignedRound {
    pub psbt_bytes: Vec<u8>,
    pub our_inputs: Vec<usize>,
    /// Sats this round costs us; the caller adds it to `Session::fee_spent`.
    pub fee_contribution: u64,
}

/// Verify the round PSBT against the policy and sign our inputs (P2WPKH and
/// taproot key-spend).
///
/// "Ours" is decided by the BIP-32 derivations embedded in the PSBT: an entry
/// with our fingerprint must re-derive from our account keys to exactly the
/// input's scriptPubKey (a lying host gains nothing — wrong paths either fail
/// the pubkey check or derive a script that isn't in the transaction).
/// The self-spend guarantee is the fee bound: only outputs that provably pay
/// back to keys under the policy account count as credit, so
/// `our inputs - our outputs`, summed over the session, stays inside
/// `fee_budget_sats` no matter what the rest of the transaction looks like.
pub fn check_and_sign(
    secp: &Secp256k1<All>,
    session: &Session,
    psbt_bytes: &[u8],
) -> Result<SignedRound, CoinjoinError> {
    if session.is_expired() {
        return Err(CoinjoinError::SessionExpired);
    }
    let policy = &session.policy;
    let keys = &session.keys;

    let mut psbt = Psbt::deserialize(psbt_bytes).map_err(|_| CoinjoinError::MalformedPsbt)?;
    if psbt.inputs.len() != psbt.unsigned_tx.input.len()
        || psbt.outputs.len() != psbt.unsigned_tx.output.len()
    {
        return Err(CoinjoinError::MalformedPsbt);
    }

    // Classify inputs.
    struct OurInput {
        index: usize,
        xpriv: Xpriv,
        script_type: ScriptType,
        spk: ScriptBuf,
        amount: Amount,
    }
    let mut our_inputs: Vec<OurInput> = Vec::new();
    let mut our_input_sum = Amount::ZERO;
    for (index, input) in psbt.inputs.iter().enumerate() {
        let Some(path) = ours_in_derivation(input, keys.fingerprint) else {
            continue;
        };
        if !policy.path_in_scope(&path) {
            return Err(CoinjoinError::InputOutOfScope(index));
        }
        let utxo = input.witness_utxo.as_ref().ok_or(CoinjoinError::MissingWitnessUtxo(index))?;
        let (xpriv, script_type) = keys.address_key(secp, &path)?;
        let spk = slip19::script_pubkey(secp, &xpriv, script_type);
        if utxo.script_pubkey != spk {
            return Err(CoinjoinError::InputKeyMismatch(index));
        }
        our_input_sum += utxo.value;
        our_inputs.push(OurInput { index, xpriv, script_type, spk, amount: utxo.value });
    }
    if our_inputs.is_empty() {
        return Err(CoinjoinError::NothingToSign);
    }

    // Credit: outputs that provably pay back into the policy account.
    let mut our_output_sum = Amount::ZERO;
    for (index, output) in psbt.outputs.iter().enumerate() {
        let Some(path) = ours_in_output_derivation(output, keys.fingerprint) else {
            continue;
        };
        if !policy.path_in_scope(&path) {
            continue; // out-of-scope claim: simply not credited
        }
        let (xpriv, script_type) = keys.address_key(secp, &path)?;
        let spk = slip19::script_pubkey(secp, &xpriv, script_type);
        let tx_out = psbt.unsigned_tx.output.get(index).ok_or(CoinjoinError::MalformedPsbt)?;
        if tx_out.script_pubkey != spk {
            return Err(CoinjoinError::OutputKeyMismatch(index));
        }
        our_output_sum += tx_out.value;
    }

    let contribution = our_input_sum.to_sat().saturating_sub(our_output_sum.to_sat());
    let cumulative = session.fee_spent.saturating_add(contribution);
    if cumulative > policy.fee_budget_sats {
        return Err(CoinjoinError::FeeExceeded {
            actual: cumulative,
            max: policy.fee_budget_sats,
        });
    }

    // Taproot key-spend commits to every prevout, so all of them must be known
    // (Wasabi's coordinator sends witness-utxo-complete PSBTs).
    let signing_taproot = our_inputs.iter().any(|i| i.script_type == ScriptType::P2tr);
    let prevouts: Vec<TxOut> = if signing_taproot {
        psbt.inputs
            .iter()
            .enumerate()
            .map(|(index, input)| {
                input.witness_utxo.clone().ok_or(CoinjoinError::MissingWitnessUtxo(index))
            })
            .collect::<Result<_, _>>()?
    } else {
        Vec::new()
    };

    let tx = psbt.unsigned_tx.clone();
    let mut cache = SighashCache::new(&tx);
    let mut signed_indexes = Vec::with_capacity(our_inputs.len());
    for input in our_inputs {
        let witness = match input.script_type {
            ScriptType::P2wpkh => {
                // BIP-143 P2WPKH, SIGHASH_ALL.
                let sighash = cache
                    .p2wpkh_signature_hash(
                        input.index,
                        &input.spk,
                        input.amount,
                        EcdsaSighashType::All,
                    )
                    .map_err(|_| CoinjoinError::MalformedPsbt)?;
                let signature = ecdsa::Signature {
                    signature: secp.sign_ecdsa(
                        &Message::from_digest(sighash.to_byte_array()),
                        &input.xpriv.private_key,
                    ),
                    sighash_type: EcdsaSighashType::All,
                };
                let pubkey = CompressedPublicKey(input.xpriv.private_key.public_key(secp));
                Witness::p2wpkh(&signature, &pubkey.0)
            }
            ScriptType::P2tr => {
                // BIP-341 key spend with the BIP-86 tweak, SIGHASH_DEFAULT.
                let sighash = cache
                    .taproot_key_spend_signature_hash(
                        input.index,
                        &Prevouts::All(&prevouts),
                        TapSighashType::Default,
                    )
                    .map_err(|_| CoinjoinError::MalformedPsbt)?;
                let keypair = Keypair::from_secret_key(secp, &input.xpriv.private_key);
                let tweaked = keypair.tap_tweak(secp, None).to_keypair();
                let signature = taproot::Signature {
                    signature: secp.sign_schnorr_no_aux_rand(
                        &Message::from_digest(sighash.to_byte_array()),
                        &tweaked,
                    ),
                    sighash_type: TapSighashType::Default,
                };
                Witness::p2tr_key_spend(&signature)
            }
        };
        psbt.inputs[input.index].final_script_witness = Some(witness);
        signed_indexes.push(input.index);
    }

    Ok(SignedRound {
        psbt_bytes: psbt.serialize(),
        our_inputs: signed_indexes,
        fee_contribution: contribution,
    })
}

/// Path of the first input derivation entry carrying our fingerprint, covering
/// both the ECDSA (`bip32_derivation`) and taproot (`tap_key_origins`) maps.
fn ours_in_derivation(
    input: &ngwallet::bdk_wallet::bitcoin::psbt::Input,
    fingerprint: Fingerprint,
) -> Option<Vec<u32>> {
    input
        .bip32_derivation
        .values()
        .find(|(fp, _)| *fp == fingerprint)
        .map(|(_, path)| path)
        .or_else(|| {
            input
                .tap_key_origins
                .values()
                .map(|(_, origin)| origin)
                .find(|(fp, _)| *fp == fingerprint)
                .map(|(_, path)| path)
        })
        .map(to_indexes)
}

fn ours_in_output_derivation(
    output: &ngwallet::bdk_wallet::bitcoin::psbt::Output,
    fingerprint: Fingerprint,
) -> Option<Vec<u32>> {
    output
        .bip32_derivation
        .values()
        .find(|(fp, _)| *fp == fingerprint)
        .map(|(_, path)| path)
        .or_else(|| {
            output
                .tap_key_origins
                .values()
                .map(|(_, origin)| origin)
                .find(|(fp, _)| *fp == fingerprint)
                .map(|(_, path)| path)
        })
        .map(to_indexes)
}

fn to_indexes(path: &DerivationPath) -> Vec<u32> {
    path.into_iter().map(|child| u32::from(*child)).collect()
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use ngwallet::bdk_wallet::{
        bitcoin::{
            absolute::LockTime, hashes::Hash, secp256k1::XOnlyPublicKey, transaction::Version,
            OutPoint, Sequence, Transaction, TxIn, Txid,
        },
        keys::bip39::Mnemonic,
    };

    use super::*;

    const H: u32 = HARDENED;

    fn seed() -> Vec<u8> {
        Mnemonic::parse("all all all all all all all all all all all all")
            .unwrap()
            .to_seed("")
            .to_vec()
    }

    fn policy() -> Policy {
        Policy {
            network: Network::Bitcoin,
            account: 0,
            coordinator_id: b"CoinJoinCoordinatorIdentifier".to_vec(),
            fee_budget_sats: 10_000,
            max_rounds: 5,
            valid_for_secs: 3600,
        }
    }

    fn session_with(secp: &Secp256k1<All>, policy: Policy) -> Session {
        let keys = SessionKeys::derive(secp, policy.network, policy.account, &seed()).unwrap();
        Session {
            token: [7; TOKEN_LEN],
            policy,
            authorized_at: Instant::now(),
            rounds_used: 0,
            fee_spent: 0,
            keys,
        }
    }

    fn session(secp: &Secp256k1<All>) -> Session {
        session_with(secp, policy())
    }

    fn derive(secp: &Secp256k1<All>, path: &[u32]) -> (Xpriv, ScriptType, ScriptBuf) {
        let master = Xpriv::new_master(Network::Bitcoin, &seed()).unwrap();
        let derivation: DerivationPath =
            path.iter().map(|&i| ChildNumber::from(i)).collect::<Vec<_>>().into();
        let xpriv = master.derive_priv(secp, &derivation).unwrap();
        let script_type = ScriptType::for_purpose(path[0]);
        let spk = slip19::script_pubkey(secp, &xpriv, script_type);
        (xpriv, script_type, spk)
    }

    fn fingerprint(secp: &Secp256k1<All>) -> Fingerprint {
        Xpriv::new_master(Network::Bitcoin, &seed()).unwrap().fingerprint(secp)
    }

    fn foreign_spk() -> ScriptBuf {
        ScriptBuf::new_p2wpkh(
            &CompressedPublicKey::from_slice(&hex(
                "032ef68318c8f6aaa0adec0199c69901f0db7d3485eb38d9ad235221dc3d61154b",
            ))
            .unwrap()
            .wpubkey_hash(),
        )
    }

    /// Coinjoin-shaped PSBT: our input at `our_in_path`, a foreign input, our
    /// output at `our_out_path`, and a foreign output.
    fn fixture_psbt(
        secp: &Secp256k1<All>,
        our_in: u64,
        our_out: u64,
        our_in_path: &[u32],
        our_out_path: &[u32],
    ) -> Psbt {
        let fingerprint = fingerprint(secp);
        let (in_xpriv, in_type, in_spk) = derive(secp, our_in_path);
        let (out_xpriv, _out_type, out_spk) = derive(secp, our_out_path);

        let tx = Transaction {
            version: Version::TWO,
            lock_time: LockTime::ZERO,
            input: vec![
                TxIn {
                    previous_output: OutPoint::new(Txid::from_byte_array([1; 32]), 0),
                    sequence: Sequence::MAX,
                    ..Default::default()
                },
                TxIn {
                    previous_output: OutPoint::new(Txid::from_byte_array([2; 32]), 1),
                    sequence: Sequence::MAX,
                    ..Default::default()
                },
            ],
            output: vec![
                TxOut { value: Amount::from_sat(our_out), script_pubkey: out_spk },
                TxOut { value: Amount::from_sat(50_000), script_pubkey: foreign_spk() },
            ],
        };

        let mut psbt = Psbt::from_unsigned_tx(tx).unwrap();

        let input = &mut psbt.inputs[0];
        input.witness_utxo = Some(TxOut { value: Amount::from_sat(our_in), script_pubkey: in_spk });
        insert_derivation(&mut input.bip32_derivation, &mut input.tap_key_origins, secp, &in_xpriv,
            in_type, fingerprint, our_in_path);

        psbt.inputs[1].witness_utxo =
            Some(TxOut { value: Amount::from_sat(60_000), script_pubkey: foreign_spk() });

        let out_type = ScriptType::for_purpose(our_out_path[0]);
        let output = &mut psbt.outputs[0];
        insert_derivation(&mut output.bip32_derivation, &mut output.tap_key_origins, secp,
            &out_xpriv, out_type, fingerprint, our_out_path);

        psbt
    }

    #[allow(clippy::too_many_arguments)]
    fn insert_derivation(
        bip32: &mut std::collections::BTreeMap<
            ngwallet::bdk_wallet::bitcoin::secp256k1::PublicKey,
            (Fingerprint, DerivationPath),
        >,
        taps: &mut std::collections::BTreeMap<
            XOnlyPublicKey,
            (Vec<ngwallet::bdk_wallet::bitcoin::taproot::TapLeafHash>, (Fingerprint, DerivationPath)),
        >,
        secp: &Secp256k1<All>,
        xpriv: &Xpriv,
        script_type: ScriptType,
        fingerprint: Fingerprint,
        path: &[u32],
    ) {
        let derivation: DerivationPath =
            path.iter().map(|&i| ChildNumber::from(i)).collect::<Vec<_>>().into();
        match script_type {
            ScriptType::P2wpkh => {
                bip32.insert(xpriv.private_key.public_key(secp), (fingerprint, derivation));
            }
            ScriptType::P2tr => {
                let keypair = Keypair::from_secret_key(secp, &xpriv.private_key);
                let (xonly, _) = XOnlyPublicKey::from_keypair(&keypair);
                taps.insert(xonly, (vec![], (fingerprint, derivation)));
            }
        }
    }

    #[test]
    fn conforming_round_signs_our_input_only() {
        let secp = Secp256k1::new();
        let psbt = fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        let signed = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap();
        assert_eq!(signed.our_inputs, vec![0]);
        assert_eq!(signed.fee_contribution, 5_000);

        let out = Psbt::deserialize(&signed.psbt_bytes).unwrap();
        let witness = out.inputs[0].final_script_witness.as_ref().unwrap();
        assert_eq!(witness.len(), 2);
        assert!(out.inputs[1].final_script_witness.is_none(), "foreign input untouched");
    }

    /// Taproot round: BIP-86 key-spend witness, one 64-byte schnorr signature.
    #[test]
    fn taproot_round_signs_key_spend() {
        let secp = Secp256k1::new();
        let psbt = fixture_psbt(&secp, 100_000, 95_000, &[86 | H, H, H, 0, 0], &[86 | H, H, H, 1, 0]);
        let signed = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap();
        assert_eq!(signed.our_inputs, vec![0]);

        let out = Psbt::deserialize(&signed.psbt_bytes).unwrap();
        let witness = out.inputs[0].final_script_witness.as_ref().unwrap();
        assert_eq!(witness.len(), 1);
        assert_eq!(witness.nth(0).unwrap().len(), 64, "schnorr sig, SIGHASH_DEFAULT");
    }

    /// A taproot signature commits to every prevout, so an incomplete PSBT must
    /// be refused rather than signed over guessed amounts.
    #[test]
    fn taproot_round_needs_all_prevouts() {
        let secp = Secp256k1::new();
        let mut psbt =
            fixture_psbt(&secp, 100_000, 95_000, &[86 | H, H, H, 0, 0], &[86 | H, H, H, 1, 0]);
        psbt.inputs[1].witness_utxo = None;
        let err = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::MissingWitnessUtxo(1));
    }

    #[test]
    fn fee_above_cap_rejected() {
        let secp = Secp256k1::new();
        // 100k in, 80k back: 20k contribution > 10k budget
        let psbt = fixture_psbt(&secp, 100_000, 80_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        let err = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::FeeExceeded { actual: 20_000, max: 10_000 });
    }

    /// The budget is the session total, not a per-round allowance: rounds that
    /// each fit on their own still stop once the sum would exceed it.
    #[test]
    fn fee_budget_is_cumulative() {
        let secp = Secp256k1::new();
        let mut session = session(&secp);
        let psbt = fixture_psbt(&secp, 100_000, 94_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0])
            .serialize();

        let first = check_and_sign(&secp, &session, &psbt).unwrap();
        assert_eq!(first.fee_contribution, 6_000);
        session.fee_spent += first.fee_contribution;
        session.rounds_used += 1;

        // A second identical round would take the session to 12k > 10k budget.
        let err = check_and_sign(&secp, &session, &psbt).unwrap_err();
        assert_eq!(err, CoinjoinError::FeeExceeded { actual: 12_000, max: 10_000 });
        assert_eq!(session.fee_remaining(), 4_000);
    }

    #[test]
    fn output_to_wrong_account_not_credited() {
        let secp = Secp256k1::new();
        // "our" output claims account 1 — out of policy scope, so not credited:
        // contribution = full 100k > budget.
        let psbt =
            fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, 1 | H, 1 | H, 1, 0]);
        let err = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap_err();
        assert!(matches!(err, CoinjoinError::FeeExceeded { .. }));
    }

    #[test]
    fn lying_output_derivation_rejected() {
        let secp = Secp256k1::new();
        let mut psbt =
            fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        // host lies: claims the foreign output pays to our change path
        let (xpriv, script_type, _) = derive(&secp, &[84 | H, H, H, 1, 5]);
        let fp = fingerprint(&secp);
        let output = &mut psbt.outputs[1];
        insert_derivation(&mut output.bip32_derivation, &mut output.tap_key_origins, &secp, &xpriv,
            script_type, fp, &[84 | H, H, H, 1, 5]);
        let err = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::OutputKeyMismatch(1));
    }

    /// An input whose claimed path derives to a different script than the utxo
    /// it spends is a lying host, not a signable input.
    #[test]
    fn input_script_mismatch_rejected() {
        let secp = Secp256k1::new();
        let mut psbt =
            fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        psbt.inputs[0].witness_utxo =
            Some(TxOut { value: Amount::from_sat(100_000), script_pubkey: foreign_spk() });
        let err = check_and_sign(&secp, &session(&secp), &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::InputKeyMismatch(0));
    }

    #[test]
    fn expired_session_rejected() {
        let secp = Secp256k1::new();
        let psbt = fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        let mut session = session(&secp);
        session.authorized_at = Instant::now() - Duration::from_secs(7200);
        let err = check_and_sign(&secp, &session, &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::SessionExpired);
    }

    #[test]
    fn round_budget_exhaustion_rejected() {
        let secp = Secp256k1::new();
        let psbt = fixture_psbt(&secp, 100_000, 95_000, &[84 | H, H, H, 0, 0], &[84 | H, H, H, 1, 0]);
        let mut session = session(&secp);
        session.rounds_used = session.policy.max_rounds;
        let err = check_and_sign(&secp, &session, &psbt.serialize()).unwrap_err();
        assert_eq!(err, CoinjoinError::SessionExpired);
    }

    #[test]
    fn policy_wire_roundtrip() {
        let p = policy();
        let bytes = p.serialize();
        let (parsed, consumed) = Policy::parse(&bytes).unwrap();
        assert_eq!(parsed, p);
        assert_eq!(consumed, bytes.len());
    }

    /// Session keys must reproduce the same addresses a master key would, or
    /// the device would sign for scripts the host never funded.
    #[test]
    fn session_keys_match_master_derivation() {
        let secp = Secp256k1::new();
        let keys = SessionKeys::derive(&secp, Network::Bitcoin, 0, &seed()).unwrap();
        assert_eq!(keys.fingerprint, fingerprint(&secp));
        for path in [[84 | H, H, H, 1, 3], [86 | H, H, H, 0, 7]] {
            let (expected, script_type, _) = derive(&secp, &path);
            let (actual, actual_type) = keys.address_key(&secp, &path).unwrap();
            assert_eq!(actual.private_key.secret_bytes(), expected.private_key.secret_bytes());
            assert_eq!(actual_type, script_type);
        }
    }

    fn hex(s: &str) -> Vec<u8> {
        (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
    }
}
