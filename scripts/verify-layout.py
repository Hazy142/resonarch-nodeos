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

for path in (ROOT / "contracts").glob("*.json"):
    json.loads(path.read_text())
print("NODEOS-001 layout/contracts: PASS")
