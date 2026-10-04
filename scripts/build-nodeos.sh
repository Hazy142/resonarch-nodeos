#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BR_VERSION="2026.08"
BR_COMMIT="d5180309b1b66ef3b8eaccca70ad69be8e0729a1"
CACHE="$ROOT/.cache"
BR_DIR="$CACHE/buildroot-$BR_VERSION"
PROFILE=haswell-gtx1070ti
CONFIG_ONLY=0
for arg in "$@"; do
 case "$arg" in
  --configure-only) CONFIG_ONLY=1 ;;
  --profile=dell-g15-5530) PROFILE=dell-g15-5530 ;;
  --profile=haswell-gtx1070ti) PROFILE=haswell-gtx1070ti ;;
  *) echo "unknown option: $arg" >&2; exit 2 ;;
 esac
done
OUT="$ROOT/out/$PROFILE"
ART="$ROOT/out/artifacts/$PROFILE"
if [ "$PROFILE" = "dell-g15-5530" ]; then
 DEFCONFIG=resonarch_dell_g15_5530_defconfig
 CAPABILITY=8.9
 MANIFEST_PROFILE=dell-g15-5530-v1
else
 DEFCONFIG=resonarch_haswell_gtx1070ti_defconfig
 CAPABILITY=6.1
 MANIFEST_PROFILE=haswell-gtx1070ti-v1
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
  python3 "$ROOT/scripts/verify-nvidia-payload.py" "$PAYLOAD" "$CAPABILITY" "$MANIFEST_PROFILE"
  export NODEOS_NVIDIA_PAYLOAD="$PAYLOAD"
else
  unset NODEOS_NVIDIA_PAYLOAD || true
  echo "NodeOS: no NVIDIA vendor payload; base image will boot but CUDA readiness fails closed."
fi

make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" "$DEFCONFIG"
make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" olddefconfig

if [[ "$CONFIG_ONLY" -eq 1 ]]; then
  echo "NODEOS-001 $PROFILE configuration: PASS"
  # The existing protected CI workflow invokes the default profile.
  # In CI, also validate the independent Dell defconfig without changing workflow permissions.
  if [[ "$PROFILE" == "haswell-gtx1070ti" && "${GITHUB_ACTIONS:-}" == "true" ]]; then
    "$0" --configure-only --profile=dell-g15-5530
  fi
  exit 0
fi

JOBS="${JOBS:-$(nproc)}"
make -C "$BR_DIR" BR2_EXTERNAL="$ROOT/buildroot" O="$OUT" -j"$JOBS"

for name in disk.img bzImage rootfs.ext2; do
  src="$OUT/images/$name"
  [[ -f "$src" ]] || { echo "missing build artifact: $src" >&2; exit 1; }
  cp -f "$src" "$ART/$name"
done

python3 "$ROOT/scripts/hash-artifacts.py" "$ART" "$BR_VERSION" "$BR_COMMIT" "$MANIFEST_PROFILE"
echo "NODEOS-001 build complete: $ART/disk.img"
