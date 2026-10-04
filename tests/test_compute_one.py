"""O1/O2 contracts: bounded F09 bytes, explicit routing and CPU-only stubs."""
import base64
import copy
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import unittest

from compute_one.protocol import (
    ProtocolError, canonical, digest, encode_wire, make_workunit,
    read_wire, state_root, validate_workunit, wire_root, MAX_WIRE_BYTES,
    MAX_TENSOR_BYTES,
)
from compute_one.worker import execute
from compute_one.gateway import AdmissionError, DevHmacVerifier, Gateway


def fixture(op="checksum32", data=b"\x01\x02\x03\x04", attempt=1):
    return make_workunit(tenant="test-team", epoch=7, attempt=attempt, op=op,
                         data=data, shape=[len(data)])


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_exact_bytes_and_roots(self):
        w = fixture()
        parsed, raw = read_wire(encode_wire(w))
        self.assertEqual(parsed, w)
        self.assertEqual(raw, b"\x01\x02\x03\x04")
        self.assertEqual(w["state_root"], state_root(w["tensors"]))
        self.assertEqual(w["wire_root"], wire_root(w["tensors"]))

    def test_endian_descriptor_and_checksum(self):
        data = struct.pack("<2i", -5, 123456)
        w = make_workunit(tenant="test-team", epoch=1, attempt=1,
                          op="checksum32", data=data, shape=[2], dtype="i32le")
        _, raw = read_wire(encode_wire(w))
        self.assertEqual(raw, data)

    def test_detect_modified_tensor_even_when_wire_root_recomputed(self):
        w = fixture()
        w["tensors"][0]["data_b64"] = base64.b64encode(b"\x01\x02\x03\x05").decode()
        w["wire_root"] = wire_root(w["tensors"])
        with self.assertRaisesRegex(ProtocolError, "byte/hash"):
            encode_wire(w)

    def test_detect_tampered_descriptor(self):
        w = fixture()
        w["tensors"][0]["name"] = "forged"
        w["wire_root"] = wire_root(w["tensors"])
        with self.assertRaisesRegex(ProtocolError, "source/root"):
            encode_wire(w)

    def test_duplicate_fields_rejected(self):
        wire = encode_wire(fixture())
        wire = wire.replace(b'"tenant":"test-team"',
                            b'"tenant":"test-team","tenant":"other"')
        with self.assertRaisesRegex(ProtocolError, "duplicate"):
            read_wire(wire)

    def test_unknown_critical_field_rejected(self):
        w = fixture()
        w["cuda_raw_device_pointer"] = 1234
        with self.assertRaises(ProtocolError):
            encode_wire(w)

    def test_bool_epochs_and_invalid_shape_rejected(self):
        w = fixture()
        w["epoch"] = True
        with self.assertRaises(ProtocolError):
            encode_wire(w)
        w = fixture()
        w["tensors"][0]["shape"] = [2, 2**100]
        with self.assertRaises(ProtocolError):
            encode_wire(w)

    def test_wire_and_tensor_bounds(self):
        with self.assertRaises(ProtocolError):
            read_wire(b" " * (MAX_WIRE_BYTES + 1))
        with self.assertRaises(ProtocolError):
            make_workunit(tenant="test-team", epoch=1, attempt=1, op="checksum32",
                          data=b"a" * (MAX_TENSOR_BYTES + 1),
                          shape=[MAX_TENSOR_BYTES + 1])

    def test_noncanonical_base64_and_wrong_dtype(self):
        w = fixture()
        w["tensors"][0]["data_b64"] = "AQIDBA"  # No canonical padding
        w["wire_root"] = wire_root(w["tensors"])
        with self.assertRaises(ProtocolError):
            encode_wire(w)
        with self.assertRaises(ProtocolError):
            make_workunit(tenant="test-team", epoch=1, attempt=1,
                          op="xor_u8", data=b"abcd", shape=[1], dtype="i32le")

    def test_nonfinite_json(self):
        with self.assertRaisesRegex(ProtocolError, "nonfinite"):
            read_wire(b'{"a":NaN}')


class IsolationAndAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.verifier = DevHmacVerifier(os.urandom(32))
        self.gateway = Gateway(self.verifier.verify)

    def test_isolated_pascal_and_ada_agree_on_f09_and_output(self):
        w = fixture(data=bytes(range(32)))
        pascal = self.gateway.dispatch(w, preferred="pascal",
                         lease=self.verifier.issue(w, "pascal"))
        # Distinct gateway: simulate different ownership/enrollment namespace.
        other = Gateway(self.verifier.verify)
        ada = other.dispatch(w, preferred="ada", lease=self.verifier.issue(w, "ada"))
        self.assertEqual(pascal["source_root"], ada["source_root"])
        self.assertEqual(pascal["output_sha256"], ada["output_sha256"])
        self.assertEqual(base64.b64decode(ada["output_b64"]),
                         struct.pack("<I", sum(range(32))))
        for result in (pascal, ada):
            self.assertFalse(result["gpu_executed"])
            self.assertFalse(result["cuda_loaded"])
            self.assertEqual(result["status"], "SIMULATED_PASS")

    def test_cuda13_only_rejects_pascal(self):
        w = fixture(op="cuda13_only_reverse", data=b"abcd")
        with self.assertRaisesRegex(ProtocolError, "UNSUPPORTED"):
            self.gateway.dispatch(w, preferred="pascal",
                                  lease=self.verifier.issue(w, "pascal"))
        self.assertEqual(execute("ada", encode_wire(w))["worker_profile"], "ada")

    def test_explicit_fallback_requires_ada_bound_lease(self):
        w = fixture(op="cuda13_only_reverse", data=b"abcd")
        with self.assertRaises(AdmissionError):
            self.gateway.dispatch(w, preferred="pascal", allow_fallback=True,
                                  lease=self.verifier.issue(w, "pascal"))
        result = self.gateway.dispatch(w, preferred="pascal", allow_fallback=True,
                                       lease=self.verifier.issue(w, "ada"))
        self.assertEqual(base64.b64decode(result["output_b64"]), b"dcba")

    def test_invalid_hmac_and_wrong_tenant_fail_closed(self):
        w = fixture()
        lease = self.verifier.issue(w, "pascal")
        lease["tenant"] = "different"
        with self.assertRaises(AdmissionError):
            self.gateway.dispatch(w, preferred="pascal", lease=lease)
        lease = self.verifier.issue(w, "pascal")
        lease["signature"] = "0" * 64
        with self.assertRaises(AdmissionError):
            self.gateway.dispatch(w, preferred="pascal", lease=lease)
        with self.assertRaises(AdmissionError):
            Gateway(None)

    def test_expiry_wrong_root_and_backend_fail_closed(self):
        w = fixture()
        for field, value in (("expires_unix_s", int(time.time()) - 1),
                             ("state_root", "0"*64), ("target_backend", "ada")):
            lease = self.verifier.issue(w, "pascal")
            lease[field] = value
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                self.gateway.dispatch(w, preferred="pascal", lease=lease)

    def test_replay_rejected_after_first_attempt(self):
        w = fixture()
        lease = self.verifier.issue(w, "pascal")
        self.gateway.dispatch(w, preferred="pascal", lease=lease)
        with self.assertRaisesRegex(AdmissionError, "replay"):
            self.gateway.dispatch(w, preferred="pascal", lease=lease)
        new = fixture(attempt=2)
        # The same workunit ID may get another authorized attempt.
        new["workunit_id"] = w["workunit_id"]
        self.gateway.dispatch(new, preferred="pascal",
                              lease=self.verifier.issue(new, "pascal"))

    def test_worker_subprocess_is_a_distinct_python_process(self):
        w = fixture(op="xor_u8", data=b"abcd")
        lease = self.verifier.issue(w, "turing")
        r = self.gateway.dispatch(w, preferred="turing", lease=lease)
        self.assertEqual(r["worker_profile"], "turing")
        self.assertEqual(base64.b64decode(r["output_b64"]),
                         bytes(v ^ 0xA5 for v in b"abcd"))


if __name__ == "__main__":
    unittest.main()
