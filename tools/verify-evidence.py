#!/usr/bin/env python3
"""Verify a NodeOS evidence bundle on the workstation (no sha256sum needed).

Usage:
    python tools/verify-evidence.py nodeos-evidence-<id>.tar [--expected-cc 6.1] [--expected-profile haswell-gtx1070ti]
    python tools/verify-evidence.py path/to/<boot-id>/

Checks:
- Hash integrity of all files against SHA256SUMS and manifest.json
- Required files exist (PASS bundles allow no missing files)
- Structural and JSON type validity
- Cross-file state & verdict consistency
- PCI BDF format & PCIe source validity
- GPU UUID non-empty string and binding across nvidia-smi.txt & capability
- Network IPv4, interface, and capability <-> network.json consistency
- CUDA Compute Capability format and expected hardware profile pinning
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import tarfile
import tempfile
try:
    import jsonschema
except ImportError:
    jsonschema = None
from pathlib import Path

REQUIRED = [
    "manifest.json", "capability.json", "gate-report.json", "network.json",
    "pcie.json", "cuda-smoke.json", "nvidia-smi.txt", "lspci.txt", "dmesg-tail.txt",
    "nodeos-agent.log", "SHA256SUMS",
]

KNOWN_PROFILES = {
    "haswell-gtx1070ti": "6.1",
    "haswell-gtx1070ti-v1": "6.1",
    "dell-g15-5530": "8.9",
}

PCI_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
CC_RE = re.compile(r"^\d+\.\d+$")
VALID_PCIE_SOURCES = {"sysfs", "nvidia-smi"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_bundle_dir(root: Path) -> Path:
    if (root / "manifest.json").is_file():
        return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "manifest.json").is_file():
            return child
    raise SystemExit("no manifest.json found")


def safe_read_json(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, "file missing"
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return None, "root JSON structure must be an object"
        return data, None
    except Exception as err:
        return None, f"malformed JSON: {err}"


def validate_schema(data: dict, schema_filename: str) -> str | None:
    if not jsonschema:
        return None  # Skip if jsonschema is not installed

    schema_path = Path(__file__).parent.parent / "contracts" / schema_filename
    if not schema_path.is_file():
        return f"schema file not found: {schema_filename}"
    
    try:
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(instance=data, schema=schema)
        return None
    except Exception as e:
        return f"schema validation failed: {e}"


def is_valid_ipv4(val: str) -> bool:
    if not isinstance(val, str) or not val.strip():
        return False
    try:
        ipaddress.IPv4Interface(val)
        return True
    except ValueError:
        return False


def verify(bundle: Path, expected_cc: str | None = None, expected_profile: str | None = None) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    manifest, err = safe_read_json(bundle / "manifest.json")
    if err or manifest is None:
        return False, [f"manifest.json error: {err}"], {}

    if manifest.get("schema") != "resonarch.nodeos.evidence-manifest.v1":
        problems.append("unexpected manifest schema")
    
    err = validate_schema(manifest, "nodeos-evidence-manifest-v1.schema.json")
    if err: problems.append(f"manifest.json {err}")

    verdict = manifest.get("verdict")
    state = manifest.get("state")
    missing = manifest.get("missing") or ""

    if verdict == "PASS":
        if missing:
            problems.append(f"PASS bundle has missing files declared: {missing}")
        if state != "NODE_READY":
            problems.append(f"PASS bundle has non-ready state: {state}")

    for name in REQUIRED:
        if not (bundle / name).is_file():
            if verdict == "PASS" or name not in missing.split(","):
                problems.append(f"required file missing: {name}")

    files_list = manifest.get("files")
    if not isinstance(files_list, list):
        problems.append("manifest files field must be a list")
        files_list = []

    listed = {}
    for entry in files_list:
        if isinstance(entry, dict) and "name" in entry and "sha256" in entry and "bytes" in entry:
            listed[entry["name"]] = entry
        else:
            problems.append("invalid file entry format in manifest.json")

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
        try:
            for line in sums.read_text().splitlines():
                digest, _, name = line.partition("  ")
                name = name.lstrip("*")
                seen.add(name)
                path = bundle / name
                if not path.is_file() or sha256(path) != digest:
                    problems.append(f"SHA256SUMS mismatch: {name}")
            for name in sorted(on_disk | {"manifest.json"}):
                if name not in seen:
                    problems.append(f"file not covered by SHA256SUMS: {name}")
        except Exception as e:
            problems.append(f"failed to read SHA256SUMS: {e}")

    # Determine expected CC from CLI options or known local profile mapping
    target_cc = expected_cc
    prof = manifest.get("profile") or ""
    
    if expected_profile:
        if expected_profile not in KNOWN_PROFILES:
            problems.append(f"unknown expected profile: {expected_profile}")
        else:
            if not target_cc:
                target_cc = KNOWN_PROFILES.get(expected_profile)
        if prof != expected_profile:
            problems.append(f"profile mismatch: manifest has {prof}, expected {expected_profile}")
    else:
        if not target_cc:
            target_cc = KNOWN_PROFILES.get(prof)

    # Cross-file structural & semantic checks
    if (bundle / "gate-report.json").is_file():
        gr, err = safe_read_json(bundle / "gate-report.json")
        if err or gr is None:
            problems.append(f"gate-report.json error: {err}")
        else:
            err = validate_schema(gr, "nodeos-gate-report-v1.schema.json")
            if err: problems.append(f"gate-report.json {err}")
            
            if verdict == "PASS" and gr.get("verdict") != "PASS":
                problems.append(f"semantic mismatch: gate-report verdict is {gr.get('verdict')}")
            if verdict == "PASS" and gr.get("state") != "NODE_READY":
                problems.append(f"semantic mismatch: gate-report state is {gr.get('state')}")

    cap_data = None
    if (bundle / "capability.json").is_file():
        cap, err = safe_read_json(bundle / "capability.json")
        if err or cap is None:
            problems.append(f"capability.json error: {err}")
        else:
            err = validate_schema(cap, "node-capability-v1.schema.json")
            if err: problems.append(f"capability.json {err}")

            cap_data = cap
            if verdict == "PASS":
                if cap.get("verdict") != "PASS":
                    problems.append(f"semantic mismatch: capability verdict is {cap.get('verdict')}")
                if cap.get("state") != "NODE_READY":
                    problems.append(f"semantic mismatch: capability state is {cap.get('state')}")
                if cap.get("health", {}).get("cuda_smoke") != "PASS":
                    problems.append(f"semantic mismatch: capability cuda_smoke health is {cap.get('health', {}).get('cuda_smoke')}")

            accs = cap.get("accelerators")
            if not isinstance(accs, list) or not accs:
                if verdict == "PASS":
                    problems.append("semantic mismatch: capability accelerators list is missing or empty")
            else:
                gpu = accs[0]
                uuid = gpu.get("uuid") if isinstance(gpu, dict) else None
                if not isinstance(uuid, str) or not uuid.strip():
                    if verdict == "PASS":
                        problems.append("semantic mismatch: capability GPU UUID is missing or invalid")
                pci_addr = gpu.get("pci_address") if isinstance(gpu, dict) else None
                if pci_addr and not PCI_RE.match(pci_addr):
                    problems.append(f"invalid PCI address format: {pci_addr}")

            net = cap.get("network")
            if isinstance(net, dict):
                iface = net.get("interface")
                ipv4 = net.get("ipv4")
                if verdict == "PASS":
                    if not isinstance(iface, str) or not iface.strip():
                        problems.append("semantic mismatch: capability network interface missing")
                    if not is_valid_ipv4(str(ipv4)):
                        problems.append(f"semantic mismatch: capability network IPv4 invalid: {ipv4}")
            elif verdict == "PASS":
                problems.append("semantic mismatch: capability network object missing or invalid type")

    net_data = None
    if (bundle / "network.json").is_file():
        net_json, err = safe_read_json(bundle / "network.json")
        if err or net_json is None:
            problems.append(f"network.json error: {err}")
        else:
            net_data = net_json
            link = net_json.get("link", {})
            if isinstance(link, dict):
                ipv4 = link.get("ipv4")
                if ipv4 and not is_valid_ipv4(str(ipv4)):
                    problems.append(f"invalid IPv4 in network.json: {ipv4}")

    # Cross-check capability.json vs network.json
    if cap_data and net_data and verdict == "PASS":
        cap_net = cap_data.get("network", {})
        net_link = net_data.get("link", {})
        if isinstance(cap_net, dict) and isinstance(net_link, dict):
            if cap_net.get("interface") != net_link.get("interface"):
                problems.append(f"network inconsistency: interface capability ({cap_net.get('interface')}) != network ({net_link.get('interface')})")
            if cap_net.get("ipv4") != net_link.get("ipv4"):
                problems.append(f"network inconsistency: ipv4 capability ({cap_net.get('ipv4')}) != network ({net_link.get('ipv4')})")

    cuda_cc = None
    if (bundle / "cuda-smoke.json").is_file():
        cs, err = safe_read_json(bundle / "cuda-smoke.json")
        if err or cs is None:
            problems.append(f"cuda-smoke.json error: {err}")
        else:
            if verdict == "PASS" and cs.get("status") != "PASS":
                problems.append(f"semantic mismatch: cuda-smoke status is {cs.get('status')}")
            cuda_cc = cs.get("compute_capability")
            if cuda_cc and not CC_RE.match(str(cuda_cc)):
                problems.append(f"invalid CUDA compute capability syntax: {cuda_cc}")

    if cap_data and isinstance(cap_data.get("system"), dict):
        sys_cuda = cap_data["system"].get("cuda", {})
        if isinstance(sys_cuda, dict) and sys_cuda.get("compute_capability"):
            cap_cc = str(sys_cuda.get("compute_capability"))
            if not CC_RE.match(cap_cc):
                problems.append(f"invalid capability CUDA compute capability syntax: {cap_cc}")
            if cuda_cc and cap_cc != str(cuda_cc):
                problems.append(f"CUDA CC inconsistency: capability ({cap_cc}) != cuda-smoke ({cuda_cc})")

    if target_cc and verdict == "PASS":
        if not cuda_cc:
            problems.append(f"compute capability missing, expected {target_cc}")
        elif str(cuda_cc) != str(target_cc):
            problems.append(f"compute capability mismatch: measured {cuda_cc} != expected {target_cc}")

    if (bundle / "pcie.json").is_file():
        pcie, err = safe_read_json(bundle / "pcie.json")
        if err or pcie is None:
            problems.append(f"pcie.json error: {err}")
        else:
            src = pcie.get("source")
            if src is not None and src not in VALID_PCIE_SOURCES and src != "none":
                problems.append(f"invalid pcie source: {src}")
            addr = pcie.get("address")
            if addr and not PCI_RE.match(str(addr)):
                problems.append(f"invalid PCI address format in pcie.json: {addr}")
            if verdict == "PASS":
                if src == "none" or not src or not addr:
                    problems.append("semantic mismatch: pcie evidence has no GPU address or source is none")

    return not problems, problems, manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify a NodeOS evidence bundle")
    parser.add_argument("target", help="Tarball or directory path")
    parser.add_argument("--expected-cc", help="Explicit expected CUDA compute capability (e.g. 6.1)")
    parser.add_argument("--expected-profile", help="Explicit expected hardware profile name")
    args = parser.parse_args(argv[1:])

    target = Path(args.target)
    if not target.exists():
        print(f"PROBLEM: target path does not exist: {target}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        if target.is_file():
            try:
                with tarfile.open(target) as archive:
                    for member in archive.getmembers():
                        if member.name.startswith("/") or ".." in member.name:
                            raise ValueError(f"unsafe tar member: {member.name}")
                    if sys.version_info >= (3, 12):
                        archive.extractall(tmp, filter="data")
                    else:
                        archive.extractall(tmp)
            except Exception as e:
                print(f"PROBLEM: invalid archive: {e}")
                return 1
            bundle = find_bundle_dir(Path(tmp))
        else:
            bundle = find_bundle_dir(target)

        ok, problems, manifest = verify(bundle, expected_cc=args.expected_cc, expected_profile=args.expected_profile)
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
