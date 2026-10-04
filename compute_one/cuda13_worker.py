"""O3 opt-in real Ada worker. Only fixed, auditable CUDA source is compiled.

Host process boundary is NOT a malicious-code sandbox. Gateway authorizes leases;
this child rechecks canonical wire integrity and enforces a CUDA13/sm89 allowlist.
"""
from __future__ import annotations

import argparse
import base64
import ctypes as c
import hashlib
import json
import os
import sys
from .protocol import MAX_WIRE_BYTES, ProtocolError, canonical, digest, read_wire

# Runtime-compiled from *our own* immutable source: remote users supply no PTX.
SOURCE = b"""\
extern "C" __global__ void one_checksum32(const unsigned char *src, unsigned char *dst, unsigned int n) {
    if (blockIdx.x == 0 && threadIdx.x == 0) {
        unsigned int sum = 0u;
        for (unsigned int i = 0; i < n; ++i) sum += (unsigned int)src[i];
        dst[0] = (unsigned char)(sum & 255u);
        dst[1] = (unsigned char)((sum >> 8) & 255u);
        dst[2] = (unsigned char)((sum >> 16) & 255u);
        dst[3] = (unsigned char)((sum >> 24) & 255u);
    }
}
extern "C" __global__ void one_xor_u8(const unsigned char *src, unsigned char *dst, unsigned int n) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) dst[i] = src[i] ^ 0xa5u;
}
extern "C" __global__ void one_reverse(const unsigned char *src, unsigned char *dst, unsigned int n) {
    unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) dst[i] = src[n - 1u - i];
}
"""
KERNELS = {"checksum32": b"one_checksum32", "xor_u8": b"one_xor_u8",
           "cuda13_only_reverse": b"one_reverse"}
STATUS = "CUDA13_SM89_WORKUNIT_PASS"


def _checked(code: int, stage: str) -> None:
    if code != 0:
        raise RuntimeError(f"{stage}: CUDA/NVRTC returned {code}")


def _api(lib, symbol: str, *argtypes):
    fn = getattr(lib, symbol)
    fn.restype = c.c_int
    fn.argtypes = list(argtypes)
    return fn


def execute_real(wire: bytes, nvrtc_path: str) -> dict:
    w, source_bytes = read_wire(wire)
    if w["op"] not in KERNELS:
        raise ProtocolError("operation is not a whitelisted CUDA13 kernel")
    if not isinstance(nvrtc_path, str) or not os.path.isabs(nvrtc_path) or not os.path.isfile(nvrtc_path):
        raise RuntimeError("administrator must configure an installed NVRTC DLL")
    if os.name != "nt":
        raise RuntimeError("O3 Windows Dell pilot only; Linux CUDA loader not yet qualified")

    driver = c.WinDLL("nvcuda.dll")
    rtc = c.WinDLL(nvrtc_path)
    p, u, z = c.c_void_p, c.c_uint, c.c_size_t

    # Public CUDA Driver API and NVRTC API; no proprietary binary modification.
    cu_init = _api(driver, "cuInit", u)
    cu_drv = _api(driver, "cuDriverGetVersion", c.POINTER(c.c_int))
    cu_dev = _api(driver, "cuDeviceGet", c.POINTER(c.c_int), c.c_int)
    cu_attr = _api(driver, "cuDeviceGetAttribute", c.POINTER(c.c_int), c.c_int, c.c_int)
    cu_ctx = _api(driver, "cuCtxCreate_v2", c.POINTER(p), u, c.c_int)
    cu_ctx_end = _api(driver, "cuCtxDestroy_v2", p)
    cu_load = _api(driver, "cuModuleLoadDataEx", c.POINTER(p), p, u, p, p)
    cu_func = _api(driver, "cuModuleGetFunction", c.POINTER(p), p, c.c_char_p)
    cu_unload = _api(driver, "cuModuleUnload", p)
    cu_malloc = _api(driver, "cuMemAlloc_v2", c.POINTER(c.c_uint64), z)
    cu_free = _api(driver, "cuMemFree_v2", c.c_uint64)
    cu_h2d = _api(driver, "cuMemcpyHtoD_v2", c.c_uint64, p, z)
    cu_d2h = _api(driver, "cuMemcpyDtoH_v2", p, c.c_uint64, z)
    cu_sync = _api(driver, "cuCtxSynchronize")
    cu_launch = _api(driver, "cuLaunchKernel", p, u, u, u, u, u, u, u,
                     p, c.POINTER(p), p)

    nv_version = _api(rtc, "nvrtcVersion", c.POINTER(c.c_int), c.POINTER(c.c_int))
    nv_create = _api(rtc, "nvrtcCreateProgram", c.POINTER(p), c.c_char_p,
                     c.c_char_p, c.c_int, p, p)
    nv_compile = _api(rtc, "nvrtcCompileProgram", p, c.c_int, c.POINTER(c.c_char_p))
    nv_ptx_size = _api(rtc, "nvrtcGetPTXSize", p, c.POINTER(z))
    nv_ptx = _api(rtc, "nvrtcGetPTX", p, p)
    nv_log_size = _api(rtc, "nvrtcGetProgramLogSize", p, c.POINTER(z))
    nv_log = _api(rtc, "nvrtcGetProgramLog", p, p)
    nv_end = _api(rtc, "nvrtcDestroyProgram", c.POINTER(p))

    _checked(cu_init(0), "cuInit")
    version, device, major, minor = c.c_int(), c.c_int(), c.c_int(), c.c_int()
    _checked(cu_drv(c.byref(version)), "cuDriverGetVersion")
    _checked(cu_dev(c.byref(device), 0), "cuDeviceGet")
    _checked(cu_attr(c.byref(major), 75, device.value), "compute capability major")
    _checked(cu_attr(c.byref(minor), 76, device.value), "compute capability minor")
    if (major.value, minor.value) != (8, 9) or version.value < 13000:
        raise RuntimeError("wrong GPU/driver: approved Ada sm89 and CUDA13 required")
    rtc_major, rtc_minor = c.c_int(), c.c_int()
    _checked(nv_version(c.byref(rtc_major), c.byref(rtc_minor)), "nvrtcVersion")
    if rtc_major.value != 13:
        raise RuntimeError("wrong NVRTC compiler family")

    program = p()
    _checked(nv_create(c.byref(program), SOURCE, b"compute_one_ada_v1.cu",
                       0, None, None), "nvrtcCreateProgram")
    try:
        opts = (c.c_char_p * 2)(b"--gpu-architecture=compute_89", b"--std=c++17")
        result = nv_compile(program, 2, opts)
        if result:
            log_size = z()
            _checked(nv_log_size(program, c.byref(log_size)), "NVRTC log size")
            if log_size.value > 65536:
                raise RuntimeError("NVRTC log overflow")
            buf = c.create_string_buffer(log_size.value)
            _checked(nv_log(program, buf), "NVRTC log")
            raise RuntimeError("NVRTC failed: " + buf.value[:1800].decode("utf-8", "replace"))
        size = z()
        _checked(nv_ptx_size(program, c.byref(size)), "nvrtcGetPTXSize")
        if not 0 < size.value <= 8_000_000:
            raise RuntimeError("unexpected generated PTX length")
        ptx = c.create_string_buffer(size.value)
        _checked(nv_ptx(program, ptx), "nvrtcGetPTX")
    finally:
        _checked(nv_end(c.byref(program)), "nvrtcDestroyProgram")

    output_size = 4 if w["op"] == "checksum32" else len(source_bytes)
    ctx, module, kernel = p(), p(), p()
    input_gpu, output_gpu = c.c_uint64(), c.c_uint64()
    ctx_ok = module_ok = input_ok = output_ok = False
    try:
        _checked(cu_ctx(c.byref(ctx), 0, device.value), "cuCtxCreate")
        ctx_ok = True
        _checked(cu_load(c.byref(module), c.cast(ptx, p), 0, None, None), "cuModuleLoadDataEx")
        module_ok = True
        _checked(cu_func(c.byref(kernel), module, KERNELS[w["op"]]), "cuModuleGetFunction")
        _checked(cu_malloc(c.byref(input_gpu), len(source_bytes)), "cuMemAlloc input")
        input_ok = True
        _checked(cu_malloc(c.byref(output_gpu), output_size), "cuMemAlloc output")
        output_ok = True
        host_src = c.create_string_buffer(source_bytes)
        _checked(cu_h2d(input_gpu, c.cast(host_src, p), len(source_bytes)), "cuMemcpyHtoD")
        _checked(cu_sync(), "input fence")
        n = c.c_uint(len(source_bytes))
        params = (p * 3)(c.cast(c.byref(input_gpu), p),
                         c.cast(c.byref(output_gpu), p),
                         c.cast(c.byref(n), p))
        block = 1 if w["op"] == "checksum32" else 256
        grid = (len(source_bytes) + block - 1) // block
        _checked(cu_launch(kernel, grid, 1, 1, block, 1, 1, 0, None,
                           params, None), "cuLaunchKernel")
        _checked(cu_sync(), "kernel completion fence")
        host_dst = c.create_string_buffer(output_size)
        _checked(cu_d2h(c.cast(host_dst, p), output_gpu, output_size), "cuMemcpyDtoH")
        _checked(cu_sync(), "output fence")
        output = host_dst.raw
        return {
            "schema": "resonarch.compute-one.cuda13-result.v1",
            "status": STATUS,
            "gpu_executed": True,
            "cuda_loaded": True,
            "worker_profile": "ada",
            "sm": f"{major.value}.{minor.value}",
            "cuda_driver_api_version": version.value,
            "nvrtc_version": f"{rtc_major.value}.{rtc_minor.value}",
            "kernel_source_sha256": digest(SOURCE),
            "ptx_sha256": digest(ptx.raw),
            "device_index": device.value,
            "workunit_id": w["workunit_id"],
            "tenant": w["tenant"],
            "epoch": w["epoch"],
            "attempt": w["attempt"],
            "op": w["op"],
            "source_root": w["state_root"],
            "output_sha256": digest(output),
            "output_b64": base64.b64encode(output).decode("ascii"),
        }
    finally:
        # Cleanup failure is a failed/quarantined worker, not a claimed PASS.
        if output_ok:
            _checked(cu_free(output_gpu), "cuMemFree output")
        if input_ok:
            _checked(cu_free(input_gpu), "cuMemFree input")
        if module_ok:
            _checked(cu_unload(module), "cuModuleUnload")
        if ctx_ok:
            _checked(cu_ctx_end(ctx), "cuCtxDestroy")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="explicit GPU execution opt-in")
    parser.add_argument("--nvrtc", help="absolute path to administrator-approved CUDA13 NVRTC")
    args = parser.parse_args()
    if not args.real or not args.nvrtc:
        parser.error("physical worker requires --real and explicit --nvrtc path")
    try:
        wire = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        result = execute_real(wire, args.nvrtc)
    except (ProtocolError, RuntimeError, OSError) as exc:
        sys.stdout.buffer.write(canonical({"status": "REJECTED", "reason": str(exc)[:512]}))
        return 2
    sys.stdout.buffer.write(canonical(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
