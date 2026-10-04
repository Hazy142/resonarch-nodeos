# Compute ONE / O1–O2 · Bounded CPU reference (2026-10-04)

## Scope and naming

Versioned prototype wire format for signed-admission experiments; not a CUDA fork,
an NVIDIA driver, a production F09 transport or a security sandbox. Its 1 MiB
inline-base64 limit is strictly a **fixture bound**; production F09 objects are
content-addressed and streamed via independently authorized FiberFEC/FiberTunnel.
All successful receipts explicitly say SIMULATED_PASS, gpu_executed=false and
cuda_loaded=false. No performance or physical GPU proof is inferred.

## Architecture

Console → APE lease verifier (injected) → strict Compute ONE v1 decoder →
backend capability selection → one-request subprocess (Pascal 12.x / Turing
13.x / Ada 13.x **simulated** profiles) → hash-checked result receipt.
Versions here identify intended toolchain **families**, not installed toolkits.
Do not mix CUDA12 and CUDA13 shared libraries in one process; no pointers,
events, PTX or device contexts cross this protocol.

Protocol is deterministic canonical sorted-key ASCII JSON with an inline tensor
named input; dtype u8/i32le/f32le; 1–4 positive contiguous dimensions;
bounded byte_length and canonical base64; exact field sets and duplicate-key
rejection; sha256 of original raw bytes and a canonical descriptor-based
F09-like state_root; wire_root covers serialized tensor entries.
Operation is a pre-approved ID, not externally supplied executable code.
checksum32, xor_u8 and cuda13_only_reverse are **CPU test operations**,
the last one being a deliberate dispatch capability marker, NOT an assertion
that reverse-bytes is absent from CUDA12. Unknown operators are rejected.

## Development-only lease

Gateway requires an injected verified-lease callback. Test helper
DevHmacVerifier creates random-secret HMAC leases bound to tenant,
workunit_id, epoch/attempt, original state_root, backend and an absolute
short expiry. DO NOT expose these fixtures on any network, ship their
HMAC as enterprise security, or reuse a per-process replay cache for
cross-host security. Production integration requires APE signed tickets,
durable epoch/attempt/replay ledger, node identity, mTLS/TPM policy and
real receiver-commit once F09/F10/FiberFEC is wired.

## Local run

From repository root:

    python -m compute_one.gateway
    python -m unittest discover -s tests -p 'test_compute_one*.py' -v
    python scripts/verify-layout.py

Run on Windows and Linux without CUDA. Existing NodeOS CI calls
verify-layout.py; the extended gate executes the O1/O2 tests automatically.
The worker subprocess is process-separated but not sandboxed against malicious
code. Trust boundaries: only Gateway can launch workers; no inbound socket;
all outputs are bounded and hash checked.

## Pinned official compatibility references

- NVIDIA CUDA Archive (https://developer.nvidia.com/cuda-toolkit-archive):
  13.4.1 latest at 2026-10-04, 12.9.2 latest 12.x.
- NVIDIA CUDA 13.0 release notes:
  https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html
  Maxwell/Pascal/Volta offline compilation and library support removed.
- GPU capability and driver/toolkit matrix is a per-deployment prerequisite.
  Runtime minor-version compatibility is not a guarantee for all new features.

## Deliberately open O3/O4/O5

- Dell physical Ada CUDA13 receive/normalize and pinned driver/ABI validation.
- Tesla T4 physical CUDA13 with matching GSP/kernel/userspace (authorized host).
- Pascal real sm61 CUDA12.9.x after second computer is assembled.
- Physical F09/F10, FiberFEC over actual LAN/WAN and production signed leases.
- Bootable signed Dell USB image and a separate administrator console.
- NDA/EULA and interoperability legal review before any NVIDIA binary analysis.

## Optional real Dell GPU gates (separate from O1/O2 CPU workers)

The authorized Dell G15 has RTX4060 Laptop sm_89, Windows NVIDIA driver 610.88, CUDA toolkit 13.2.86, and NVIDIA CUDA driver API reports version 13030. These are **physical pilot observations**, not declarations that 13.4/615 has been installed.

Run only on an owner-authorized compatible Windows test machine:

    python -m compute_one.physical_probe
    python -m compute_one.cuda13_kernel_probe --real --nvrtc 'D:\Program Files\bin\x64\nvrtc64_130_0.dll'

The first gate allocates 4096 GPU bytes, copies via official CUDA Driver API in both directions and verifies an identical SHA256 on readback. The second compiles our own marker CUDA C kernel via the legitimately installed CUDA13.2 NVRTC public API to sm_89 PTX; the CUDA driver loads and launches it, synchronizes GPU execution and reads back exactly `0x4e4f4445`. Both checks were performed twice on the Dell and independently exited zero; their *unsigned pilot observations* are recorded at `docs/evidence/dell-ada-cuda-driver-memcpy.json` and `docs/evidence/dell-ada-cuda13-nvrtc-marker.json`.

These physical scripts are **opt-in and not run in CI**: GitHub Ubuntu runners do not have this personal laptop, matching GPU, matching NVRTC DLL or its trust identity. The separate CPU subprocess gateway continues to return only SIMULATED_PASS; the physical kernel is not yet invoked as a ONE workunit. No GPU overclock, firmware change, binary reverse engineering, USB flashing, driver installation or OS setting was performed.

Next O3 is an authorized `compute_one.cuda13_worker` that reads verified ONE input, executes whitelisted real CUDA kernels and returns SHA-verified receipts with full physical hardware/build evidence; it must never accept arbitrary PTX from clients.
