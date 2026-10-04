from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class DellContractTests(unittest.TestCase):
    def test_profiles_are_separate(self):
        dell=(ROOT/"buildroot/configs/resonarch_dell_g15_5530_defconfig").read_text()
        legacy=(ROOT/"buildroot/configs/resonarch_haswell_gtx1070ti_defconfig").read_text()
        self.assertIn("BR2_PACKAGE_DROPBEAR=y",dell)
        self.assertIn("dell-g15-5530/rootfs-overlay",dell)
        self.assertIn("dell-g15-5530/post-build.sh",dell)
        self.assertNotIn("dell-g15-5530",legacy)
        self.assertIn("BR2_TARGET_GRUB2_X86_64_EFI=y",dell)

    def test_dell_is_dhcp_and_gpu_gated(self):
        c=(ROOT/"buildroot/board/resonarch/dell-g15-5530/rootfs-overlay/etc/nodeos/nodeos.conf").read_text()
        self.assertIn("NODEOS_LINK_MODE=dhcp",c)
        self.assertIn("NODEOS_EXPECTED_CC=8.9",c)
        smoke=(ROOT/"buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/usr/sbin/nodeos-cuda-smoke").read_text()
        self.assertIn("cuda-capability-mismatch",smoke)
        agent=(ROOT/"buildroot/package/nodeos-agent/src/nodeos-agent").read_text()
        self.assertIn('$(json_escape "$NODEOS_GPU_PROFILE")',agent)

    def test_key_only_ssh_and_no_stock_service(self):
        board=ROOT/"buildroot/board/resonarch/dell-g15-5530"
        s=(board/"post-build.sh").read_text()
        self.assertIn('rm -f "$TARGET_DIR/etc/init.d/S50dropbear"',s)
        init=(board/"rootfs-overlay/etc/init.d/S55nodeos-ssh").read_text()
        self.assertIn('[ -s "$KEY" ]',init)
        self.assertIn("-s -g",init)
        self.assertNotIn("password",init.lower().replace("no password",""))

    def test_usb_tool_is_read_only_and_guards_system_disk(self):
        s=(ROOT/"tools/dell-usb-preflight.ps1").read_text()
        for needle in ("ExpectedPnpId","IsBoot","IsSystem","ProtectedLabel","READ_ONLY_TARGET_CHECK"):
            self.assertIn(needle,s)
        for dangerous in ("Clear-Disk","Format-Volume","Set-Partition","WriteAllBytes","Set-Content -Path \\\\.\\\\PhysicalDrive"):
            self.assertNotIn(dangerous,s)

    def test_payload_verification_profile_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);f=p/"rootfs/usr/libexec/nodeos-cuda-probe"
            f.parent.mkdir(parents=True);f.write_bytes(b"synthetic probe")
            manifest={"schema":"resonarch.nodeos.nvidia-payload.v1",
              "target_profile":"dell-g15-5530-v1","target_compute_capability":"8.9",
              "files":[{"path":"rootfs/usr/libexec/nodeos-cuda-probe","sha256":hashlib.sha256(f.read_bytes()).hexdigest()}]}
            (p/"manifest.json").write_text(json.dumps(manifest))
            cmd=[sys.executable,str(ROOT/"scripts/verify-nvidia-payload.py"),str(p)]
            good=subprocess.run(cmd+["8.9","dell-g15-5530-v1"],capture_output=True,text=True)
            self.assertEqual(good.returncode,0,good.stderr)
            bad=subprocess.run(cmd+["6.1","haswell-gtx1070ti-v1"],capture_output=True,text=True)
            self.assertNotEqual(bad.returncode,0)

if __name__=="__main__":unittest.main()
