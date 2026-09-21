# resonarch-nodeos

Minimal, immutable compute-appliance OS for dedicated FiberFEC / distributed-runtime nodes.

## Purpose

NodeOS turns an otherwise general-purpose PC into a deterministic network compute appliance. The first target is a dual-boot node built around an Intel i7-4770K and NVIDIA GTX 1070 Ti.

NodeOS is intentionally **not** a second tensor runtime. llama.cpp / GGML remains compute authority. NodeOS exposes bounded CPU, RAM, storage, network and CUDA resources to the distributed runtime and provides evidence, health and lifecycle control.

## Initial architecture

```text
Main workstation / PLEXUS
        |
        | direct LAN / later FiberTunnel
        v
NodeOS appliance
  |- node-agent
  |- FiberFEC object/residency services
  |- CPU / RAM staging tier
  |- local persistent object cache
  |- CUDA execution tier
  '- watchdog / health / evidence
```

## First hardware profile

- Intel Core i7-4770K
- NVIDIA GeForce GTX 1070 Ti
- Pascal / CUDA compute capability 6.1
- 1 GbE direct LAN baseline
- Windows preserved as alternate dual-boot target

## Milestones

- **NODEOS-001** — reproducible minimal boot image; CPU, NIC and GPU visible.
- **NODEOS-002** — node identity, discovery, health and resource inventory.
- **NODEOS-003** — FiberFEC object plane and RAM↔GPU residency.
- **NODEOS-004** — cross-generation determinism gate: CPU == RTX 4060 == GTX 1070 Ti.
- **NODEOS-005** — llama.cpp/GGML remote consumer integration.
- **NODEOS-006** — immutable 24/7 appliance mode, watchdog and recovery.

## Design rules

1. Buildroot/Linux is the initial substrate; no custom kernel-from-scratch requirement.
2. GGML decides compute semantics and tensor need.
3. FiberFEC manages verified object identity, transfer, repair, lease and residency.
4. NodeOS must fail closed on identity, evidence or residency contract violations.
5. Cross-node behavior must be reproducible and evidence-producing.
6. Windows remains untouched and independently bootable.

## Repository layout

```text
buildroot/        pinned Buildroot integration and board profile
config/           immutable NodeOS configuration
contracts/        node identity / capability / evidence contracts
docs/             architecture decisions and bring-up notes
node-agent/       appliance control-plane daemon
scripts/          build, image and deployment helpers
tests/            contract and cross-node acceptance gates
```

Status: bootstrap repository; implementation starts with NODEOS-001.
