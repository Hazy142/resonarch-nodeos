#!/bin/sh
set -eu
TARGET_DIR="$1"
mkdir -p "$TARGET_DIR/etc/nodeos"

if [ -n "${NODEOS_NVIDIA_PAYLOAD:-}" ]; then
    if [ ! -d "$NODEOS_NVIDIA_PAYLOAD/rootfs" ]; then
        echo "NODEOS_NVIDIA_PAYLOAD has no rootfs/ directory" >&2
        exit 1
    fi
    cp -a "$NODEOS_NVIDIA_PAYLOAD/rootfs/." "$TARGET_DIR/"
    cp "$NODEOS_NVIDIA_PAYLOAD/manifest.json" "$TARGET_DIR/etc/nodeos/nvidia-payload-manifest.json"
    rm -f "$TARGET_DIR/etc/nodeos/nvidia-payload-missing"
else
    : > "$TARGET_DIR/etc/nodeos/nvidia-payload-missing"
fi
