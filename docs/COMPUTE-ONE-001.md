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

## O3 now integrated on the owner's Dell (details below); O4/O5 remain open

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

## O3 implementation: real Ada Compute ONE worker (Dell lab)

**O3 local physical integration PASS on Dell G15 / RTX4060 Laptop sm89 /
Windows driver 610.88 / NVRTC 13.2.** The external ONE v1 API and small bounded
JSON/F09-like tensor fixture are unchanged. Gateway.dispatch now additionally
accepts mode=cuda13_ada with an absolute locally installed nvrtc_path. The
Gateway checks its injected lease, binds the lease to the selected backend,
consumes the attempt before launch, then invokes compute_one.cuda13_worker as
a separate process. The physical receipt must match tenant/workunit/epoch/
attempt, source-root, sm89, CUDA13 compiler, pinned kernel-source SHA256 and
output hash. The parent independently compares output against its CPU oracle.

The real worker compiles ONLY static auditable own CUDA C from the source file
through official NVRTC APIs, allocates bounded GPU input/output buffers,
copies the verified input into VRAM, launches an approved CUDA kernel, fences
completion with cuCtxSynchronize, reads back and cleans up. Three real workunits
passed on Dell: checksum32 (sequential GPU byte sum), xor_u8 (256-thread grid),
and cuda13_only_reverse (byte-reversal DISPATCH MARKER; not a claim that CUDA12
is incapable of reversing bytes). The third job explicitly routed from a
preferred Pascal plan to a separately authorized Ada physical worker. Every
GPU output agreed byte-for-byte with an independent CPU reference.

Owner-authorized, one-shot physical lab test from repository root:

    python -m compute_one.real_demo --real --nvrtc "D:\Program Files\bin\x64\nvrtc64_130_0.dll"

Physical tests are intentionally excluded from generic Ubuntu CI. To execute
the manual GPU regression gate on a compatible authorized Windows test host,
set COMPUTE_ONE_REAL_NVRTC_DLL to the installed DLL before running:

    python -m unittest discover -s tests -p test_compute_one_cuda13.py -v

Unsigned local end-to-end observations are stored in
docs/evidence/dell-ada-compute-one-o3.json. Source/PTX hashes and SHA256 of
original and output buffers are recorded, but this is NOT enterprise hardware
attestation or a signed, timestamped evidence chain.

**Not implemented:** production APE-signed leases, mTLS, multi-host durable
replay protection, PCI/GSP secure attestation, F09/F10 remote transport,
physical T4/Pascal CUDA workers, real GGML model operators, token-performance
claims, NodeOS Linux NVRTC loader or signed Dell USB Secure Boot image. The
HMAC verifier is a local-development-only helper and subprocess separation
is not a malicious-code sandbox. Arbitrary PTX supplied by users is forbidden.
Native NVIDIA .ko research remains independently gated.

### O3 physical boundary gate (1 MiB)

The same administrator-approved Dell also passed a fourth physical gateway
workunit with a full 1,048,576-byte u8 tensor shaped [16, 65536] (within the
original per-dimension limit). The approved Ada CUDA13 XOR kernel executed
across a 4096-block CUDA grid; complete GPU output agreed byte-for-byte with
an independent CPU oracle. Reproducible opt-in lab command:

    python -m compute_one.real_demo --real --large --nvrtc "D:\Program Files\bin\x64\nvrtc64_130_0.dll"

Its separate unsigned local pilot report is
`docs/evidence/dell-ada-compute-one-o3-1mib.json` with all four physical
workunit roots, per-input sizes, kernel-source/PTX hashes and SHA256 outputs.
The 1-MiB output SHA256 on the initial Dell run was
`5e8e6a984ed51e93f0b6889bb88b5322a9a52a109e2aa9b8636b11dbd050648c`.
No sustained bandwidth, TPS, low-latency inference or production safety
conclusion follows from this short synthetic test.
