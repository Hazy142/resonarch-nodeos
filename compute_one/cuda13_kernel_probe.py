"""Opt-in *real* NVRTC/CUDA Driver API sm_89 kernel probe for owned Dell.

Requires --real plus a path to legitimately installed NVIDIA NVRTC DLL.
No driver installation, firmware changes, disk writes, CUDA binary patches or
network. This is NOT an end-to-end Compute ONE backend.
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import json
import os
import sys

SOURCE = b'extern "C" __global__ void one_marker(unsigned int *out) { if(threadIdx.x == 0 && blockIdx.x == 0) out[0] = 0x4e4f4445u; }\n'


def probe(nvrtc_path: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("pilot currently supports Windows DLLs only")
    driver = c.WinDLL("nvcuda.dll")
    compiler = c.WinDLL(nvrtc_path)
    p = c.c_void_p
    uint = c.c_uint

    def fn(dll, symbol, *types):
        x = getattr(dll, symbol)
        x.restype = c.c_int
        x.argtypes = list(types)
        return x

    def checked(code, stage):
        if code:
            raise RuntimeError(f"{stage} failed: CUDA/NVRTC error {code}")

    cu_init = fn(driver, "cuInit", uint)
    cu_version = fn(driver, "cuDriverGetVersion", c.POINTER(c.c_int))
    cu_device = fn(driver, "cuDeviceGet", c.POINTER(c.c_int), c.c_int)
    cu_attr = fn(driver, "cuDeviceGetAttribute", c.POINTER(c.c_int), c.c_int, c.c_int)
    cu_ctx_create = fn(driver, "cuCtxCreate_v2", c.POINTER(p), uint, c.c_int)
    cu_ctx_destroy = fn(driver, "cuCtxDestroy_v2", p)
    cu_module_load = fn(driver, "cuModuleLoadDataEx", c.POINTER(p), p, uint, p, p)
    cu_module_get = fn(driver, "cuModuleGetFunction", c.POINTER(p), p, c.c_char_p)
    cu_module_unload = fn(driver, "cuModuleUnload", p)
    cu_malloc = fn(driver, "cuMemAlloc_v2", c.POINTER(c.c_uint64), c.c_size_t)
    cu_free = fn(driver, "cuMemFree_v2", c.c_uint64)
    cu_launch = fn(driver, "cuLaunchKernel", p, uint, uint, uint, uint, uint,
                   uint, uint, p, c.POINTER(p), p)
    cu_sync = fn(driver, "cuCtxSynchronize")
    cu_d2h = fn(driver, "cuMemcpyDtoH_v2", p, c.c_uint64, c.c_size_t)

    rtc_version = fn(compiler, "nvrtcVersion", c.POINTER(c.c_int), c.POINTER(c.c_int))
    rtc_create = fn(compiler, "nvrtcCreateProgram", c.POINTER(p), c.c_char_p,
                    c.c_char_p, c.c_int, p, p)
    rtc_compile = fn(compiler, "nvrtcCompileProgram", p, c.c_int, c.POINTER(c.c_char_p))
    rtc_ptx_size = fn(compiler, "nvrtcGetPTXSize", p, c.POINTER(c.c_size_t))
    rtc_ptx = fn(compiler, "nvrtcGetPTX", p, p)
    rtc_log_size = fn(compiler, "nvrtcGetProgramLogSize", p, c.POINTER(c.c_size_t))
    rtc_log = fn(compiler, "nvrtcGetProgramLog", p, p)
    rtc_destroy = fn(compiler, "nvrtcDestroyProgram", c.POINTER(p))

    checked(cu_init(0), "cuInit")
    driver_ver, device, sm_major, sm_minor = c.c_int(), c.c_int(), c.c_int(), c.c_int()
    checked(cu_version(c.byref(driver_ver)), "cuDriverGetVersion")
    checked(cu_device(c.byref(device), 0), "cuDeviceGet")
    checked(cu_attr(c.byref(sm_major), 75, device.value), "sm_major")
    checked(cu_attr(c.byref(sm_minor), 76, device.value), "sm_minor")
    if (sm_major.value, sm_minor.value) != (8, 9):
        raise RuntimeError("hardware gate requires RTX4060 class sm_89")
    rtc_major, rtc_minor = c.c_int(), c.c_int()
    checked(rtc_version(c.byref(rtc_major), c.byref(rtc_minor)), "nvrtcVersion")
    if rtc_major.value != 13:
        raise RuntimeError("CUDA13 NVRTC required; refusing unexpected compiler version")
    program = p()
    checked(rtc_create(c.byref(program), SOURCE, b"compute_one_marker.cu", 0, None, None),
            "nvrtcCreateProgram")
    try:
        opts = (c.c_char_p * 2)(b"--gpu-architecture=compute_89", b"--std=c++17")
        status = rtc_compile(program, len(opts), opts)
        if status:
            log_length = c.c_size_t()
            checked(rtc_log_size(program, c.byref(log_length)), "nvrtcGetLogSize")
            log_buf = c.create_string_buffer(log_length.value)
            checked(rtc_log(program, log_buf), "nvrtcGetProgramLog")
            raise RuntimeError("NVRTC compile failed: " + log_buf.value.decode("utf-8", "replace")[:1500])
        size = c.c_size_t()
        checked(rtc_ptx_size(program, c.byref(size)), "nvrtcGetPTXSize")
        if not 0 < size.value <= 8_000_000:
            raise RuntimeError("unreasonable PTX size")
        ptx = c.create_string_buffer(size.value)
        checked(rtc_ptx(program, ptx), "nvrtcGetPTX")
    finally:
        checked(rtc_destroy(c.byref(program)), "nvrtcDestroyProgram")

    ctx, module, fn_kernel, gpu = p(), p(), p(), c.c_uint64()
    created = loaded = allocated = False
    try:
        checked(cu_ctx_create(c.byref(ctx), 0, device.value), "cuCtxCreate")
        created = True
        checked(cu_module_load(c.byref(module), c.cast(ptx, p), 0, None, None), "cuModuleLoadDataEx")
        loaded = True
        checked(cu_module_get(c.byref(fn_kernel), module, b"one_marker"), "cuModuleGetFunction")
        checked(cu_malloc(c.byref(gpu), 4), "cuMemAlloc")
        allocated = True
        kernel_params = (p * 1)()
        kernel_params[0] = c.cast(c.byref(gpu), p)
        checked(cu_launch(fn_kernel, 1, 1, 1, 1, 1, 1, 0, None,
                          kernel_params, None), "cuLaunchKernel")
        checked(cu_sync(), "cuCtxSynchronize")
        host = c.c_uint32()
        checked(cu_d2h(c.byref(host), gpu, 4), "cuMemcpyDtoH")
        checked(cu_sync(), "readback fence")
        if host.value != 0x4e4f4445:
            raise RuntimeError(f"GPU marker mismatch: {host.value:08x}")
        return {
            "schema": "resonarch.compute-one.physical-cuda13-kernel.v1",
            "status": "CUDA13_SM89_KERNEL_PASS",
            "gpu_kernel_executed": True,
            "compute_one_worker_integrated": False,
            "device_index": device.value,
            "sm": "8.9",
            "cuda_driver_api_version": driver_ver.value,
            "nvrtc_version": f"{rtc_major.value}.{rtc_minor.value}",
            "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
            "ptx_sha256": hashlib.sha256(ptx.raw).hexdigest(),
            "readback_marker": "0x4e4f4445",
        }
    finally:
        if allocated:
            checked(cu_free(gpu), "cuMemFree")
        if loaded:
            checked(cu_module_unload(module), "cuModuleUnload")
        if created:
            checked(cu_ctx_destroy(ctx), "cuCtxDestroy")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="acknowledge real CUDA allocation")
    parser.add_argument("--nvrtc", default=os.environ.get("COMPUTE_ONE_NVRTC_DLL"),
                        help="absolute path to legally installed CUDA 13 NVRTC DLL")
    args = parser.parse_args()
    if not args.real or not args.nvrtc:
        parser.error("opt in with --real and --nvrtc ABSOLUTE_DLL_PATH")
    try:
        print(json.dumps(probe(args.nvrtc), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "CUDA13_SM89_KERNEL_FAIL", "reason": str(exc)}),
              file=sys.stderr)
        sys.exit(1)
