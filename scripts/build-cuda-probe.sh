#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${NODEOS_PROFILE:-haswell-gtx1070ti}"
if [[ "$PROFILE" == "dell-g15-5530" ]]; then GPU_ARCH=sm_89
elif [[ "$PROFILE" == "haswell-gtx1070ti" ]]; then GPU_ARCH=sm_61
else echo "unsupported NodeOS GPU profile" >&2; exit 2
fi
OUT="${1:-$ROOT/vendor/nvidia/payload/rootfs/usr/libexec/nodeos-cuda-probe}"
command -v nvcc >/dev/null || { echo "nvcc is required" >&2; exit 1; }
version_line="$(nvcc --version | grep 'release ' | tail -n1)"
major="$(printf '%s' "$version_line" | sed -n 's/.*release \([0-9][0-9]*\)\..*/\1/p')"
if [[ -z "$major" || ( "$GPU_ARCH" == "sm_61" && "$major" -ge 13 ) ]]; then
  echo "sm_61 offline compilation requires CUDA 12.x-or-older; found: $version_line" >&2
  exit 1
fi
mkdir -p "$(dirname "$OUT")"
nvcc -std=c++17 -O2 -arch="$GPU_ARCH" -cudart=static "$ROOT/tools/cuda/nodeos_cuda_probe.cu" -o "$OUT"
sha256sum "$OUT"
