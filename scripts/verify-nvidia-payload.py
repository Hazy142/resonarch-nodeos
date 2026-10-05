#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

if len(sys.argv) not in (2, 4):
    raise SystemExit("usage: verify-nvidia-payload.py PAYLOAD_DIR")
root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "manifest.json").read_text())
if manifest.get("schema") != "resonarch.nodeos.nvidia-payload.v1":
    raise SystemExit("unexpected NVIDIA payload schema")
expected_cc = sys.argv[2] if len(sys.argv) == 4 else "6.1"
expected_profile = sys.argv[3] if len(sys.argv) == 4 else "haswell-gtx1070ti-v1"
if manifest.get("target_compute_capability") != expected_cc or manifest.get("target_profile") != expected_profile:
    raise SystemExit("NVIDIA payload hardware profile mismatch")
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
