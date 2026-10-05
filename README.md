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

## Dell G15 5530 USB/headless pilot (experimental)

`--profile=dell-g15-5530` is an isolated Buildroot configuration for the RTX 4060 Laptop GPU (`sm_89`), wired DHCP with direct-LAN fallback, and a **key-only** SSH listener that remains closed unless an individual `NODEOS_SSH_PUBKEY` is supplied at build time. The existing GTX 1070 Ti profile remains the default. Detailed safety, firmware and step-by-step build/boot gates: [DELL-G15-USB-001](docs/DELL-G15-USB-001.md).

**Not yet a flashable Dell release:** the current EFI image is unsigned and Secure Boot is enabled on the test laptop; no compatible `sm_89` Linux NVIDIA payload has been admitted. The read-only Windows USB check `tools/dell-usb-preflight.ps1` identifies disks but never flashes them or changes UEFI settings. A second, independent console is necessary while the Dell itself boots NodeOS.

## Compute ONE v1 prototype (O1/O2, CPU simulation only)

The separate `codex/compute-one-v1-protocol` branch introduces an independently versioned bounded tensor/WorkUnit protocol, fail-closed test lease verifier, deterministic CPU reference and **separate subprocess** stub workers for the planned Pascal CUDA12 and Turing/Ada CUDA13 backends. No CUDA/NVIDIA libraries are loaded and no physical GPU speedup or driver replacement is implied. From the repository root: `python -m compute_one.gateway` and `python scripts/verify-layout.py`. Details and explicitly open production security/physical GPU gates: [COMPUTE-ONE-001](docs/COMPUTE-ONE-001.md).

On an **explicitly authorized Windows Dell RTX4060 only**, two optional independent physical evidence probes are available: `python -m compute_one.physical_probe` (CUDA driver memory roundtrip) and `python -m compute_one.cuda13_kernel_probe --real --nvrtc PATH_TO_INSTALLED_NVRTC_DLL` (real CUDA13.2 NVRTC sm89 kernel launch). Both pass on the inspected Dell; neither is wired into the simulated ONE gateway or installed drivers. Physical run receipts: `docs/evidence/`.

### Compute ONE real Ada O3 (owner lab only)

Use python -m compute_one.real_demo --real --nvrtc PATH_TO_INSTALLED_CUDA13_NVRTC_DLL to drive three physical sm89 kernels through the leased, hash-checked Compute ONE gateway. The approved CUDA source is static and local, not arbitrary external PTX. GPU outputs are compared byte-for-byte with an independent CPU oracle. See docs/COMPUTE-ONE-001.md for evidence, operational commands and explicit open production-security gates. This is not yet APE/mTLS secured or installed in NodeOS Linux.
