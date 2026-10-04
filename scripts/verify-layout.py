#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    "buildroot/external.desc",
    "buildroot/Config.in",
    "buildroot/external.mk",
    "buildroot/configs/resonarch_haswell_gtx1070ti_defconfig",
    "buildroot/configs/resonarch_dell_g15_5530_defconfig",
    "compute_one/__init__.py",
    "compute_one/protocol.py",
    "compute_one/worker.py",
    "compute_one/gateway.py",
    "compute_one/physical_probe.py",
    "compute_one/cuda13_kernel_probe.py",
    "docs/evidence/dell-ada-cuda-driver-memcpy.json",
    "docs/evidence/dell-ada-cuda13-nvrtc-marker.json",
    "contracts/compute-one-v1.schema.json",
    "tests/test_compute_one.py",
    "docs/COMPUTE-ONE-001.md",
    "buildroot/board/resonarch/dell-g15-5530/post-build.sh",
    "buildroot/board/resonarch/dell-g15-5530/rootfs-overlay/etc/nodeos/nodeos.conf",
    "buildroot/board/resonarch/dell-g15-5530/rootfs-overlay/etc/init.d/S55nodeos-ssh",
    "buildroot/package/nodeos-agent/Config.in",
    "buildroot/package/nodeos-agent/nodeos-agent.mk",
    "buildroot/package/nodeos-agent/src/nodeos-agent",
    "buildroot/board/resonarch/haswell-gtx1070ti/linux-nodeos.fragment",
    "buildroot/board/resonarch/haswell-gtx1070ti/post-build.sh",
    "buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/etc/nodeos/nodeos.conf",
    "buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/etc/init.d/S20nodeos-net",
    "buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/etc/init.d/S90nodeos-agent",
    "buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/usr/sbin/nodeos-cuda-smoke",
    "tools/cuda/nodeos_cuda_probe.cu",
    "contracts/nodeos-001-evidence-v1.schema.json",
]
missing = [item for item in required if not (ROOT / item).is_file()]
if missing:
    raise SystemExit("missing NODEOS-001 files: " + ", ".join(missing))

defconfig = (ROOT / "buildroot/configs/resonarch_haswell_gtx1070ti_defconfig").read_text()
for needle in (
    "BR2_x86_64=y",
    "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y",
    'BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="6.12.47"',
    "BR2_PACKAGE_NODEOS_AGENT=y",
    "BR2_TARGET_GRUB2_X86_64_EFI=y",
):
    if needle not in defconfig:
        raise SystemExit(f"required defconfig invariant missing: {needle}")

dell = (ROOT / "buildroot/configs/resonarch_dell_g15_5530_defconfig").read_text()
for token in ("BR2_PACKAGE_DROPBEAR=y", "dell-g15-5530/rootfs-overlay",
              "dell-g15-5530/post-build.sh", "BR2_TARGET_GRUB2_X86_64_EFI=y"):
    if token not in dell:
        raise SystemExit("missing Dell defconfig invariant: " + token)
for path in (ROOT / "contracts").glob("*.json"):
    json.loads(path.read_text())
print("NODEOS-001 layout/contracts: PASS")

# Existing GitHub workflow already runs this script. Keep Dell unit tests
# behind the same protected CI gate without requiring workflow-write scope.
import subprocess, sys
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                "-p", "test_*.py", "-v"], check=True)
