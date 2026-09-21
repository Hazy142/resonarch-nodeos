#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: verify-nvidia-payload.py PAYLOAD_DIR")
root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "manifest.json").read_text())
if manifest.get("schema") != "resonarch.nodeos.nvidia-payload.v1":
    raise SystemExit("unexpected NVIDIA payload schema")
if manifest.get("target_compute_capability") != "6.1":
    raise SystemExit("NVIDIA payload is not bound to compute capability 6.1")
for entry in manifest.get("files", []):
    path = root / entry["path"]
    if not path.is_file():
        raise SystemExit(f"payload file missing: {entry['path']}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        raise SystemExit(f"payload digest mismatch: {entry['path']}")
if not (root / "rootfs/usr/libexec/nodeos-cuda-probe").is_file():
    raise SystemExit("payload must include rootfs/usr/libexec/nodeos-cuda-probe")
print("NODEOS NVIDIA payload: PASS")
