# NVIDIA vendor payload boundary

NVIDIA proprietary driver/runtime binaries are intentionally not committed here.

NODEOS-001 builds a bootable base image without them, but the node remains `DISCOVERED` rather than `READY` until a verified payload is supplied.

## Expected layout

```text
vendor/nvidia/payload/
  manifest.json
  rootfs/
    usr/bin/nvidia-smi
    usr/lib/... driver/runtime libraries ...
    usr/libexec/nodeos-cuda-probe
    lib/modules/... NVIDIA kernel modules ...
```

The directory is treated as a rootfs overlay and is copied into the image only after `scripts/verify-nvidia-payload.py` succeeds.

Requirements:
- GTX 1070 Ti / Pascal / compute capability 6.1
- CUDA probe compiled for `sm_61`
- every supplied binary SHA-256-bound by `manifest.json`
- NVIDIA kernel modules matched to the pinned NodeOS kernel ABI
- no binary trusted merely because it exists

The Buildroot 2026.08 in-tree NVIDIA package is deliberately not enabled for NodeOS: that recipe is pinned to an old 390-series driver. NodeOS keeps the current proprietary driver/runtime as an explicit vendor integration boundary.
