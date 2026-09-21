#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BR_VERSION="2026.08"
BR_COMMIT="d5180309b1b66ef3b8eaccca70ad69be8e0729a1"
CACHE="$ROOT/.cache"
BR_DIR="$CACHE/buildroot-$BR_VERSION"
OUT="$ROOT/out/haswell-gtx1070ti"
ART="$ROOT/out/artifacts"
CONFIG_ONLY=0

if [[ "${1:-}" == "--configure-only" ]]; then CONFIG_ONLY=1
elif [[ -n "${1:-}" ]]; then echo "usage: $0 [--configure-only]" >&2; exit 2
fi

for cmd in git make gcc python3 rsync; do
  command -v "$cmd" >/dev/null || { echo "missing host command: $cmd" >&2; exit 1; }
done

mkdir -p "$CACHE" "$OUT" "$ART"
if [[ ! -d "$BR_DIR/.git" ]]; then
  git clone --depth 1 --branch "$BR_VERSION" https://github.com/buildroot/buildroot.git "$BR_DIR"
fi

actual="$(git -C "$BR_DIR" rev-parse HEAD)"
if [[ "$actual" != "$BR_COMMIT" ]]; then
  echo "Buildroot commit mismatch: expected $BR_COMMIT, got $actual" >&2
  exit 1
fi

PAYLOAD="$ROOT/vendor/nvidia/payload"
if [[ -f "$PAYLOAD/manifest.json" ]]; then
  python3 "$ROOT/scripts/verify-nvidia-payload.py" "$PAYLOAD"
  export NODEOS_NVIDIA_PAYLOAD="$PAYLOAD"
else
  unset NODEOS_NVIDIA_PAYLOAD || true
  echo "NodeOS: no NVIDIA vendor payload; base image will boot but CUDA readiness fails closed."
fi

make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" resonarch_haswell_gtx1070ti_defconfig
make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" olddefconfig

if [[ "$CONFIG_ONLY" -eq 1 ]]; then echo "NODEOS-001 configuration: PASS"; exit 0; fi

JOBS="${JOBS:-$(nproc)}"
make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" -j"$JOBS"

for name in disk.img bzImage rootfs.ext2; do
  src="$OUT/images/$name"
  [[ -f "$src" ]] || { echo "missing build artifact: $src" >&2; exit 1; }
  cp -f "$src" "$ART/$name"
done

python3 "$ROOT/scripts/hash-artifacts.py" "$ART" "$BR_VERSION" "$BR_COMMIT"
echo "NODEOS-001 build complete: $ART/disk.img"
