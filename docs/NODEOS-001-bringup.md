# NODEOS-001 bring-up

NODEOS-001 is a physical PASS only when:

1. UEFI boots the generated `disk.img`.
2. Linux starts with the pinned Buildroot/kernel profile.
3. A non-loopback NIC is discovered and configured.
4. `/run/nodeos/capability.json` identifies CPU, memory, NIC and NVIDIA PCI function.
5. The proprietary NVIDIA payload is digest-bound by its manifest.
6. `nvidia-smi -L` succeeds.
7. `nodeos-cuda-smoke` executes the `sm_61` probe and returns PASS.
8. `/run/nodeos/ready` exists only after the CUDA smoke gate passes.

A base image that boots without the NVIDIA payload is useful for bring-up but is **not** a full physical PASS.

## Direct LAN baseline

```text
NodeOS:       192.168.77.2/30
Workstation:  192.168.77.1/30
```

No default route is required for the isolated point-to-point test.

## Physical inspection

```sh
cat /run/nodeos/capability.json
lspci -nn
ip addr
nodeos-cuda-smoke
test -e /run/nodeos/ready
```
