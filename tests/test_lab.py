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

    def test_compact_layout_rendering(self):
        lab = self.lab()
        lab.agent()
        proc = subprocess.run([SH, "-c", f'. "{SRC / "nodeos-lib.sh"}"; . "{SRC / "nodeos-console"}"; render_compact'],
                              env=lab.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        frame = proc.stdout
        self.assertIn("(Compact)", frame)
        self.assertIn("STATE NODE_READY", frame)
        widths = {len(line) for line in frame.splitlines() if line.startswith(("|", "+"))}
        self.assertEqual(widths, {80})


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
