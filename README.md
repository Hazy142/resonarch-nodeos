# resonarch-nodeos

Minimal, immutable compute-appliance OS for dedicated FiberFEC / distributed-runtime nodes.

## NODEOS-001 target

First physical target: Intel Core i7-4770K + NVIDIA GeForce GTX 1070 Ti, dual-booting beside Windows and exposing CPU, RAM, storage, NIC and CUDA capability to the main workstation.

NodeOS is **not** a second tensor runtime. llama.cpp / GGML remains compute authority.

## Pinned substrate

- Buildroot **2026.08**
- verified upstream release commit `d5180309b1b66ef3b8eaccca70ad69be8e0729a1`
- Linux **6.12.47** baseline from Buildroot's x86_64 EFI profile
- x86_64 UEFI / GRUB2 / GPT `disk.img`
- glibc + BusyBox/SysV
- direct LAN default: NodeOS `192.168.77.2/30`, workstation `192.168.77.1/30`

Build on Linux/WSL2:

```bash
./scripts/build-nodeos.sh
```

Fast configuration gate:

```bash
./scripts/build-nodeos.sh --configure-only
```

Artifacts are emitted to `out/artifacts/` with SHA-256 build evidence.

## CUDA boundary

The public repo does not redistribute NVIDIA proprietary binaries. The base image boots without them but remains fail-closed in `DISCOVERED` state. A verified vendor payload plus a successful physical `sm_61` CUDA probe transitions the node to `READY`.

Compile the probe using CUDA 12.x:

```bash
./scripts/build-cuda-probe.sh
```

See `vendor/nvidia/README.md` and `docs/NODEOS-001-bringup.md`.

## Milestones

- **NODEOS-001** — boot image, direct LAN, inventory, GTX 1070 Ti discovery and CUDA readiness gate.
- **NODEOS-002** — authenticated node identity, discovery and health telemetry.
- **NODEOS-003** — FiberFEC object plane and RAM↔GPU residency.
- **NODEOS-004** — CPU == RTX 4060 == GTX 1070 Ti cross-generation gate.
- **NODEOS-005** — llama.cpp/GGML remote consumer.
- **NODEOS-006** — immutable 24/7 appliance, watchdog and recovery.
