"""One-request, CPU-only backend fixture; no CUDA driver/library is loaded.

Subprocess separation checks protocol/toolchain dispatch. It is NOT an OS
security sandbox and MUST NOT be exposed directly to untrusted network peers.
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
from .protocol import ProtocolError, MAX_WIRE_BYTES, canonical, digest, read_wire

PROFILES = {
    "pascal": {"sm": "6.1", "toolkit_family": "12.x",
               "ops": frozenset({"checksum32", "xor_u8"})},
    "turing": {"sm": "7.5", "toolkit_family": "13.x",
               "ops": frozenset({"checksum32", "xor_u8", "cuda13_only_reverse"})},
    "ada": {"sm": "8.9", "toolkit_family": "13.x",
            "ops": frozenset({"checksum32", "xor_u8", "cuda13_only_reverse"})},
}


def execute(profile: str, wire: bytes) -> dict:
    if profile not in PROFILES:
        raise ProtocolError("unknown worker profile")
    w, source = read_wire(wire)
    capabilities = PROFILES[profile]
    if w["op"] not in capabilities["ops"]:
        raise ProtocolError("UNSUPPORTED_OPERATION_" + profile.upper())
    if w["op"] == "checksum32":
        result = struct.pack("<I", sum(source) % 2**32)
    elif w["op"] == "xor_u8":
        result = bytes(x ^ 0xA5 for x in source)
    elif w["op"] == "cuda13_only_reverse":
        result = source[::-1]  # CPU-only probe of 13.x capability routing
    else:
        raise ProtocolError("operation not implemented")
    return {
        "schema": "resonarch.compute-one.stub-result.v1",
        "status": "SIMULATED_PASS",
        "gpu_executed": False,
        "cuda_loaded": False,
        "worker_profile": profile,
        "declared_sm": capabilities["sm"],
        "declared_toolkit_family": capabilities["toolkit_family"],
        "workunit_id": w["workunit_id"],
        "epoch": w["epoch"],
        "attempt": w["attempt"],
        "source_root": w["state_root"],
        "output_sha256": digest(result),
        "output_b64": base64.b64encode(result).decode("ascii"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, choices=PROFILES)
    args = parser.parse_args()
    try:
        data = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        result = execute(args.profile, data)
    except (ProtocolError, ValueError) as exc:
        sys.stdout.buffer.write(canonical({"status": "REJECTED", "reason": str(exc)}))
        return 2
    sys.stdout.buffer.write(canonical(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
