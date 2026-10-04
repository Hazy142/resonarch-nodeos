"""Optional physical NVIDIA CUDA Driver API memcpy probe for owned Windows GPU.

No driver installation, no firmware/boot changes, no NVRTC or CUDA toolkit.
This is a *physical transfer* check, NOT a CUDA kernel/Compute ONE execution.
"""
from __future__ import annotations
import ctypes as c
import hashlib
import json
import os
import struct
import sys


def probe() -> dict:
    if os.name != "nt":
        raise RuntimeError("pilot supports Windows nvcuda.dll only")
    cu = c.WinDLL("nvcuda.dll")
    u64 = c.c_uint64
    ptr = c.c_void_p

    def bind(name, *args):
        api = getattr(cu, name)
        api.restype = c.c_int
        api.argtypes = list(args)
        return api

    init = bind("cuInit", c.c_uint)
    driver_version = bind("cuDriverGetVersion", c.POINTER(c.c_int))
    device_get = bind("cuDeviceGet", c.POINTER(c.c_int), c.c_int)
    attr = bind("cuDeviceGetAttribute", c.POINTER(c.c_int), c.c_int, c.c_int)
    ctx_create = bind("cuCtxCreate_v2", c.POINTER(ptr), c.c_uint, c.c_int)
    ctx_destroy = bind("cuCtxDestroy_v2", ptr)
    mem_alloc = bind("cuMemAlloc_v2", c.POINTER(u64), c.c_size_t)
    mem_free = bind("cuMemFree_v2", u64)
    h2d = bind("cuMemcpyHtoD_v2", u64, ptr, c.c_size_t)
    d2h = bind("cuMemcpyDtoH_v2", ptr, u64, c.c_size_t)
    sync = bind("cuCtxSynchronize")

    def ok(code, stage):
        if code != 0:
            raise RuntimeError(f"CUDA Driver API {stage}: error {code}")

    ok(init(0), "cuInit")
    version, device, major, minor = c.c_int(), c.c_int(), c.c_int(), c.c_int()
    ok(driver_version(c.byref(version)), "version")
    ok(device_get(c.byref(device), 0), "device0")
    # Public CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR/MINOR enumeration.
    ok(attr(c.byref(major), 75, device.value), "SM major")
    ok(attr(c.byref(minor), 76, device.value), "SM minor")
    if (major.value, minor.value) != (8, 9):
        raise RuntimeError("Dell physical pilot requires SM8.9; refusing other GPU")
    ctx, gpu = ptr(), u64()
    allocated = False
    created = False
    sample = bytes((i * 31 + 7) % 256 for i in range(4096))
    src = c.create_string_buffer(sample)
    dst = c.create_string_buffer(len(sample))
    try:
        ok(ctx_create(c.byref(ctx), 0, device.value), "context")
        created = True
        ok(mem_alloc(c.byref(gpu), len(sample)), "cuMemAlloc")
        allocated = True
        ok(h2d(gpu, c.cast(src, ptr), len(sample)), "H2D")
        ok(sync(), "H2D fence")
        ok(d2h(c.cast(dst, ptr), gpu, len(sample)), "D2H")
        ok(sync(), "D2H fence")
        if dst.raw != sample:
            raise RuntimeError("physical device readback mismatch")
        return {"schema": "resonarch.compute-one.cuda-driver-probe.v1",
                "status": "CUDA_DRIVER_MEMCPY_PASS",
                "gpu_executed_kernel": False,
                "cuda_toolkit_compiled": False,
                "device_index": device.value,
                "sm": f"{major.value}.{minor.value}",
                "cuda_driver_api_version": version.value,
                "transferred_bytes": len(sample),
                "sha256": hashlib.sha256(dst.raw).hexdigest()}
    finally:
        if allocated:
            ok(mem_free(gpu), "cuMemFree")
        if created:
            ok(ctx_destroy(ctx), "cuCtxDestroy")


if __name__ == "__main__":
    try:
        print(json.dumps(probe(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "CUDA_DRIVER_PROBE_FAIL",
                          "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)
