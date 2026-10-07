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
    # Flat digest list so the target can re-verify the payload (sha256sum -c).
    python3 - "$NODEOS_NVIDIA_PAYLOAD/manifest.json" >"$TARGET_DIR/etc/nodeos/nvidia-payload.sha256" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
for entry in manifest.get("files", []):
    path = entry["path"]
    if not path.startswith("rootfs/"):
        continue
    print("%s  %s" % (entry["sha256"], path[len("rootfs"):]))
PY
else
    : > "$TARGET_DIR/etc/nodeos/nvidia-payload-missing"
fi

# ---- NODEOS-001-LAB -------------------------------------------------------
# Build identity, readable on the target and copied into every evidence bundle.
BUILD_COMMIT="${NODEOS_BUILD_COMMIT:-}"
if [ -z "$BUILD_COMMIT" ] && [ -n "${BR2_EXTERNAL_NODEOS_PATH:-}" ]; then
    BUILD_COMMIT="$(git -C "$BR2_EXTERNAL_NODEOS_PATH" rev-parse HEAD 2>/dev/null || true)"
fi
{
    printf 'NODEOS_BUILD_COMMIT=%s\n' "${BUILD_COMMIT:-unknown}"
    printf 'NODEOS_BUILD_DIRTY=%s\n' "${NODEOS_BUILD_DIRTY:-0}"
    printf 'NODEOS_BUILDROOT_COMMIT=%s\n' "${NODEOS_BUILDROOT_COMMIT:-unknown}"
    printf 'NODEOS_BUILD_PROFILE=%s\n' "${NODEOS_BUILD_PROFILE:-unknown}"
} > "$TARGET_DIR/etc/nodeos/build-info"

# Never run the permissive stock Dropbear service. Key-only SSH is S55nodeos-ssh
# and stays closed unless an individual public key is baked in.
rm -f "$TARGET_DIR/etc/init.d/S50dropbear"
KEY="$(printenv NODEOS_SSH_PUBKEY || true)"
if [ -n "$KEY" ]; then
    [ -f "$KEY" ] || { echo "NODEOS_SSH_PUBKEY is not a file: $KEY" >&2; exit 1; }
    [ "$(wc -l < "$KEY")" -eq 1 ] || { echo "NODEOS_SSH_PUBKEY must be exactly one line" >&2; exit 1; }
    grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+([[:space:]]|$)' "$KEY" ||
        { echo "NODEOS_SSH_PUBKEY must be an ssh-ed25519 public key" >&2; exit 1; }
    cp "$KEY" "$TARGET_DIR/etc/nodeos/authorized_keys"
    chmod 600 "$TARGET_DIR/etc/nodeos/authorized_keys"
fi

# tty1 becomes the NodeOS lab console; tty2 keeps a plain login as fallback.
INITTAB="$TARGET_DIR/etc/inittab"
if [ -f "$INITTAB" ]; then
    sed -i '/^tty1::/d;/^tty2::/d' "$INITTAB"
    printf '%s\n' \
        'tty1::respawn:/usr/sbin/nodeos-console' \
        'tty2::respawn:/sbin/getty -L tty2 0 vt100' >> "$INITTAB"
fi
