"""Compute ONE v1 reference protocol; CPU fixture, NOT a CUDA driver."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import uuid

SCHEMA = "resonarch.compute-one.workunit.v1"
MAX_WIRE_BYTES = 1_600_000
MAX_TENSOR_BYTES = 1_048_576
DTYPES = {"u8": 1, "i32le": 4, "f32le": 4}
OPS = frozenset({"checksum32", "xor_u8", "cuda13_only_reverse"})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class ProtocolError(ValueError):
    """Invalid envelope, descriptor or source-byte integrity."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ProtocolError("duplicate field: " + key)
        output[key] = value
    return output


def _exact_object(value, keys, field):
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtocolError(field + ": unexpected or missing fields")
    return value


def _bounded_int(value, low, high, name):
    if type(value) is not int or value < low or value > high:
        raise ProtocolError("invalid " + name)
    return value


def _sha(value, field):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ProtocolError("invalid " + field)
    return value


def descriptor(tensor):
    return {key: tensor[key] for key in ("name", "dtype", "shape", "byte_length", "sha256")}


def state_root(tensors):
    return digest(canonical([descriptor(t) for t in tensors]))


def wire_root(tensors):
    return digest(canonical(tensors))


def validate_tensor(value):
    t = _exact_object(value, {"name", "dtype", "shape", "byte_length", "sha256",
                              "data_b64"}, "tensor")
    if not isinstance(t["name"], str) or not NAME.fullmatch(t["name"]):
        raise ProtocolError("bad tensor name")
    if not isinstance(t["dtype"], str) or t["dtype"] not in DTYPES:
        raise ProtocolError("unsupported tensor dtype")
    shape = t["shape"]
    if not isinstance(shape, list) or not 1 <= len(shape) <= 4:
        raise ProtocolError("bad tensor shape")
    count = 1
    for dim in shape:
        count *= _bounded_int(dim, 1, 65536, "shape dimension")
        if count > MAX_TENSOR_BYTES:
            raise ProtocolError("shape cap exceeded")
    expected = count * DTYPES[t["dtype"]]
    size = _bounded_int(t["byte_length"], 1, MAX_TENSOR_BYTES, "byte_length")
    if size != expected:
        raise ProtocolError("shape/dtype/byte_length mismatch")
    _sha(t["sha256"], "tensor sha256")
    b64 = t["data_b64"]
    if not isinstance(b64, str) or len(b64) > 4 * math.ceil(MAX_TENSOR_BYTES / 3):
        raise ProtocolError("base64 cap exceeded")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("invalid base64") from exc
    if base64.b64encode(raw).decode("ascii") != b64:
        raise ProtocolError("noncanonical base64")
    if len(raw) != size or digest(raw) != t["sha256"]:
        raise ProtocolError("tensor byte/hash mismatch")
    return t, raw


def validate_workunit(value):
    w = _exact_object(value, {"schema", "workunit_id", "tenant", "epoch",
                              "attempt", "op", "tensors", "state_root",
                              "wire_root"}, "workunit")
    if w["schema"] != SCHEMA:
        raise ProtocolError("schema mismatch")
    if not isinstance(w["workunit_id"], str):
        raise ProtocolError("workunit id required")
    try:
        uid = uuid.UUID(w["workunit_id"])
    except ValueError as exc:
        raise ProtocolError("workunit UUID") from exc
    if str(uid) != w["workunit_id"]:
        raise ProtocolError("noncanonical UUID")
    if not isinstance(w["tenant"], str) or not NAME.fullmatch(w["tenant"]):
        raise ProtocolError("tenant")
    _bounded_int(w["epoch"], 1, 2**63 - 1, "epoch")
    _bounded_int(w["attempt"], 1, 2**31 - 1, "attempt")
    if not isinstance(w["op"], str) or w["op"] not in OPS:
        raise ProtocolError("operation")
    if not isinstance(w["tensors"], list) or len(w["tensors"]) != 1:
        raise ProtocolError("O1 supports exactly one tensor")
    tensor, raw = validate_tensor(w["tensors"][0])
    if w["op"] in {"xor_u8", "cuda13_only_reverse"} and tensor["dtype"] != "u8":
        raise ProtocolError("operation requires u8")
    if _sha(w["state_root"], "state_root") != state_root(w["tensors"]):
        raise ProtocolError("source/root mismatch")
    if _sha(w["wire_root"], "wire_root") != wire_root(w["tensors"]):
        raise ProtocolError("wire/root mismatch")
    return w, raw


def read_wire(payload: bytes):
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_WIRE_BYTES:
        raise ProtocolError("wire byte limit")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda x: (_ for _ in ()).throw(ProtocolError("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON") from exc
    return validate_workunit(value)


def encode_wire(workunit: dict) -> bytes:
    validate_workunit(workunit)
    wire = canonical(workunit)
    if len(wire) > MAX_WIRE_BYTES:
        raise ProtocolError("wire byte limit")
    return wire


def make_workunit(*, tenant: str, epoch: int, attempt: int, op: str,
                  data: bytes, shape: list[int], dtype="u8", workunit_id=None):
    if type(data) is not bytes:
        raise ProtocolError("input must be bytes")
    tensor = {"name": "input", "dtype": dtype, "shape": shape,
              "byte_length": len(data), "sha256": digest(data),
              "data_b64": base64.b64encode(data).decode("ascii")}
    unit = {"schema": SCHEMA, "workunit_id": workunit_id or str(uuid.uuid4()),
            "tenant": tenant, "epoch": epoch, "attempt": attempt,
            "op": op, "tensors": [tensor],
            "state_root": state_root([tensor]), "wire_root": wire_root([tensor])}
    validate_workunit(unit)
    return unit
