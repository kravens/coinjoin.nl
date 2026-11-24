# Wasabi Preview Mobile privacy policy

Last updated: 24 November 2026

Wasabi Preview Mobile (iOS and Android) is a companion for Wasabi Wallet running on your own computer. It collects no data: the developer runs no server, and the app contains no analytics, advertising, crash reporting or tracking.

## What the app connects to

The app talks only to the Wasabi Wallet JSON-RPC server on your own computer, at the address you enter:

- over your own Tailscale network, or
- over a Tor onion service that you set up with client authorisation. For this the app connects to the Tor network (directory servers and relays) through the Tor client (Arti) built into the app.

Your balance, coins, transactions, addresses and payments travel only between your phone and your computer. Nothing is sent to the developer or to any other third party.

## What stays on your phone

- The connection details you enter (address, RPC user and password, onion key) are kept in the iOS Keychain or Android Keystore-backed storage.
- Display settings such as the selected wallet, theme and privacy choice are kept in the app's local settings.

Deleting the app deletes this data.

## Permissions

- **Camera**: scans bitcoin QR codes. Images are processed on the phone and never stored or sent.
- **Face ID / biometrics**: confirms each payment. Apple or Google handle the biometric check; the app only learns whether it succeeded.
- **Local network**: reaches Wasabi Wallet on your computer.

## Contact

Questions: open an issue at https://github.com/kravens/coinjoin.nl/issues.
