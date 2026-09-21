# ADR-0001: NodeOS scope and authority

## Status
Accepted for initial implementation.

## Decision
NodeOS is a minimal Linux-based compute appliance, not a general-purpose desktop OS and not a replacement for llama.cpp/GGML.

## Authority boundaries
- **PLEXUS**: jobs, user intent, workspace and orchestration.
- **llama.cpp / GGML**: model semantics, tensor demand, kernels, KV-cache, logits and sampling.
- **NodeOS adapter**: translates compute demand into residency/resource intents.
- **FiberFEC**: object identity, verification, transfer, repair, lease and eviction.
- **CUDA / CPU**: physical execution.

## Initial substrate
Use a pinned Buildroot/Linux image so the project gets deterministic boot artifacts, drivers and networking without creating a new kernel ecosystem.

## Non-goals
- No desktop environment.
- No package manager on the running appliance.
- No browser or interactive workstation role.
- No independent transformer executor.
- No claim that remote VRAM is equivalent to local VRAM.

## First target
Intel i7-4770K + NVIDIA GTX 1070 Ti as a dedicated LAN-connected node while preserving Windows as a separate boot option.
