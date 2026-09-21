# Acceptance gates

## NODEOS-001
- image builds reproducibly from pinned inputs;
- boots on x86_64 target;
- NIC comes up;
- CPU and RAM inventory exported;
- GTX 1070 Ti detected;
- CUDA smoke test passes.

## NODEOS-004 cross-generation gate
The same frozen FiberRAY/KTS test vector must satisfy:

```text
CPU reference
  == RTX 4060 / sm_89
  == GTX 1070 Ti / sm_61
```

Required evidence includes device identity, build/toolchain identity, final-state digest, full-trace digest and exact replay equality.
