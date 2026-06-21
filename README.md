# coinjoin.nl
Self-hosted webpage for monitoring round status of WabiSabi coordinator server and explaining how to add the coordinator.

<img src="coinjoin_website.png" alt="Preview of coinjoin.nl website" width="100%" href="https://coinjoin.nl/"/>

# How to run a wabisabi coordinator yourself
See my tutorial to learn how: 

https://planb.academy/en/tutorials/privacy/on-chain/coinjoin-coordinator-3e26b5be-d1f8-4253-9297-0e163c19b387

# Track coinjoin round statistics
Use coinjoin-stats.py to track various statistics:
  - sync   create table if needed, scan the log, insert any txids not yet stored
         (idempotent; only new txids hit bitcoind).
  - stats  print aggregate mining-fee / coordinator-fee numbers.
  - latest show the latest N coinjoin rounds.
  - lowest show the 5 lowest fee-rate coinjoin rounds.
