"""O3 CPU-only safety checks; the physical Dell test is opt-in."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from compute_one.cuda13_worker import SOURCE, STATUS
from compute_one.gateway import (
    AdmissionError, DevHmacVerifier, Gateway, WorkerError,
)
from compute_one.protocol import ProtocolError, canonical, encode_wire, make_workunit, MAX_TENSOR_BYTES
from compute_one.worker import execute as cpu_oracle


def unit(op="xor_u8"):
    data = bytes(range(32))
    return make_workunit(tenant="physical-mock", epoch=2, attempt=1,
                         op=op, data=data, shape=[len(data)])


def mock_physical_receipt(w):
    cpu = cpu_oracle("ada", encode_wire(w))
    return {
        "schema": "resonarch.compute-one.cuda13-result.v1",
        "status": STATUS,
        "gpu_executed": True,
        "cuda_loaded": True,
        "worker_profile": "ada",
        "sm": "8.9",
        "cuda_driver_api_version": 13030,
        "nvrtc_version": "13.2",
        "kernel_source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "ptx_sha256": "a" * 64,
        "tenant": w["tenant"],
        "op": w["op"],
        "workunit_id": w["workunit_id"],
        "epoch": w["epoch"],
        "attempt": w["attempt"],
        "source_root": w["state_root"],
        "output_sha256": cpu["output_sha256"],
        "output_b64": cpu["output_b64"],
    }


class PhysicalGatewayContract(unittest.TestCase):
    def setUp(self):
        self.v = DevHmacVerifier(os.urandom(32))
        self.g = Gateway(self.v.verify, worker_timeout_s=10)
        # A real file path is required by the Gateway, even in mocked CI.
        self.path = os.path.abspath(sys.executable)

    def run_mock(self, w, r):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=canonical(r), stderr=b"")
        with patch("compute_one.gateway.subprocess.run", return_value=fake) as run:
            result = self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"),
                                     mode="cuda13_ada", nvrtc_path=self.path)
            argv = run.call_args.args[0]
            self.assertIn("compute_one.cuda13_worker", argv)
            self.assertIn("--real", argv)
        return result

    def test_physical_receipt_verified_against_independent_cpu_oracle(self):
        w = unit()
        receipt = self.run_mock(w, mock_physical_receipt(w))
        self.assertTrue(receipt["local_cpu_oracle_pass"])
        self.assertFalse(receipt["receipt_signed"])

    def test_only_ada_with_explicit_nvrtc_and_mode(self):
        w = unit()
        with self.assertRaisesRegex(ProtocolError, "Ada only"):
            self.g.dispatch(w, preferred="pascal", lease=self.v.issue(w, "pascal"),
                            mode="cuda13_ada", nvrtc_path=self.path)
        with self.assertRaisesRegex(ProtocolError, "NVRTC"):
            self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"),
                            mode="cuda13_ada")
        with self.assertRaisesRegex(ProtocolError, "unknown execution mode"):
            self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"), mode="cuda99")
        with self.assertRaisesRegex(ProtocolError, "NVRTC cannot"):
            self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"), nvrtc_path=self.path)

    def test_reject_forged_physical_status_and_kernel_source(self):
        for key, replacement in (
            ("gpu_executed", False),
            ("cuda_loaded", False),
            ("sm", "6.1"),
            ("kernel_source_sha256", "b" * 64),
            ("source_root", "c" * 64),
            ("tenant", "another"),
            ("status", "SIMULATED_PASS"),
        ):
            with self.subTest(key=key):
                w = unit()
                self.g = Gateway(self.v.verify)
                receipt = mock_physical_receipt(w)
                receipt[key] = replacement
                fake = subprocess.CompletedProcess(args=[], returncode=0,
                                                   stdout=canonical(receipt), stderr=b"")
                with patch("compute_one.gateway.subprocess.run", return_value=fake):
                    with self.assertRaisesRegex(WorkerError, "receipt invalid"):
                        self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"),
                                        mode="cuda13_ada", nvrtc_path=self.path)

    def test_reject_forged_output_even_with_self_consistent_sha(self):
        w = unit()
        receipt = mock_physical_receipt(w)
        receipt["output_b64"] = base64.b64encode(b"x" * 32).decode()
        receipt["output_sha256"] = hashlib.sha256(b"x" * 32).hexdigest()
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=canonical(receipt), stderr=b"")
        with patch("compute_one.gateway.subprocess.run", return_value=fake):
            with self.assertRaisesRegex(WorkerError, "independent CPU oracle"):
                self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"),
                                mode="cuda13_ada", nvrtc_path=self.path)

    def test_fail_closed_on_unauthorized_lease_before_gpu_call(self):
        w = unit()
        with patch("compute_one.gateway.subprocess.run") as run:
            with self.assertRaises(AdmissionError):
                self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "pascal"),
                                mode="cuda13_ada", nvrtc_path=self.path)
            run.assert_not_called()

    def test_nonobject_worker_result_fail_closed(self):
        w = unit()
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=b"[]", stderr=b"")
        with patch("compute_one.gateway.subprocess.run", return_value=fake):
            with self.assertRaisesRegex(WorkerError, "not an object"):
                self.g.dispatch(w, preferred="ada", lease=self.v.issue(w, "ada"),
                                mode="cuda13_ada", nvrtc_path=self.path)

    def test_timeout_quarantines_attempt_and_denies_replay(self):
        w = unit()
        lease = self.v.issue(w, "ada")
        with patch("compute_one.gateway.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("worker", 0.01)):
            with self.assertRaisesRegex(WorkerError, "quarantined"):
                self.g.dispatch(w, preferred="ada", lease=lease, mode="cuda13_ada",
                                nvrtc_path=self.path)
        with patch("compute_one.gateway.subprocess.run") as run:
            with self.assertRaisesRegex(AdmissionError, "replay"):
                self.g.dispatch(w, preferred="ada", lease=lease, mode="cuda13_ada",
                                nvrtc_path=self.path)
            run.assert_not_called()

    def test_developer_fallback_requires_ada_bound_lease(self):
        w = unit("cuda13_only_reverse")
        with patch("compute_one.gateway.subprocess.run") as run:
            with self.assertRaises(AdmissionError):
                self.g.dispatch(w, preferred="pascal", allow_fallback=True,
                                lease=self.v.issue(w, "pascal"), mode="cuda13_ada",
                                nvrtc_path=self.path)
            run.assert_not_called()
        receipt = mock_physical_receipt(w)
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=canonical(receipt), stderr=b"")
        with patch("compute_one.gateway.subprocess.run", return_value=fake):
            result = self.g.dispatch(w, preferred="pascal", allow_fallback=True,
                                     lease=self.v.issue(w, "ada"), mode="cuda13_ada",
                                     nvrtc_path=self.path)
        self.assertTrue(result["local_cpu_oracle_pass"])


@unittest.skipUnless(os.environ.get("COMPUTE_ONE_REAL_NVRTC_DLL"),
                     "physical GPU tests are explicit and never run in CI")
class ManualDellPhysicalTests(unittest.TestCase):
    def test_real_1mib_fixture_memory_and_bounded_result(self):
        verifier = DevHmacVerifier(os.urandom(32))
        gateway = Gateway(verifier.verify, worker_timeout_s=45)
        payload = bytes(range(256)) * (MAX_TENSOR_BYTES // 256)
        w = make_workunit(tenant="physical-mock", epoch=3, attempt=1,
                          op="xor_u8", data=payload, shape=[16, MAX_TENSOR_BYTES // 16])
        result = gateway.dispatch(
            w, preferred="ada", lease=verifier.issue(w, "ada"),
            mode="cuda13_ada",
            nvrtc_path=os.environ["COMPUTE_ONE_REAL_NVRTC_DLL"])
        self.assertTrue(result["gpu_executed"])
        self.assertTrue(result["local_cpu_oracle_pass"])
        self.assertEqual(len(base64.b64decode(result["output_b64"])), MAX_TENSOR_BYTES)

    def test_real_gateway_dispatch_all_three_operators(self):
        verifier = DevHmacVerifier(os.urandom(32))
        gateway = Gateway(verifier.verify, worker_timeout_s=45)
        for op in ("checksum32", "xor_u8", "cuda13_only_reverse"):
            with self.subTest(op=op):
                w = unit(op)
                preferred = "pascal" if op == "cuda13_only_reverse" else "ada"
                result = gateway.dispatch(
                    w, preferred=preferred,
                    allow_fallback=op == "cuda13_only_reverse",
                    lease=verifier.issue(w, "ada"), mode="cuda13_ada",
                    nvrtc_path=os.environ["COMPUTE_ONE_REAL_NVRTC_DLL"],
                )
                self.assertTrue(result["gpu_executed"])
                self.assertTrue(result["local_cpu_oracle_pass"])


if __name__ == "__main__":
    unittest.main()
