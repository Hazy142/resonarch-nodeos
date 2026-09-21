#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/vendor/nvidia/payload/rootfs/usr/libexec/nodeos-cuda-probe}"
command -v nvcc >/dev/null || { echo "nvcc is required" >&2; exit 1; }
version_line="$(nvcc --version | grep 'release ' | tail -n1)"
major="$(printf '%s' "$version_line" | sed -n 's/.*release \([0-9][0-9]*\)\..*/\1/p')"
if [[ -z "$major" || "$major" -ge 13 ]]; then
  echo "sm_61 offline compilation requires CUDA 12.x-or-older; found: $version_line" >&2
  exit 1
fi
mkdir -p "$(dirname "$OUT")"
nvcc -std=c++17 -O2 -arch=sm_61 -cudart=static "$ROOT/tools/cuda/nodeos_cuda_probe.cu" -o "$OUT"
sha256sum "$OUT"
