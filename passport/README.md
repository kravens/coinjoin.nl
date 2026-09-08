# Passport Prime coinjoin signer

The app, its wallet-rpc protocol and the signing engine live in their own repository:

- https://github.com/kravens/passport-coinjoin — the KeyOS SDK app (Coinjoin Signer), `crates/wallet-rpc-core`
  (the protocol v2 spec the Wasabi client is tested against), and the request to Foundation for the USB
  interface permission a third-party app needs.
- https://github.com/kravens/KeyOS/tree/feature/passport-coinjoin — the same transport as a KeyOS system
  service (`os/wallet-rpc`), and the earlier QuantumLink proposal.

The Wasabi side is `WalletWasabi/Hwi/Passport` on https://github.com/kravens/WalletWasabi (Preview releases).
