"""Compute ONE v1 process gateway for bounded CPU fixture requests.

Development HMAC is not production APE/mTLS/TPM enrollment. Production callers
MUST inject an independently authenticated APE capability verifier.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from collections.abc import Callable

from .protocol import HEX64, ProtocolError, canonical, encode_wire
from .worker import PROFILES, execute as cpu_oracle
from .cuda13_worker import SOURCE as ADA_KERNEL_SOURCE, STATUS as ADA_STATUS


class AdmissionError(PermissionError):
    """Failed tenant/lease/epoch authentication or replay defense."""


class WorkerError(RuntimeError):
    pass


LEASE_FIELDS = frozenset({
    "schema", "lease_id", "workunit_id", "tenant", "state_root",
    "epoch", "attempt", "target_backend", "expires_unix_s", "signature",
})


class DevHmacVerifier:
    """Explicit developer/test lease helper. Never use as enterprise APE."""
    def __init__(self, secret: bytes):
        if type(secret) is not bytes or len(secret) < 32:
            raise ValueError("dev secret must be random and >=32 bytes")
        self._secret = secret

    def issue(self, workunit: dict, target_backend: str, ttl_s=60) -> dict:
        if target_backend not in PROFILES or type(ttl_s) is not int or not 1 <= ttl_s <= 3600:
            raise ValueError("invalid development lease")
        lease = {"schema": "resonarch.compute-one.dev-lease.v1",
                 "lease_id": str(uuid.uuid4()),
                 "workunit_id": workunit["workunit_id"],
                 "tenant": workunit["tenant"], "state_root": workunit["state_root"],
                 "epoch": workunit["epoch"], "attempt": workunit["attempt"],
                 "target_backend": target_backend,
                 "expires_unix_s": int(time.time()) + ttl_s}
        lease["signature"] = hmac.new(self._secret, canonical(lease), hashlib.sha256).hexdigest()
        return lease

    def verify(self, lease: dict, workunit: dict, profile: str) -> bool:
        if not isinstance(lease, dict) or set(lease) != LEASE_FIELDS:
            return False
        if lease["schema"] != "resonarch.compute-one.dev-lease.v1":
            return False
        try:
            uid = uuid.UUID(lease["lease_id"])
            if str(uid) != lease["lease_id"]:
                return False
        except (ValueError, AttributeError, TypeError):
            return False
        if any(lease.get(field) != workunit.get(field) for field in (
                "workunit_id", "tenant", "state_root", "epoch", "attempt")):
            return False
        if lease["target_backend"] != profile:
            return False
        expiry = lease["expires_unix_s"]
        if type(expiry) is not int or not int(time.time()) < expiry <= int(time.time()) + 3600:
            return False
        sig = lease["signature"]
        if not isinstance(sig, str) or len(sig) != 64:
            return False
        signed = {key: value for key, value in lease.items() if key != "signature"}
        expected = hmac.new(self._secret, canonical(signed), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)


class Gateway:
    def __init__(self, verify_lease: Callable[[dict, dict, str], bool] | None,
                 worker_timeout_s: float = 5):
        if verify_lease is None or not callable(verify_lease):
            raise AdmissionError("a trusted APE verifier is required; no default allow")
        self.verify_lease = verify_lease
        self.worker_timeout_s = worker_timeout_s
        self._consumed: set[tuple] = set()

    @staticmethod
    def choose(workunit: dict, preferred: str, allow_fallback=False) -> str:
        if preferred not in PROFILES:
            raise ProtocolError("unknown preferred backend")
        if workunit["op"] in PROFILES[preferred]["ops"]:
            return preferred
        if allow_fallback is not True:
            raise ProtocolError("UNSUPPORTED_OPERATION_" + preferred.upper())
        for candidate in ("ada", "turing", "pascal"):
            if workunit["op"] in PROFILES[candidate]["ops"]:
                return candidate
        raise ProtocolError("no authorized implementation")

    def dispatch(self, workunit: dict, *, lease: dict, preferred: str,
                 allow_fallback=False, mode="stub", nvrtc_path=None) -> dict:
        wire = encode_wire(workunit)
        profile = self.choose(workunit, preferred, allow_fallback)
        if mode not in ("stub", "cuda13_ada"):
            raise ProtocolError("unknown execution mode")
        if mode == "stub" and nvrtc_path is not None:
            raise ProtocolError("NVRTC cannot be passed into CPU-only mode")
        if mode == "cuda13_ada":
            if profile != "ada":
                raise ProtocolError("real CUDA13 worker supports Ada only")
            if not isinstance(nvrtc_path, str) or not os.path.isabs(nvrtc_path) or not Path(nvrtc_path).is_file():
                raise ProtocolError("trusted administrator must provide installed NVRTC path")
        if self.verify_lease(lease, workunit, profile) is not True:
            raise AdmissionError("invalid or mismatched lease")
        replay_key = (workunit["tenant"], workunit["workunit_id"], workunit["epoch"],
                      workunit["attempt"])
        if replay_key in self._consumed:
            raise AdmissionError("replay: workunit attempt already consumed")
        self._consumed.add(replay_key)  # Consume even on worker crash or timeout.
        root = Path(__file__).resolve().parents[1]
        command = ([sys.executable, "-u", "-m", "compute_one.worker", "--profile", profile]
                   if mode == "stub" else
                   [sys.executable, "-u", "-m", "compute_one.cuda13_worker",
                    "--real", "--nvrtc", nvrtc_path])
        try:
            proc = subprocess.run(
                command,
                cwd=root, input=wire, capture_output=True, timeout=self.worker_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerError("worker timeout; attempt quarantined") from exc
        if len(proc.stdout) > 2_000_000:
            raise WorkerError("oversized worker output")
        try:
            result = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkerError("invalid worker output") from exc
        if not isinstance(result, dict):
            raise WorkerError("worker receipt is not an object")
        if proc.returncode != 0:
            raise WorkerError("worker rejected: " + str(result.get("reason", "unknown")))
        common = (isinstance(result, dict)
                  and result.get("source_root") == workunit["state_root"]
                  and result.get("workunit_id") == workunit["workunit_id"]
                  and result.get("epoch") == workunit["epoch"]
                  and result.get("attempt") == workunit["attempt"]
                  and result.get("worker_profile") == profile)
        if mode == "stub":
            valid = (common and result.get("status") == "SIMULATED_PASS"
                     and result.get("gpu_executed") is False
                     and result.get("cuda_loaded") is False)
        else:
            valid = (common and result.get("schema") == "resonarch.compute-one.cuda13-result.v1"
                     and result.get("status") == ADA_STATUS
                     and result.get("gpu_executed") is True
                     and result.get("cuda_loaded") is True
                     and result.get("sm") == "8.9"
                     and isinstance(result.get("cuda_driver_api_version"), int)
                     and result["cuda_driver_api_version"] >= 13000
                     and isinstance(result.get("nvrtc_version"), str)
                     and result["nvrtc_version"].startswith("13.")
                     and result.get("kernel_source_sha256") == hashlib.sha256(ADA_KERNEL_SOURCE).hexdigest()
                     and isinstance(result.get("ptx_sha256"), str)
                     and HEX64.fullmatch(result["ptx_sha256"]) is not None
                     and result.get("tenant") == workunit["tenant"]
                     and result.get("op") == workunit["op"])
        if not valid:
            raise WorkerError("worker receipt invalid")
        try:
            raw = base64.b64decode(result["output_b64"], validate=True)
        except (ValueError, KeyError) as exc:
            raise WorkerError("result not decodable") from exc
        if hashlib.sha256(raw).hexdigest() != result.get("output_sha256"):
            raise WorkerError("worker output hash mismatch")
        if mode == "cuda13_ada":
            reference = cpu_oracle("ada", wire)
            if (reference["output_sha256"] != result["output_sha256"]
                    or base64.b64decode(reference["output_b64"]) != raw):
                raise WorkerError("physical CUDA result fails independent CPU oracle")
            result["local_cpu_oracle_pass"] = True
            result["receipt_signed"] = False  # Not APE/mTLS attestation yet.
        return result


def local_demo() -> dict:
    """No network, CUDA or privileged access; create an ephemeral dev secret."""
    from .protocol import make_workunit
    verifier = DevHmacVerifier(os.urandom(32))
    gateway = Gateway(verifier.verify)
    unit = make_workunit(tenant="local-test", epoch=1, attempt=1,
                         op="checksum32", data=bytes(range(32)), shape=[32])
    pascal = gateway.dispatch(unit, preferred="pascal",
                              lease=verifier.issue(unit, "pascal"))
    unit2 = make_workunit(tenant="local-test", epoch=1, attempt=2,
                          op="cuda13_only_reverse", data=b"test", shape=[4])
    ada = gateway.dispatch(unit2, preferred="pascal", allow_fallback=True,
                           lease=verifier.issue(unit2, "ada"))
    return {"status": "CPU_SIMULATION_ONLY", "cuda_executed": False,
            "pascal_checksum": pascal["output_sha256"],
            "ada_fallback": ada["worker_profile"],
            "ada_output_sha256": ada["output_sha256"]}


if __name__ == "__main__":
    print(json.dumps(local_demo(), sort_keys=True))
