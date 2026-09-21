# Buildroot integration

NODEOS-001 will pin a Buildroot release and produce a reproducible x86_64 appliance image.

Planned board profile:

```text
buildroot/
  external/
    board/resonarch/haswell-gtx1070ti/
    configs/resonarch_haswell_gtx1070ti_defconfig
```

The running image should contain only the minimum services needed for networking, NVIDIA/CUDA userspace, NodeOS node-agent, FiberFEC integration, evidence export and watchdog behavior.

The proprietary NVIDIA driver path for Pascal is expected to be pinned separately from the OS image configuration.
