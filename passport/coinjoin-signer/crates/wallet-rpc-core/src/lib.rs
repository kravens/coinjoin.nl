// SPDX-FileCopyrightText: 2026 Kevin Ravensberg <kevinravensberg@proton.me>
// SPDX-License-Identifier: MIT OR GPL-3.0-or-later

//! Host-independent coinjoin remote-signing core for Passport Prime.
//!
//! Everything here is functional over its inputs (keys, policies, PSBTs, byte
//! frames) with no KeyOS server, USB, or GUI dependency: the Coinjoin Signer
//! app wraps it with the `os/security` seed source and the on-device approval
//! UI, the KeyOS `wallet-rpc` server crate wraps the same code with a USB HID
//! transport, and the hosted tests drive it directly.

pub mod coinjoin;
pub mod frames;
pub mod protocol;
pub mod slip19;

// Re-exports so consumers don't need to pin these dependencies themselves:
// bitcoin types surface in our public API (Network, bip39, Psbt, ...) and
// `Zeroizing` is part of the `Backend::seed` contract.
pub use ngwallet;
pub use zeroize;
