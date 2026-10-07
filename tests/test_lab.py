"""NODEOS-001-LAB: state machine, telemetry, network gate, evidence, console.

The real shell tools run against a fake /sys, /proc and fake hardware commands
(lspci, nvidia-smi, ip, ping, iperf3, ...) so every gate and every blocked
state is exercised without hardware. Skipped when no POSIX sh is available.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import io
import tarfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "buildroot/package/nodeos-agent/src"
SMOKE = ROOT / "buildroot/board/resonarch/haswell-gtx1070ti/rootfs-overlay/usr/sbin/nodeos-cuda-smoke"
OVERLAY = ROOT / "buildroot/board/resonarch/haswell-gtx1070ti"
SH = shutil.which("sh")
HAVE_SH = SH is not None and sys.platform != "win32"
TOOLS = ["nodeos-agent", "nodeos-netgate", "nodeos-evidence", "nodeos-console"]

STUBS = {
    "lspci": 'cat "$FAKE/lspci.out" 2>/dev/null; exit 0',
    "ip": 'cat "$FAKE/ip.out" 2>/dev/null; exit 0',
    "dmesg": 'echo "[    1.000000] fake dmesg line"; exit 0',
    "ethtool": 'echo "Speed: 1000Mb/s"; exit 0',
    "ping": 'cat "$FAKE/ping.out" 2>/dev/null; exit "$(cat "$FAKE/ping.rc" 2>/dev/null || echo 0)"',
    "iperf3": 'cat "$FAKE/iperf.out" 2>/dev/null; exit "$(cat "$FAKE/iperf.rc" 2>/dev/null || echo 0)"',
    "nvidia-smi": (
        'case "$1" in\n'
        ' -L) [ -f "$FAKE/smi.fail" ] && exit 1; echo "GPU 0: NVIDIA GeForce GTX 1070 Ti (UUID: GPU-aaaa)"; exit 0 ;;\n'
        ' --query-gpu=*) [ -f "$FAKE/smi.fail" ] && exit 1; cat "$FAKE/smi.query"; exit 0 ;;\n'
        ' -q) echo "nvidia-smi -q dump"; exit 0 ;;\n'
        ' *) echo "nvidia-smi table"; exit 0 ;;\n'
        'esac'
    ),
    "probe": 'cat "$FAKE/probe.json"; exit "$(cat "$FAKE/probe.rc" 2>/dev/null || echo 0)"',
}

PING_OK = (
    "PING 192.168.77.1 (192.168.77.1): 56 data bytes\n\n--- 192.168.77.1 ping statistics ---\n"
    "4 packets transmitted, 4 packets received, 0% packet loss\n"
    "round-trip min/avg/max = 0.250/0.310/0.400 ms\n"
)
PING_DEAD = (
    "--- 192.168.77.1 ping statistics ---\n4 packets transmitted, 0 packets received, 100% packet loss\n"
)
IPERF_OK = (
    "[  5]   0.00-5.00   sec   562 MBytes   943 Mbits/sec    0             sender\n"
    "[  5]   0.00-5.00   sec   560 MBytes   941 Mbits/sec                  receiver\n"
)
PROBE_OK = (
    '{"schema":"resonarch.nodeos.cuda-smoke.v1","status":"PASS","device":"NVIDIA GeForce GTX 1070 Ti",'
    '"compute_capability":"6.1","word":"0x4e4f4445","runtime_version":12040,"driver_version":12040}'
)
SMI_QUERY = (
    "NVIDIA GeForce GTX 1070 Ti, GPU-aaaa-bbbb, 535.54, 8192, 7866, 326, 38, 139, 405, 0, 12.34, 180.00, "
    "1, 3, 8, 16\n"
)


@unittest.skipUnless(HAVE_SH, "POSIX sh required (runs in Linux CI / WSL)")
class Lab:
    """A fake NodeOS box. Mutate files, then run the real tools."""

    def __init__(self, case: unittest.TestCase, conf_extra: str = "", **kw):
        self.case = case
        self.tmp = Path(tempfile.mkdtemp(prefix="nodeos-lab-"))
        case.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        self.run_dir, self.etc, self.sysfs, self.proc = t / "run", t / "etc", t / "sys", t / "proc"
        self.fake, self.bin, self.stubs = t / "fake", t / "bin", t / "stubs"
        self.log = t / "agent.log"
        self.persist = t / "persist"
        for d in (self.run_dir, self.etc, self.sysfs, self.proc, self.fake, self.bin, self.stubs):
            d.mkdir(parents=True)
        for tool in TOOLS:
            shutil.copy(SRC / tool, self.bin / tool)
        shutil.copy(SMOKE, self.bin / "nodeos-cuda-smoke")
        for path in self.bin.iterdir():
            path.chmod(0o755)
        for name, body in STUBS.items():
            p = self.stubs / name
            p.write_text("#!/bin/sh\n" + body + "\n")
            p.chmod(0o755)
        self.env = dict(
            os.environ, FAKE=str(self.fake), PATH=f"{self.stubs}:{os.environ['PATH']}",
            NODEOS_RUN=str(self.run_dir), NODEOS_ETC=str(self.etc), NODEOS_SYSFS=str(self.sysfs),
            NODEOS_PROCFS=str(self.proc), NODEOS_LOG=str(self.log), NODEOS_BIN=str(self.bin),
            NODEOS_LIB=str(SRC / "nodeos-lib.sh"), NODEOS_CUDA_PROBE=str(self.stubs / "probe"),
            NODEOS_EVIDENCE_PERSIST=str(self.persist),
        )
        self.build_healthy(conf_extra, **kw)

    # -- fake hardware ------------------------------------------------------
    def w(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def build_healthy(self, conf_extra: str = "", speed: str = "1000"):
        self.w(self.etc / "nodeos.conf",
               "NODEOS_NODE_ID=test-node\nNODEOS_PEER_IPV4=192.168.77.1\nNODEOS_REQUIRE_PEER=1\n"
               "NODEOS_MIN_LINK_MBIT=1000\nNODEOS_MIN_THROUGHPUT_MBIT=800\nNODEOS_EXPECTED_CC=6.1\n"
               "NODEOS_IPERF_SECONDS=1\nNODEOS_NODE_ID=test-node\n" + conf_extra)
        self.w(self.etc / "build-info",
               "NODEOS_BUILD_COMMIT=abc1234def5678abc1234def5678abc1234def56\nNODEOS_BUILD_DIRTY=0\n"
               "NODEOS_BUILDROOT_COMMIT=d5180309b1b66ef3b8eaccca70ad69be8e0729a1\n"
               "NODEOS_BUILD_PROFILE=haswell-gtx1070ti-v1\n")
        payload_file = self.tmp / "payload" / "libnvidia.so"
        self.w(payload_file, "synthetic payload")
        digest = hashlib.sha256(payload_file.read_bytes()).hexdigest()
        self.w(self.etc / "nvidia-payload-manifest.json", '{"schema":"resonarch.nodeos.nvidia-payload.v1"}\n')
        self.w(self.etc / "nvidia-payload.sha256", f"{digest}  {payload_file}\n")
        self.payload_file = payload_file
        net = self.sysfs / "class/net/eth0"
        for name, value in {
            "address": "aa:bb:cc:dd:ee:ff", "carrier": "1", "duplex": "full", "speed": speed,
            "mtu": "1500", "operstate": "up", "statistics/rx_bytes": "123456", "statistics/tx_bytes": "654321",
            "statistics/rx_packets": "100", "statistics/tx_packets": "90", "statistics/rx_errors": "0",
            "statistics/tx_errors": "0", "statistics/rx_dropped": "0", "statistics/tx_dropped": "0",
        }.items():
            self.w(net / name, value + "\n")
        (self.sysfs / "drivers/r8169").mkdir(parents=True)
        (net / "device").mkdir(parents=True, exist_ok=True)
        os.symlink(self.sysfs / "drivers/r8169", net / "device/driver")
        (self.sysfs / "firmware/efi").mkdir(parents=True)
        pci = self.sysfs / "bus/pci/devices/0000:01:00.0"
        for name, value in {
            "current_link_speed": "8.0 GT/s PCIe", "max_link_speed": "8.0 GT/s PCIe",
            "current_link_width": "8", "max_link_width": "16",
        }.items():
            self.w(pci / name, value + "\n")
        self.w(self.proc / "cpuinfo", "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-4770K CPU @ 3.50GHz\n"
                                       "processor\t: 1\nmodel name\t: Intel(R) Core(TM) i7-4770K CPU @ 3.50GHz\n")
        self.w(self.proc / "meminfo", "MemTotal:       16690000 kB\n")
        self.w(self.proc / "uptime", "194.52 700.00\n")
        self.w(self.proc / "sys/kernel/random/boot_id", "1a2b3c4d-1111-2222-3333-444455556666\n")
        self.w(self.fake / "lspci.out",
               "0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GP104 [GeForce GTX 1070 Ti] "
               "[10de:1b82] (rev a1)\n0000:00:1f.2 SATA controller [0106]: Intel Corporation [8086:8c02]\n")
        self.w(self.fake / "ip.out", "2: eth0    inet 192.168.77.2/30 brd 192.168.77.3 scope global eth0\n")
        self.w(self.fake / "ping.out", PING_OK)
        self.w(self.fake / "iperf.out", IPERF_OK)
        self.w(self.fake / "probe.json", PROBE_OK + "\n")
        self.w(self.fake / "smi.query", SMI_QUERY)

    # -- execution -----------------------------------------------------------
    def run(self, tool: str, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
        path = self.bin / tool
        return subprocess.run([SH, str(path), *args], env=self.env, capture_output=True, text=True, timeout=timeout)

    def agent(self) -> dict:
        proc = self.run("nodeos-agent", "--once")
        self.case.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return json.loads(proc.stdout)

    def gates(self) -> dict:
        report = json.loads((self.run_dir / "gate-report.json").read_text())
        return {g["name"]: g for g in report["gates"]}


class LabTestBase(unittest.TestCase):
    def lab(self, **kw) -> Lab:
        return Lab(self, **kw)


@unittest.skipUnless(HAVE_SH, "POSIX sh required")
class StateMachineTests(LabTestBase):
    def test_healthy_node_reaches_node_ready_with_full_telemetry(self):
        lab = self.lab()
        cap = lab.agent()
        self.assertEqual(cap["state"], "NODE_READY")
        self.assertEqual(cap["verdict"], "PASS")
        self.assertTrue((lab.run_dir / "ready").exists())
        self.assertEqual(cap["system"]["boot_id"], "1a2b3c4d-1111-2222-3333-444455556666")
        self.assertEqual(cap["system"]["nodeos_commit"], "abc1234def5678abc1234def5678abc1234def56")
        self.assertEqual(cap["system"]["buildroot_commit"], "d5180309b1b66ef3b8eaccca70ad69be8e0729a1")
        self.assertTrue(cap["system"]["uefi"])
        self.assertEqual(len(cap["system"]["nvidia_payload_sha256"]), 64)
        self.assertEqual(cap["system"]["cuda"]["compute_capability"], "6.1")
        self.assertEqual(cap["system"]["cuda"]["runtime_version"], 12040)
        self.assertEqual(cap["cpu"]["logical_processors"], 2)
        net = cap["network"]
        self.assertEqual((net["interface"], net["driver"], net["speed_mbps"], net["duplex"], net["mtu"]),
                         ("eth0", "r8169", 1000, "full", 1500))
        self.assertEqual(net["ipv4"], "192.168.77.2/30")
        self.assertIs(net["carrier"], True)
        self.assertEqual(net["rx_bytes"], 123456)
        acc = cap["accelerators"][0]
        self.assertEqual(acc["name"], "NVIDIA GeForce GTX 1070 Ti")
        self.assertEqual(acc["uuid"], "GPU-aaaa-bbbb")
        self.assertEqual(acc["driver"], "535.54")
        self.assertEqual(acc["memory_mib"], {"total": 8192, "used": 326, "free": 7866})
        self.assertEqual(acc["temperature_c"], 38)
        self.assertEqual(acc["power_w"], {"draw": 12.34, "limit": 180.0})
        self.assertEqual(acc["pcie"]["current"]["generation"], 3)
        self.assertEqual(acc["pcie"]["current"]["width"], 8)
        self.assertEqual(acc["pcie"]["maximum"]["width"], 16)
        self.assertIs(acc["pcie"]["downtrained_now"], True)
        self.assertEqual(cap["health"]["cuda_smoke"], "PASS")
        pcie = json.loads((lab.run_dir / "pcie.json").read_text())
        self.assertEqual(pcie["best_observed"], {"generation": 3, "width": 8})
        gates = lab.gates()
        for name in ("uefi", "kernel", "nic", "link", "gpu", "payload", "driver", "cuda", "network"):
            self.assertEqual(gates[name]["status"], "PASS", name)
        log = lab.log.read_text()
        self.assertIn("NODE STATE BOOTING -> NODE_READY", log)
        self.assertIn("GATE cuda PASS", log)

    def assertBlocked(self, lab: Lab, state: str, reason: str):
        cap = lab.agent()
        self.assertEqual(cap["state"], state, cap)
        self.assertEqual(cap["state_reason"], reason)
        self.assertEqual(cap["verdict"], "FAIL")
        self.assertFalse((lab.run_dir / "ready").exists())
        self.assertIn(f"NODE STATE BOOTING -> {state}", lab.log.read_text())
        return cap

    def test_no_nic(self):
        lab = self.lab()
        shutil.rmtree(lab.sysfs / "class/net/eth0")
        self.assertBlocked(lab, "BLOCKED_NO_NIC", "no-nic")

    def test_no_carrier(self):
        lab = self.lab()
        lab.w(lab.sysfs / "class/net/eth0/carrier", "0\n")
        self.assertBlocked(lab, "BLOCKED_NO_CARRIER", "no-carrier")

    def test_no_ipv4(self):
        lab = self.lab()
        lab.w(lab.fake / "ip.out", "")
        self.assertBlocked(lab, "BLOCKED_NO_IP", "no-ipv4")

    def test_gpu_missing(self):
        lab = self.lab()
        lab.w(lab.fake / "lspci.out", "0000:00:1f.2 SATA controller [0106]: Intel Corporation [8086:8c02]\n")
        cap = self.assertBlocked(lab, "BLOCKED_GPU_MISSING", "gpu-missing")
        self.assertEqual(json.loads((lab.run_dir / "pcie.json").read_text())["source"], "none")
        self.assertEqual(cap["health"]["cuda_smoke"], "PASS")  # independent gates stay independent

    def test_payload_missing_blocks_driver_stage(self):
        lab = self.lab()
        (lab.etc / "nvidia-payload-manifest.json").unlink()
        self.assertBlocked(lab, "BLOCKED_DRIVER", "payload-missing")

    def test_payload_digest_mismatch(self):
        lab = self.lab()
        lab.payload_file.write_text("tampered")
        self.assertBlocked(lab, "BLOCKED_DRIVER", "payload-digest-mismatch")

    def test_driver_not_ready(self):
        lab = self.lab()
        (lab.fake / "smi.fail").write_text("")
        cap = self.assertBlocked(lab, "BLOCKED_DRIVER", "driver-not-ready")
        self.assertEqual(lab.gates()["cuda"]["status"], "NA")
        self.assertEqual(cap["health"]["cuda_smoke"], "UNAVAILABLE")

    def test_cuda_probe_failure(self):
        lab = self.lab()
        (lab.fake / "probe.rc").write_text("1\n")
        cap = self.assertBlocked(lab, "BLOCKED_CUDA", "cuda-probe-failed")
        self.assertEqual(cap["health"]["cuda_smoke"], "FAIL")

    def test_cuda_capability_mismatch(self):
        lab = self.lab(conf_extra="NODEOS_EXPECTED_CC=8.9\n")
        self.assertBlocked(lab, "BLOCKED_CUDA", "cuda-capability-mismatch")

    def test_peer_unreachable_is_degraded_link(self):
        lab = self.lab()
        lab.w(lab.fake / "ping.out", PING_DEAD)
        (lab.fake / "ping.rc").write_text("1\n")
        cap = self.assertBlocked(lab, "DEGRADED_LINK", "peer-unreachable")
        self.assertEqual(cap["health"]["cuda_smoke"], "PASS")

    def test_slow_link_is_degraded_link(self):
        lab = self.lab(speed="100")
        self.assertBlocked(lab, "DEGRADED_LINK", "link-speed-low")

    def test_rx_errors_degrade_link(self):
        lab = self.lab()
        lab.w(lab.sysfs / "class/net/eth0/statistics/rx_errors", "7\n")
        self.assertBlocked(lab, "DEGRADED_LINK", "link-errors")

    def test_low_throughput_degrades_link(self):
        lab = self.lab()
        lab.w(lab.fake / "iperf.out", "[  5]   0.00-5.00   sec  40 MBytes  60.0 Mbits/sec   receiver\n")
        self.assertBlocked(lab, "DEGRADED_LINK", "throughput-low")

    def test_missing_iperf_server_is_inconclusive_not_failure(self):
        lab = self.lab()
        (lab.fake / "iperf.rc").write_text("1\n")
        lab.w(lab.fake / "iperf.out", "iperf3: error - unable to connect to server\n")
        cap = lab.agent()
        self.assertEqual(cap["state"], "NODE_READY")
        net = json.loads((lab.run_dir / "network.json").read_text())
        self.assertEqual(net["iperf"]["result"], "SKIPPED_NO_SERVER")

    def test_peer_not_required_keeps_ready_but_verdict_fails(self):
        lab = self.lab(conf_extra="NODEOS_REQUIRE_PEER=0\n")
        lab.w(lab.fake / "ping.out", PING_DEAD)
        (lab.fake / "ping.rc").write_text("1\n")
        cap = lab.agent()
        self.assertEqual(cap["state"], "DEGRADED_LINK")
        self.assertEqual(cap["verdict"], "FAIL")

    def test_no_peer_configured_is_incomplete(self):
        lab = self.lab(conf_extra="NODEOS_PEER_IPV4=\nNODEOS_REQUIRE_PEER=0\n")
        cap = lab.agent()
        self.assertEqual(cap["state"], "NODE_READY")
        self.assertEqual(cap["verdict"], "INCOMPLETE")
        self.assertFalse((lab.run_dir / "ready").exists())

    def test_no_peer_configured_with_require_peer_blocks_state(self):
        lab = self.lab(conf_extra="NODEOS_PEER_IPV4=\nNODEOS_REQUIRE_PEER=1\n")
        cap = lab.agent()
        self.assertEqual(cap["state"], "DEGRADED_LINK")
        self.assertEqual(cap["state_reason"], "no-peer-configured")
        self.assertEqual(cap["verdict"], "FAIL")
        self.assertFalse((lab.run_dir / "ready").exists())

    def test_not_uefi_fails_verdict(self):
        lab = self.lab()
        shutil.rmtree(lab.sysfs / "firmware/efi")
        cap = lab.agent()
        self.assertEqual(cap["verdict"], "FAIL")
        self.assertFalse((lab.run_dir / "ready").exists())

    def test_recovery_clears_ready_flag_and_logs_transition(self):
        lab = self.lab()
        self.assertEqual(lab.agent()["state"], "NODE_READY")
        lab.w(lab.sysfs / "class/net/eth0/carrier", "0\n")
        self.assertEqual(lab.agent()["state"], "BLOCKED_NO_CARRIER")
        self.assertFalse((lab.run_dir / "ready").exists())
        self.assertFalse((lab.run_dir / "network.json").exists())
        self.assertIn("NODE STATE NODE_READY -> BLOCKED_NO_CARRIER", lab.log.read_text())


@unittest.skipUnless(HAVE_SH, "POSIX sh required")
class NetworkGateTests(LabTestBase):
    def test_full_gate_reports_ping_and_iperf(self):
        lab = self.lab()
        proc = lab.run("nodeos-netgate", "--full")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        net = json.loads((lab.run_dir / "network.json").read_text())
        self.assertEqual(net["status"], "PASS")
        self.assertEqual(net["peer"]["ping"], {"result": "PASS", "loss_pct": 0, "rtt_ms": 0.31})
        self.assertEqual(net["iperf"]["mbit_s"], 941)
        self.assertEqual(net["link"]["speed_mbps"], 1000)
        self.assertIn("NETWORK GATE        PASS", proc.stdout)

    def test_failure_exit_code(self):
        lab = self.lab()
        lab.w(lab.fake / "ping.out", PING_DEAD)
        (lab.fake / "ping.rc").write_text("1\n")
        proc = lab.run("nodeos-netgate", "--ping")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads((lab.run_dir / "network.json").read_text())["status"], "FAIL")

    def test_partial_packet_loss_fails(self):
        lab = self.lab()
        lab.w(lab.fake / "ping.out", PING_OK.replace("0% packet loss", "25% packet loss"))
        self.assertEqual(lab.run("nodeos-netgate", "--ping").returncode, 1)
        self.assertIn("ping-loss", (lab.run_dir / "network.env").read_text())

    def test_ping_mode_keeps_previous_iperf_result(self):
        lab = self.lab()
        lab.run("nodeos-netgate", "--full")
        lab.run("nodeos-netgate", "--ping")
        net = json.loads((lab.run_dir / "network.json").read_text())
        self.assertEqual(net["iperf"]["mbit_s"], 941)

    def test_bad_option(self):
        self.assertEqual(self.lab().run("nodeos-netgate", "--bogus").returncode, 2)


@unittest.skipUnless(HAVE_SH, "POSIX sh required")
class EvidenceTests(LabTestBase):
    def collect(self, lab: Lab):
        proc = lab.run("nodeos-evidence", "collect", "--refresh")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def test_bundle_contents_hashes_and_banner(self):
        lab = self.lab()
        proc = self.collect(lab)
        bundle = lab.run_dir / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        for name in ("manifest.json", "capability.json", "cuda-smoke.json", "network.json", "pcie.json",
                     "nvidia-smi.txt", "lspci.txt", "dmesg-tail.txt", "nodeos-agent.log", "gate-report.json",
                     "SHA256SUMS", "build-info.txt", "ethtool.txt"):
            self.assertTrue((bundle / name).is_file(), name)
        manifest = json.loads((bundle / "manifest.json").read_text())
        self.assertEqual(manifest["verdict"], "PASS")
        self.assertEqual(manifest["state"], "NODE_READY")
        self.assertNotIn("manifest.json", [f["name"] for f in manifest["files"]])
        for entry in manifest["files"]:
            self.assertEqual(hashlib.sha256((bundle / entry["name"]).read_bytes()).hexdigest(), entry["sha256"])
        evidence_id = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
        self.assertIn("NODEOS-001 PHYSICAL PASS", proc.stdout)
        self.assertIn(evidence_id, proc.stdout)
        self.assertIn("nodeos-evidence-export", proc.stdout)
        self.assertTrue((lab.persist / "1a2b3c4d-1111-2222-3333-444455556666/manifest.json").is_file())
        verify = subprocess.run([sys.executable, str(ROOT / "tools/verify-evidence.py"), str(bundle)],
                                capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stdout)
        self.assertIn(evidence_id, verify.stdout)

    def test_fail_verdict_is_recorded_and_tampering_is_detected(self):
        lab = self.lab()
        (lab.fake / "probe.rc").write_text("1\n")
        proc = self.collect(lab)
        self.assertIn("NODEOS-001 PHYSICAL FAIL (BLOCKED_CUDA: cuda-probe-failed)", proc.stdout)
        bundle = lab.run_dir / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        tool = [sys.executable, str(ROOT / "tools/verify-evidence.py"), str(bundle)]
        self.assertEqual(subprocess.run(tool, capture_output=True, text=True).returncode, 1)  # FAIL verdict
        (bundle / "lspci.txt").write_text("forged")
        out = subprocess.run(tool, capture_output=True, text=True)
        self.assertIn("bundle integrity: BROKEN", out.stdout)
        self.assertIn("lspci.txt", out.stdout)

    def test_incomplete_banner_when_peer_unmeasured(self):
        lab = self.lab(conf_extra="NODEOS_PEER_IPV4=\nNODEOS_REQUIRE_PEER=0\n")
        self.assertIn("PHYSICAL INCOMPLETE", self.collect(lab).stdout)

    def test_export_tar_roundtrip(self):
        lab = self.lab()
        self.collect(lab)
        dest = lab.tmp / "usb"
        proc = lab.run("nodeos-evidence", "export", str(dest))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        tars = list(dest.glob("nodeos-evidence-*.tar"))
        self.assertEqual(len(tars), 1)
        verify = subprocess.run([sys.executable, str(ROOT / "tools/verify-evidence.py"), str(tars[0])],
                                capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stdout)
        self.assertIn("scp -i <key> root@", proc.stdout)

    def test_export_alias_name(self):
        lab = self.lab()
        alias = lab.bin / "nodeos-evidence-export"
        shutil.copy(lab.bin / "nodeos-evidence", alias)
        proc = subprocess.run([SH, str(alias)], env=lab.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bundle:", proc.stdout)

    def test_verify_subcommand(self):
        lab = self.lab()
        self.collect(lab)
        bundle = lab.run_dir / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        self.assertEqual(lab.run("nodeos-evidence", "verify", str(bundle)).returncode, 0)
        (bundle / "capability.json").write_text("{}")
        self.assertNotEqual(lab.run("nodeos-evidence", "verify", str(bundle)).returncode, 0)

    def test_agent_loop_collects_evidence_automatically_once_stable(self):
        lab = self.lab(conf_extra="NODEOS_EVIDENCE_STABLE_CYCLES=2\nNODEOS_AGENT_INTERVAL=1\n")
        proc = subprocess.Popen([SH, str(lab.bin / "nodeos-agent"), "run"], env=lab.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            latest = lab.run_dir / "evidence/latest.env"
            deadline = time.time() + 40
            while time.time() < deadline and not latest.exists():
                time.sleep(0.5)
            self.assertTrue(latest.exists(), "no automatic evidence bundle")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertIn("EVIDENCE_VERDICT='PASS'", latest.read_text())
        self.assertIn("EVIDENCE id=", lab.log.read_text())

    def test_pcie_best_link_pair_capacity_ranking_and_counterexample(self):
        # Counterexample to linear gen*width heuristic:
        # Gen3 x8 (985*8 = 7880 MB/s) vs Gen5 x4 (3938*4 = 15752 MB/s)
        # Linear gen*width heuristic: Gen3 x8 = 3*8 = 24 vs Gen5 x4 = 5*4 = 20. Linear says Gen3 x8 > Gen5 x4.
        # True bandwidth: Gen5 x4 (15752 MB/s) > Gen3 x8 (7880 MB/s).
        lab = self.lab()
        pci = lab.sysfs / "bus/pci/devices/0000:01:00.0"

        # Step 1: Gen5 x4 (15752 MB/s)
        lab.w(pci / "current_link_speed", "32.0 GT/s PCIe\n")
        lab.w(pci / "current_link_width", "4\n")
        lab.agent()
        pcie1 = json.loads((lab.run_dir / "pcie.json").read_text())
        self.assertEqual(pcie1["best_observed"], {"generation": 5, "width": 4})

        # Step 2: Gen3 x8 (7880 MB/s) -> 7880 MB/s < 15752 MB/s, so Gen3 x8 MUST NOT update best_observed!
        lab.w(pci / "current_link_speed", "8.0 GT/s PCIe\n")
        lab.w(pci / "current_link_width", "8\n")
        lab.agent()
        pcie2 = json.loads((lab.run_dir / "pcie.json").read_text())
        self.assertEqual(pcie2["best_observed"], {"generation": 5, "width": 4})

    def test_pcie_best_link_pair_tie_break(self):
        # Tie-break: Gen1 x4 (250*4 = 1000 MB/s) vs Gen2 x2 (500*2 = 1000 MB/s)
        # Higher generation Gen2 x2 should win on tie-break.
        lab = self.lab()
        pci = lab.sysfs / "bus/pci/devices/0000:01:00.0"

        lab.w(pci / "current_link_speed", "2.5 GT/s PCIe\n")
        lab.w(pci / "current_link_width", "4\n")
        lab.agent()

        lab.w(pci / "current_link_speed", "5.0 GT/s PCIe\n")
        lab.w(pci / "current_link_width", "2\n")
        lab.agent()

        pcie = json.loads((lab.run_dir / "pcie.json").read_text())
        self.assertEqual(pcie["best_observed"], {"generation": 2, "width": 2})

    def test_pcie_best_link_invalid_or_empty_values(self):
        lab = self.lab()
        # Corrupt pcie-best.env
        lab.w(lab.run_dir / "pcie-best.env", "BEST_GEN='invalid'\nBEST_WIDTH='corrupted'\n")
        lab.agent()
        pcie = json.loads((lab.run_dir / "pcie.json").read_text())
        self.assertEqual(pcie["best_observed"], {"generation": 3, "width": 8})

    def test_verify_evidence_cli_expected_cc_options(self):
        lab = self.lab()
        self.collect(lab)
        bundle = lab.run_dir / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        tool = [sys.executable, str(ROOT / "tools/verify-evidence.py"), str(bundle)]

        proc = subprocess.run(tool + ["--expected-cc", "6.1"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)

        proc2 = subprocess.run(tool + ["--expected-cc", "8.9"], capture_output=True, text=True)
        self.assertEqual(proc2.returncode, 1, proc2.stdout)
        self.assertIn("compute capability mismatch", proc2.stdout)

    def test_verify_evidence_negative_cases(self):
        lab = self.lab()
        self.collect(lab)
        bundle = lab.run_dir / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        verifier = [sys.executable, str(ROOT / "tools/verify-evidence.py"), str(bundle)]

        def reseal(filename: str, content: str):
            (bundle / filename).write_text(content)
            manifest = json.loads((bundle / "manifest.json").read_text())
            for entry in manifest["files"]:
                if entry["name"] == filename:
                    entry["bytes"] = len(content.encode())
                    entry["sha256"] = hashlib.sha256(content.encode()).hexdigest()
            (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))
            sums_lines = []
            for f in bundle.iterdir():
                if f.is_file() and f.name != "SHA256SUMS":
                    sums_lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
            (bundle / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n")

        orig_cs = (bundle / "cuda-smoke.json").read_text()
        orig_pcie = (bundle / "pcie.json").read_text()
        orig_net = (bundle / "network.json").read_text()
        orig_gr = (bundle / "gate-report.json").read_text()

        def reset_and_seal(filename: str, content: str):
            (bundle / "cuda-smoke.json").write_text(orig_cs)
            (bundle / "pcie.json").write_text(orig_pcie)
            (bundle / "network.json").write_text(orig_net)
            (bundle / "gate-report.json").write_text(orig_gr)
            (bundle / filename).write_text(content)

            manifest = json.loads((bundle / "manifest.json").read_text())
            manifest["missing"] = ""
            file_map = {}
            for f in bundle.iterdir():
                if f.is_file() and f.name not in ("manifest.json", "SHA256SUMS"):
                    b = f.read_bytes()
                    file_map[f.name] = {"name": f.name, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
            manifest["files"] = list(file_map.values())
            (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))

            sums_lines = []
            for f in sorted(bundle.iterdir()):
                if f.is_file() and f.name != "SHA256SUMS":
                    sums_lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
            (bundle / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n")

        # 1. Invalid CUDA Compute Capability syntax
        reset_and_seal("cuda-smoke.json", orig_cs.replace('"6.1"', '"invalid-cc"'))
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid CUDA compute capability syntax", proc.stdout)

        # 2. Invalid PCI address format in pcie.json
        reset_and_seal("pcie.json", orig_pcie.replace('"0000:01:00.0"', '"invalid-pci-bdf"'))
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid PCI address format", proc.stdout)

        # 3. Invalid pcie source
        reset_and_seal("pcie.json", orig_pcie.replace('"sysfs"', '"fake-driver"'))
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid pcie source", proc.stdout)

        # 4. Contradictory network data between capability and network.json
        reset_and_seal("network.json", orig_net.replace('"eth0"', '"eth1"'))
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("network inconsistency", proc.stdout)

        # 5. Broken JSON
        reset_and_seal("gate-report.json", "{ broken json ")
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("gate-report.json error", proc.stdout)

        # 6. Contradictory state/verdict
        reset_and_seal("gate-report.json", orig_gr.replace('"PASS"', '"FAIL"'))
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("semantic mismatch", proc.stdout)

        # 7. PASS verdict with missing file declared in manifest
        (bundle / "cuda-smoke.json").write_text(orig_cs)
        (bundle / "pcie.json").write_text(orig_pcie)
        (bundle / "network.json").write_text(orig_net)
        (bundle / "gate-report.json").write_text(orig_gr)
        (bundle / "cuda-smoke.json").unlink()
        mf = json.loads((bundle / "manifest.json").read_text())
        mf["missing"] = "cuda-smoke.json"
        mf["files"] = [e for e in mf["files"] if e["name"] != "cuda-smoke.json"]
        (bundle / "manifest.json").write_text(json.dumps(mf, indent=2))
        sums_lines = []
        for f in sorted(bundle.iterdir()):
            if f.is_file() and f.name != "SHA256SUMS":
                sums_lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
        (bundle / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n")
        proc = subprocess.run(verifier, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("PASS bundle has missing files declared", proc.stdout)


class HostVerifyEvidenceTests(unittest.TestCase):
    def make_bundle(self, tmp: Path) -> Path:
        bundle = tmp / "evidence/1a2b3c4d-1111-2222-3333-444455556666"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "manifest.json").write_text(json.dumps({
            "schema": "resonarch.nodeos.evidence-manifest.v1",
            "node_id": "test-node", "boot_id": "1a2b3c4d",
            "verdict": "PASS", "state": "NODE_READY", "missing": "",
            "profile": "haswell-gtx1070ti",
            "files": []
        }))
        (bundle / "cuda-smoke.json").write_text(json.dumps({"status": "PASS", "compute_capability": "6.1"}))
        (bundle / "capability.json").write_text(json.dumps({
            "schema": "resonarch.nodeos.capability.v1",
            "node_id": "test-node",
            "verdict": "PASS", "state": "NODE_READY",
            "cpu": {}, "memory": {},
            "health": {"cuda_smoke": "PASS"},
            "system": {"cuda": {"compute_capability": "6.1"}},
            "network": {"interface": "eth0", "ipv4": "192.168.77.2"},
            "accelerators": [{"uuid": "GPU-123", "pci_address": "0000:01:00.0"}]
        }))
        (bundle / "gate-report.json").write_text(json.dumps({
            "schema": "resonarch.nodeos.gate-report.v1", 
            "node_id": "test-node", "boot_id": "1a2b3c4d",
            "verdict": "PASS", "state": "NODE_READY",
            "gates": [{"name": "gpu", "stage": "GPU_DISCOVERED", "status": "PASS", "blocking": True}]
        }))
        (bundle / "network.json").write_text(json.dumps({"link": {"interface": "eth0", "ipv4": "192.168.77.2"}}))
        (bundle / "pcie.json").write_text(json.dumps({"source": "sysfs", "address": "0000:01:00.0"}))
        (bundle / "nvidia-smi.txt").write_text("GPU-123")
        (bundle / "lspci.txt").write_text("")
        (bundle / "dmesg-tail.txt").write_text("")
        (bundle / "nodeos-agent.log").write_text("")
        self._seal(bundle)
        return bundle

    def _seal(self, bundle: Path):
        manifest = json.loads((bundle / "manifest.json").read_text())
        file_map = {}
        for f in bundle.iterdir():
            if f.is_file() and f.name not in ("manifest.json", "SHA256SUMS"):
                b = f.read_bytes()
                file_map[f.name] = {"name": f.name, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        manifest["files"] = list(file_map.values())
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))
        sums_lines = []
        for f in sorted(bundle.iterdir()):
            if f.is_file() and f.name != "SHA256SUMS":
                sums_lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
        (bundle / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n")

    def test_verify_evidence_cc_and_profile_rules(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bundle = self.make_bundle(tmp)
        verifier = [sys.executable, str(ROOT / "tools/verify-evidence.py")]
        
        orig_cs = (bundle / "cuda-smoke.json").read_text()
        
        def reset_and_check(cs_content: str, args: list[str], expected_in_out: str):
            (bundle / "cuda-smoke.json").write_text(cs_content)
            self._seal(bundle)
            proc = subprocess.run(verifier + args + [str(bundle)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, f"Expected fail for {args}")
            self.assertIn(expected_in_out, proc.stdout + proc.stderr)

        reset_and_check(orig_cs.replace('"compute_capability": "6.1"', '"compute_capability": ""'), [], "missing or invalid type for compute_capability")
        reset_and_check(orig_cs.replace('"6.1"', '6.1'), [], "missing or invalid type for compute_capability")
        reset_and_check(orig_cs, ["--expected-cc", "8.9", "--expected-profile", "haswell-gtx1070ti"], "contradictory expectations")
        
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest["profile"] = "unknown-profile"
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))
        reset_and_check(orig_cs, [], "unknown bundle profile: unknown-profile")

    def test_verify_evidence_jsonschema_missing(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bundle = self.make_bundle(tmp)
        
        mask_script = tmp / "mask.py"
        mask_script.write_text(
            "import sys\n"
            "sys.modules['jsonschema'] = None\n"
            f"with open(r'{ROOT}/tools/verify-evidence.py') as f: exec(f.read())\n"
        )
        
        proc = subprocess.run([sys.executable, str(mask_script), str(bundle)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("jsonschema module is missing", proc.stdout)

    def test_verify_evidence_tar_safety(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        
        unsafe_tar = tmp / "unsafe.tar"
        escape_target = tmp / "escaped.txt"
        with tarfile.open(unsafe_tar, "w") as tar:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"{}"))
            
        verifier = [sys.executable, str(ROOT / "tools/verify-evidence.py")]
        proc = subprocess.run(verifier + [str(unsafe_tar)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unsafe tar member path", proc.stdout)
        self.assertFalse(escape_target.exists(), "Tarball extraction escaped its root directory!")
        
        dup_tar = tmp / "dup.tar"
        with tarfile.open(dup_tar, "w") as tar:
            for _ in range(2):
                info = tarfile.TarInfo("manifest.json")
                info.size = 2
                tar.addfile(info, io.BytesIO(b"{}"))
        proc = subprocess.run(verifier + [str(dup_tar)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate member path", proc.stdout)

    def test_verify_evidence_manifest_and_sha256sums_safety(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        verifier = [sys.executable, str(ROOT / "tools/verify-evidence.py")]

        # Test 1: Absolute path in manifest.json
        bundle = self.make_bundle(tmp)
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest["files"].append({"name": "/etc/passwd", "sha256": "fake", "bytes": 10})
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        proc = subprocess.run(verifier + [str(bundle)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("absolute paths are not allowed", proc.stdout)
        
        # Test 2: Parent traversal in SHA256SUMS
        bundle = self.make_bundle(tmp)
        sums = bundle / "SHA256SUMS"
        sums.write_text(sums.read_text() + "fakehash  ../outside.txt\n")
        proc = subprocess.run(verifier + [str(bundle)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("parent traversal is not allowed", proc.stdout)

        # Test 3: Duplicate entry in manifest.json
        bundle = self.make_bundle(tmp)
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest["files"].append(manifest["files"][0])
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        proc = subprocess.run(verifier + [str(bundle)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate manifest entry", proc.stdout)

        # Test 4: Symlink in bundle
        bundle = self.make_bundle(tmp)
        if sys.platform != "win32": # Symlinks on Windows require admin privileges, skip on Windows
            (bundle / "symlink.txt").symlink_to("capability.json")
            sums = bundle / "SHA256SUMS"
            sums.write_text(sums.read_text() + "fakehash  symlink.txt\n")
            proc = subprocess.run(verifier + [str(bundle)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("symlinks are not allowed", proc.stdout)

        # Test 5: Successful baseline on synthetic bundle
        bundle = self.make_bundle(tmp)
        proc = subprocess.run(verifier + [str(bundle)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Expected baseline to pass, got: {proc.stdout}")
        self.assertIn("Evidence ID:", proc.stdout)
        self.assertIn("bundle integrity: OK", proc.stdout)


@unittest.skipUnless(HAVE_SH, "POSIX sh required")
class ConsoleTests(LabTestBase):
    def test_frame_shows_gates_hardware_fabric_and_log(self):
        lab = self.lab()
        lab.agent()
        frame = lab.run("nodeos-console", "--once").stdout
        for needle in ("resonArch NodeOS", "node: test-node", "uptime: 00:03:14", "build: abc1234",
                       "[ OK ] UEFI_BOOT", "[ OK ] CUDA_PROBE_PASS", "[ OK ] NODE_READY",
                       "i7-4770K", "GeForce GTX 1070 Ti", "Gen3 x8", "(max Gen3 x16)", "38 C",
                       "192.168.77.2/30", "1000 Mbit/s", "941 Mbit/s", "0.310 ms", "VERDICT PASS",
                       "LIVE LOG", "NODE STATE BOOTING -> NODE_READY", "[R] rerun gates", "[E] export evidence"):
            self.assertIn(needle, frame)
        widths = {len(line) for line in frame.splitlines() if line.startswith(("|", "+"))}
        self.assertEqual(widths, {80}, "frame rows must have a stable width")
        self.assertNotIn("\x1b", frame)

    def test_frame_names_the_failing_gate(self):
        lab = self.lab()
        (lab.fake / "probe.rc").write_text("1\n")
        lab.agent()
        frame = lab.run("nodeos-console", "--once").stdout
        self.assertIn("STATE BLOCKED_CUDA", frame)
        self.assertIn("reason: cuda-probe-failed", frame)
        self.assertIn("[FAIL] NODE_READY", frame)

    def test_frame_without_agent(self):
        lab = self.lab()
        self.assertIn("waiting for nodeos-agent", lab.run("nodeos-console", "--once").stdout)

    def test_console_sourcing_guard(self):
        # Sourcing nodeos-console must not run interactive event loop or produce top-level stdout
        lab = self.lab()
        lab.agent()
        proc = subprocess.run([SH, "-c", f'NODEOS_RUN="{lab.run_dir}" . "{SRC / "nodeos-lib.sh"}"; . "{SRC / "nodeos-console"}"'],
                              env=lab.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_console_compact_rendering_max_22_lines_and_80_columns(self):
        lab = self.lab()
        lab.w(lab.fake / "lspci.out", "0000:01:00.0 VGA: NVIDIA GP104 " + "x" * 120 + "\n")
        lab.w(lab.log, "line 1 " + "a" * 150 + "\nline 2 " + "b" * 150 + "\nline 3 " + "c" * 150 + "\n")
        lab.agent()
        proc = lab.run("nodeos-console", "--compact")
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.splitlines()
        self.assertLessEqual(len(lines), 22, f"compact frame has {len(lines)} lines, expected <= 22")
        borders = [line for line in lines if line.startswith(("|", "+"))]
        self.assertTrue(borders)
        for b in borders:
            self.assertEqual(len(b), 80, f"line length is {len(b)}, expected 80: {b}")

    def test_console_cli_flags_once_compact_full(self):
        lab = self.lab()
        lab.agent()
        once_out = lab.run("nodeos-console", "--once").stdout
        self.assertIn("SYSTEM STATE", once_out)
        self.assertIn("FABRIC LINK", once_out)

        compact_out = lab.run("nodeos-console", "--compact").stdout
        self.assertIn("(Compact)", compact_out)

        full_out = lab.run("nodeos-console", "--full").stdout
        self.assertIn("SYSTEM STATE", full_out)
        self.assertIn("FABRIC LINK", full_out)


class HostPowerShellTests(unittest.TestCase):
    def test_powershell_script_syntax_and_mock_runs(self):
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps is None:
            self.skipTest("No PowerShell executable found")

        script = ROOT / "tools/dell-directlan-host.ps1"

        # Test 1: Syntax Parse
        syntax_check = subprocess.run(
            [ps, "-NoProfile", "-Command", f"& {{ $errs=$null; [System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$errs); if($errs) {{ throw 'Syntax error' }} }}"],
            capture_output=True, text=True
        )
        self.assertEqual(syntax_check.returncode, 0, f"PowerShell syntax errors:\n{syntax_check.stderr}\n{syntax_check.stdout}")

        tmp = Path(tempfile.mkdtemp(prefix="nodeos-host-ps-"))
        self.addCleanup(shutil.rmtree, tmp, True)

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        out_dir = tmp / "downloads"
        out_dir.mkdir()

        key_file = tmp / "id_ed25519"
        key_file.write_text("fake-ssh-key\n")

        # Mock scripts for Windows/Linux
        ext = ".cmd" if sys.platform == "win32" else ""

        ssh_mock = bin_dir / f"ssh{ext}"
        scp_mock = bin_dir / f"scp{ext}"
        python_mock = bin_dir / f"python{ext}"

        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\\nEXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
            scp_mock.write_text(f"@\"{sys.executable}\" -c \"import sys, os; d=sys.argv[-1]; open(os.path.join(d, 'nodeos-evidence-123.tar'), 'w').write('mock tar content')\" %*\nexit /b 0\n")
            python_mock.write_text(f"@\"{sys.executable}\" -c \"print('Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\necho EXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666\nexit 0\n")
            scp_mock.write_text("#!/bin/sh\neval dest=\\$$#\necho mock tar content > \"$dest/nodeos-evidence-123.tar\"\nexit 0\n")
            python_mock.write_text("#!/bin/sh\necho 'Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666'\nexit 0\n")
            ssh_mock.chmod(0o755)
            scp_mock.chmod(0o755)
            python_mock.chmod(0o755)

        cmd = [
            ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-SshKeyPath", str(key_file), "-FetchEvidence", "-OutputDir", str(out_dir),
            "-SshCmd", str(ssh_mock), "-ScpCmd", str(scp_mock), "-PythonCmd", str(python_mock),
            "-SkipNetworkProbe"
        ]

        # Test 2: Success Run
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Expected success run to exit 0. Stdout:\n{proc.stdout}\nStderr:\n{proc.stderr}")
        self.assertIn("SUCCESS EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666", proc.stdout)
        self.assertIn("Evidence verification SUCCESSFUL", proc.stderr)

        # Test 3: Export failure (mock ssh fail) -> no scp, no verifier
        if sys.platform == "win32":
            ssh_mock.write_text("@echo off\nexit /b 1\n")
            scp_mock.write_text("@echo off\necho SCP_CALLED > scp_called.txt\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\nexit 1\n")
            scp_mock.write_text("#!/bin/sh\necho SCP_CALLED > scp_called.txt\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: nodeos-evidence export failed", proc_fail.stderr)
        self.assertFalse((Path.cwd() / "scp_called.txt").exists(), "SCP was incorrectly called after export failed")

        # Test 4: SCP failure -> no verifier
        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\\nEXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
            scp_mock.write_text("@echo off\nexit /b 1\n")
            python_mock.write_text("@echo off\necho PYTHON_CALLED > py_called.txt\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\necho EXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666\nexit 0\n")
            scp_mock.write_text("#!/bin/sh\nexit 1\n")
            python_mock.write_text("#!/bin/sh\necho PYTHON_CALLED > py_called.txt\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: SCP transfer failed", proc_fail.stderr)
        self.assertFalse((Path.cwd() / "py_called.txt").exists(), "Verifier was incorrectly called after SCP failed")

        # Test 5: Verifier returns non-zero exit code
        if sys.platform == "win32":
            scp_mock.write_text(f"@\"{sys.executable}\" -c \"import sys, os; d=sys.argv[-1]; open(os.path.join(d, 'nodeos-evidence-123.tar'), 'w').write('mock tar content')\" %*\nexit /b 0\n")
            python_mock.write_text("@echo off\nexit /b 1\n")
        else:
            scp_mock.write_text("#!/bin/sh\neval dest=\\$$#\necho mock tar content > \"$dest/nodeos-evidence-123.tar\"\nexit 0\n")
            python_mock.write_text("#!/bin/sh\nexit 1\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Host verifier rejected evidence bundle", proc_fail.stderr)

        # Test 6: Export ID != Verifier ID
        if sys.platform == "win32":
            python_mock.write_text(f"@\"{sys.executable}\" -c \"print('Evidence ID: BAD1222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
        else:
            python_mock.write_text("#!/bin/sh\necho 'Evidence ID: BAD1222233334444555566667777888899990000111122223333444455556666'\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Evidence ID mismatch", proc_fail.stderr)

        # Test 7: Missing Export ID
        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar')\"\nexit /b 0\n")
            python_mock.write_text(f"@\"{sys.executable}\" -c \"print('Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\nexit 0\n")
            python_mock.write_text("#!/bin/sh\necho 'Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666'\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Export output did not provide EXPORT_EVIDENCE_ID", proc_fail.stderr)

        # Test 8: Empty download file
        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\\nEXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
            scp_mock.write_text(f"@\"{sys.executable}\" -c \"import sys, os; d=sys.argv[-1]; open(os.path.join(d, 'nodeos-evidence-123.tar'), 'w').write('')\" %*\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\necho EXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666\nexit 0\n")
            scp_mock.write_text("#!/bin/sh\neval dest=\\$$#\ntouch \"$dest/nodeos-evidence-123.tar\"\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Downloaded evidence tarball is 0 bytes", proc_fail.stderr)

        # Clean up markers
        if (Path.cwd() / "scp_called.txt").exists(): (Path.cwd() / "scp_called.txt").unlink()
        if (Path.cwd() / "py_called.txt").exists(): (Path.cwd() / "py_called.txt").unlink()

        # Test 9: Duplicate Export ID
        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\\nEXPORT_EVIDENCE_ID=111\\nEXPORT_EVIDENCE_ID=222')\"\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\necho EXPORT_EVIDENCE_ID=111\necho EXPORT_EVIDENCE_ID=222\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Found multiple EXPORT_EVIDENCE_ID entries. Refusing ambiguous export.", proc_fail.stderr)

        # Test 10: Duplicate Verifier ID
        if sys.platform == "win32":
            ssh_mock.write_text(f"@\"{sys.executable}\" -c \"print('EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\\nEXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666')\"\nexit /b 0\n")
            scp_mock.write_text(f"@\"{sys.executable}\" -c \"import sys, os; d=sys.argv[-1]; open(os.path.join(d, 'nodeos-evidence-123.tar'), 'w').write('mock tar content')\" %*\nexit /b 0\n")
            python_mock.write_text(f"@\"{sys.executable}\" -c \"print('Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666\\nEvidence ID: 2222333344445555666677778888999900001111222233334444555566667777')\"\nexit /b 0\n")
        else:
            ssh_mock.write_text("#!/bin/sh\necho EXPORT_BUNDLE=/run/nodeos/nodeos-evidence-123.tar\necho EXPORT_EVIDENCE_ID=1111222233334444555566667777888899990000111122223333444455556666\nexit 0\n")
            scp_mock.write_text("#!/bin/sh\neval dest=\\$$#\necho mock tar content > \"$dest/nodeos-evidence-123.tar\"\nexit 0\n")
            python_mock.write_text("#!/bin/sh\necho 'Evidence ID: 1111222233334444555566667777888899990000111122223333444455556666\\nEvidence ID: 2222333344445555666677778888999900001111222233334444555566667777'\nexit 0\n")
        proc_fail = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(proc_fail.returncode, 0)
        self.assertIn("ERROR: Verifier output must contain exactly one Evidence ID, found 2.", proc_fail.stderr)


@unittest.skipUnless(HAVE_SH, "POSIX sh required")
class LibraryTests(LabTestBase):
    def lib(self, script: str) -> str:
        env = dict(os.environ)
        out = subprocess.run([SH, "-c", f'. "{SRC / "nodeos-lib.sh"}"; {script}'], env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_sq_roundtrips_hostile_values(self):
        value = "it's a \"test\" $(rm -rf /) `x` \\ done"
        script = f"v={_shell_quote(value)}; q=$(sq \"$v\"); eval \"r=$q\"; printf '%s' \"$r\""
        self.assertEqual(self.lib(script), value)

    def test_json_helpers(self):
        self.assertEqual(self.lib("json_num 12.5; echo; json_num '[N/A]'; echo; json_num ''; echo; json_num 1.2.3"),
                         "12.5\nnull\nnull\nnull")
        self.assertEqual(self.lib('json_escape \'a"b\\c\''), 'a\\"b\\\\c')
        self.assertEqual(self.lib("json_bool 1; echo; json_bool 0; echo; json_bool x"), "true\nfalse\nnull")

    def test_state_name_tables(self):
        self.assertEqual(self.lib("nodeos_blocked_of link no-ipv4"), "BLOCKED_NO_IP\n")
        self.assertEqual(self.lib("nodeos_blocked_of link no-carrier"), "BLOCKED_NO_CARRIER\n")
        self.assertEqual(self.lib("nodeos_blocked_of network peer-unreachable"), "DEGRADED_LINK\n")
        self.assertEqual(self.lib("nodeos_stage_of cuda"), "CUDA_PROBE_PASS\n")
        self.assertIn("workstation", self.lib("nodeos_hint peer-unreachable"))


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


class StaticContractTests(unittest.TestCase):
    """Run everywhere, also on Windows without sh."""

    def test_scripts_parse(self):
        if not HAVE_SH:
            self.skipTest("no sh")
        files = [SRC / n for n in [*TOOLS, "nodeos-lib.sh"]] + [
            SMOKE, OVERLAY / "post-build.sh",
            OVERLAY / "rootfs-overlay/etc/init.d/S55nodeos-ssh", OVERLAY / "rootfs-overlay/etc/init.d/S56nodeos-iperf",
        ]
        for f in files:
            proc = subprocess.run([SH, "-n", str(f)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{f}: {proc.stderr}")

    def test_new_files_use_unix_line_endings(self):
        for f in [*(SRC / n for n in [*TOOLS, "nodeos-lib.sh"]), OVERLAY / "post-build.sh",
                  OVERLAY / "rootfs-overlay/etc/init.d/S55nodeos-ssh",
                  OVERLAY / "rootfs-overlay/etc/init.d/S56nodeos-iperf", SMOKE]:
            self.assertNotIn(b"\r\n", f.read_bytes(), f.name)

    def test_ssh_is_key_only_fail_closed_and_direct_lan(self):
        init = (OVERLAY / "rootfs-overlay/etc/init.d/S55nodeos-ssh").read_text()
        self.assertIn('[ -s "$KEY" ]', init)
        self.assertIn("fail closed", init)
        self.assertIn("-s -g", init)  # no password / no root password
        self.assertIn("-j -k", init)  # no forwarding
        self.assertIn("NODEOS_SSH_LISTEN", init)
        self.assertNotIn("password", init.lower().replace("no password", ""))
        conf = (OVERLAY / "rootfs-overlay/etc/nodeos/nodeos.conf").read_text()
        self.assertIn("NODEOS_SSH_LISTEN=192.168.77.2", conf)
        post = (OVERLAY / "post-build.sh").read_text()
        self.assertIn('rm -f "$TARGET_DIR/etc/init.d/S50dropbear"', post)
        self.assertIn("ssh-ed25519", post)

    def test_defconfig_has_lab_packages_and_console(self):
        cfg = (ROOT / "buildroot/configs/resonarch_haswell_gtx1070ti_defconfig").read_text()
        for needle in ("BR2_PACKAGE_DROPBEAR=y", "BR2_PACKAGE_IPERF3=y", "BR2_PACKAGE_ETHTOOL=y",
                       "BR2_PACKAGE_PCIUTILS=y", "BR2_PACKAGE_IPROUTE2=y"):
            self.assertIn(needle, cfg)
        post = (OVERLAY / "post-build.sh").read_text()
        self.assertIn("/usr/sbin/nodeos-console", post)
        self.assertIn("nvidia-payload.sha256", post)
        frag = (OVERLAY / "linux-nodeos.fragment").read_text()
        for needle in ("CONFIG_FRAMEBUFFER_CONSOLE=y", "CONFIG_FB_EFI=y", "CONFIG_VT_CONSOLE=y"):
            self.assertIn(needle, frag)
        mk = (ROOT / "buildroot/package/nodeos-agent/nodeos-agent.mk").read_text()
        for tool in (*TOOLS, "nodeos-lib.sh", "nodeos-evidence-export"):
            self.assertIn(tool, mk)

    def test_1070ti_profile_requires_the_direct_lan_peer(self):
        conf = (OVERLAY / "rootfs-overlay/etc/nodeos/nodeos.conf").read_text()
        for needle in ("NODEOS_REQUIRE_PEER=1", "NODEOS_MIN_LINK_MBIT=1000", "NODEOS_IPERF_SERVER=1",
                       "NODEOS_EXPECTED_CC=6.1", 'NODEOS_GPU_PROFILE="GTX 1070 Ti / Pascal / sm_61"'):
            self.assertIn(needle, conf)

    def test_contracts_are_valid_json_with_expected_ids(self):
        for name, schema_const in (
            ("nodeos-gate-report-v1.schema.json", "resonarch.nodeos.gate-report.v1"),
            ("nodeos-evidence-manifest-v1.schema.json", "resonarch.nodeos.evidence-manifest.v1"),
            ("node-capability-v1.schema.json", "resonarch.nodeos.capability.v1"),
        ):
            data = json.loads((ROOT / "contracts" / name).read_text())
            self.assertEqual(data["properties"]["schema"]["const"], schema_const)

    def test_state_enum_covers_every_state_the_agent_can_emit(self):
        cap = json.loads((ROOT / "contracts/node-capability-v1.schema.json").read_text())
        allowed = set(cap["properties"]["state"]["enum"])
        lib = (SRC / "nodeos-lib.sh").read_text()
        import re
        emitted = set(re.findall(r"echo ((?:BLOCKED_|DEGRADED_)[A-Z_]+|[A-Z_]+_READY|[A-Z_]+_PASS|[A-Z_]+_DISCOVERED|BOOTING|[A-Z_]+_VERIFIED)", lib))
        emitted.discard("UEFI_BOOT")
        self.assertTrue(emitted)
        self.assertLessEqual(emitted, allowed | {"NODE_READY"})

    def test_build_script_exports_identity_and_ssh_flag(self):
        build = (ROOT / "scripts/build-nodeos.sh").read_text()
        for needle in ("NODEOS_BUILD_COMMIT", "NODEOS_BUILDROOT_COMMIT", "NODEOS_BUILD_PROFILE", "--ssh-pubkey="):
            self.assertIn(needle, build)


if __name__ == "__main__":
    unittest.main()
