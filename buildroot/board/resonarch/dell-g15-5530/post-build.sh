#!/bin/sh
set -eu
TARGET_DIR="$1"
# Never run the permissive stock Dropbear service. No password logins.
rm -f "$TARGET_DIR/etc/init.d/S50dropbear"
KEY="$(printenv NODEOS_SSH_PUBKEY || true)"
if [ -n "$KEY" ]; then
 [ -f "$KEY" ] || exit 1
 [ "$(wc -l < "$KEY")" -eq 1 ] || exit 1
 grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+([[:space:]]|$)' "$KEY" || exit 1
 mkdir -p "$TARGET_DIR/etc/nodeos"
 cp "$KEY" "$TARGET_DIR/etc/nodeos/authorized_keys"
 chmod 600 "$TARGET_DIR/etc/nodeos/authorized_keys"
fi
