# node-agent

The node-agent is the NodeOS control-plane daemon.

Initial responsibilities:

- derive and expose stable node identity;
- report CPU, RAM, NIC and CUDA capability;
- perform startup self-tests;
- advertise readiness to the main workstation;
- accept bounded job/residency intents;
- supervise worker processes;
- export health, temperature, lease and evidence status;
- fail closed on contract mismatch.

The node-agent must not decide tensor semantics or replace GGML scheduling.
