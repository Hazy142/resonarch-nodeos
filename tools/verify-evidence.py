#!/usr/bin/env python3
"""Verify a NodeOS evidence bundle on the workstation (no sha256sum needed).

    python tools/verify-evidence.py nodeos-evidence-<id>.tar
    python tools/verify-evidence.py path/to/<boot-id>/

Checks: SHA256SUMS matches every file, manifest.json lists every other file with
the right hash, required files exist, and prints the Evidence ID (SHA-256 of
manifest.json) and the verdict. Exit code 0 only for verdict PASS.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import tempfile
from pathlib import Path

REQUIRED = [
    "manifest.json", "capability.json", "gate-report.json", "network.json",
    "pcie.json", "nvidia-smi.txt", "lspci.txt", "dmesg-tail.txt",
    "nodeos-agent.log", "SHA256SUMS",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_bundle_dir(root: Path) -> Path:
    if (root / "manifest.json").is_file():
        return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "manifest.json").is_file():
            return child
    raise SystemExit("no manifest.json found")


def verify(bundle: Path) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("schema") != "resonarch.nodeos.evidence-manifest.v1":
        problems.append("unexpected manifest schema")
    missing = manifest.get("missing") or ""
    for name in REQUIRED:
        if not (bundle / name).is_file() and name not in missing.split(","):
            problems.append(f"required file missing: {name}")

    listed = {entry["name"]: entry for entry in manifest.get("files", [])}
    for name, entry in listed.items():
        path = bundle / name
        if not path.is_file():
            problems.append(f"listed file missing: {name}")
        elif sha256(path) != entry["sha256"]:
            problems.append(f"manifest hash mismatch: {name}")
        elif path.stat().st_size != entry["bytes"]:
            problems.append(f"manifest size mismatch: {name}")
    on_disk = {p.name for p in bundle.iterdir() if p.is_file()} - {"manifest.json", "SHA256SUMS"}
    for name in sorted(on_disk - set(listed)):
        problems.append(f"file not listed in manifest: {name}")

    sums = bundle / "SHA256SUMS"
    seen = set()
    if sums.is_file():
        for line in sums.read_text().splitlines():
            digest, _, name = line.partition("  ")
            name = name.lstrip("*")
            seen.add(name)
            path = bundle / name
            if not path.is_file() or sha256(path) != digest:
                problems.append(f"SHA256SUMS mismatch: {name}")
        for name in sorted(on_disk | {"manifest.json"}) :
            if name not in seen:
                problems.append(f"file not covered by SHA256SUMS: {name}")
    return not problems, problems, manifest


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        if target.is_file():
            with tarfile.open(target) as archive:
                archive.extractall(tmp, filter="data") if sys.version_info >= (3, 12) else archive.extractall(tmp)
            bundle = find_bundle_dir(Path(tmp))
        else:
            bundle = find_bundle_dir(target)
        ok, problems, manifest = verify(bundle)
        evidence_id = sha256(bundle / "manifest.json")
        for line in problems:
            print("PROBLEM:", line)
        print(f"Evidence ID: {evidence_id}")
        print(f"node: {manifest.get('node_id')}  boot: {manifest.get('boot_id')}")
        print(f"state: {manifest.get('state')} {manifest.get('state_reason') or ''}".rstrip())
        verdict = manifest.get("verdict")
        print(f"bundle integrity: {'OK' if ok else 'BROKEN'}")
        print(f"NODEOS-001 PHYSICAL {verdict}")
        return 0 if ok and verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
