"""Explicit owner-authorized physical Dell Compute ONE integration test.

No network listener, no persistence, no firmware/driver changes; the local
HMAC test verifier is NOT a production APE identity or durable replay ledger.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .gateway import DevHmacVerifier, Gateway
from .protocol import make_workunit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="explicit physical CUDA13 execution")
    parser.add_argument("--nvrtc", required=True, help="absolute path to installed NVRTC CUDA13 DLL")
    parser.add_argument("--large", action="store_true", help="also run maximum 1-MiB fixture")
    args = parser.parse_args()
    if not args.real:
        parser.error("real GPU execution requires --real")
    verifier = DevHmacVerifier(os.urandom(32))
    gateway = Gateway(verifier.verify, worker_timeout_s=45)
    fixtures = [
        ("checksum32", bytes(range(256)) * 4, "ada", False),
        ("xor_u8", bytes(range(256)) * 4, "ada", False),
        ("cuda13_only_reverse", bytes(range(256)) * 4, "pascal", True),
    ]
    if args.large:
        fixtures.append(("xor_u8", bytes(range(256)) * 4096, "ada", False))
    output = []
    for i, (op, raw, preferred, fallback) in enumerate(fixtures, start=1):
        w = make_workunit(tenant="dell-owner-lab", epoch=1, attempt=i,
                          op=op, data=raw,
                          shape=[16, 65536] if len(raw) > 65536 else [len(raw)])
        receipt = gateway.dispatch(
            w, lease=verifier.issue(w, "ada"), preferred=preferred,
            allow_fallback=fallback, mode="cuda13_ada", nvrtc_path=args.nvrtc,
        )
        summary = {k: v for k, v in receipt.items() if k != "output_b64"}
        summary["input_bytes"] = len(raw)
        output.append(summary)
    print(json.dumps({"schema": "resonarch.compute-one.o3-physical-dell.v1",
                      "status": f"ALL_{len(output)}_PHYSICAL_WORKUNITS_PASS",
                      "local_dev_admission_only": True,
                      "production_authorization": False,
                      "runs": output}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
