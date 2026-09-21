#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit("usage: hash-artifacts.py ARTIFACT_DIR BUILDROOT_VERSION BUILDROOT_COMMIT")
artifact_dir = Path(sys.argv[1]).resolve()
artifacts = []
for name in ("disk.img", "bzImage", "rootfs.ext2"):
    path = artifact_dir / name
    artifacts.append({
        "name": name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
try:
    repo_commit = subprocess.check_output(
        ["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"], text=True
    ).strip()
except Exception:
    repo_commit = "unknown"
payload = {
    "schema": "resonarch.nodeos.build-evidence.v1",
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "nodeos_commit": repo_commit,
    "buildroot_version": sys.argv[2],
    "buildroot_commit": sys.argv[3],
    "profile": "haswell-gtx1070ti-v1",
    "artifacts": artifacts,
}
out = artifact_dir / "nodeos-001-build.json"
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(out)
