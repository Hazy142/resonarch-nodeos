# DELL-USB-001 — Dell G15 5530 USB-first headless laboratory profile

**Scope:** Isolated, no-flash preflight and image-build candidate. This branch is **not** a boot-tested release. NEVER deploy unsigned EFI images while Dell Secure Boot is enabled.

## Inspected physical hardware (2026-10-04)
- Dell G15 5530, 16 GB RAM, RTX 4060 Laptop GPU (sm_89), NVIDIA Windows driver 610.88.
- UEFI Secure Boot **enabled**, BIOS 1.34.0.
- Realtek PCIe GbE Ethernet (currently disconnected), Intel AX201 Wi-Fi (not configured in the base image).
- Internal 512 GB NVMe: system disk 0; keep all internal partitions and Windows Boot Manager untouched.
- Two distinct generic USB devices of about 16 GB each: disk 1 holds the **DOKUMENTE** volume (E:); disk 2 holds F: (NTFS). Disk numbers change with plugging/unplugging. Neither was erased.
- Windows WSL2 engine present but **no Linux distro** installed; C: and D: have little free space. Prefer reproducible GitHub Actions for the first full image build.

## Dell headless profile
Use Buildroot 2026.08/Linux 6.12.47 and x86_64 UEFI/GRUB2 under a separate Dell defconfig. The inherited Ethernet-ready kernel includes R8169. The Dell board overlay selects wired DHCP with direct-LAN fallback 192.168.77.2/30, expects sm_89 for READY, and sets a distinct hardware profile. The legacy Haswell/GTX1070Ti profile stays independently configurable.

The Dell profile enables Dropbear but removes Buildroot's stock init script. SSH starts *only* if an individually provisioned, syntactically valid **ssh-ed25519 public key** was supplied to the build via the NODEOS_SSH_PUBKEY environment variable. Password authentication is disabled. Images without a key must fail closed: no remotely accessible console. Do not publish reusable images with a universal access key.

## Non-destructive USB selection
Windows PowerShell:
\`\`\`powershell
.\tools\dell-usb-preflight.ps1
.\tools\dell-usb-preflight.ps1 -DiskNumber 2 -ExpectedPnpId 'EXACT_ID_FROM_CURRENT_INVENTORY'
\`\`\`
**Read-only**: returns disk number, exact PNP device identifier, partitions/volume labels and a risk flag. DOKUMENTE-labelled media are never candidates. No automatic choice of a generic VendorCo disk, no image flashing and no persistent UEFI/NVRAM changes. The owner must explicitly authorize erasing a particular disk after reviewing its current files.

## Build and physical boot dependencies
On a Linux builder with sufficient disk space:
\`\`\`bash
./scripts/build-nodeos.sh --configure-only --profile=dell-g15-5530
NODEOS_SSH_PUBKEY=/absolute/path/unique-dell-pilot.pub ./scripts/build-nodeos.sh --profile=dell-g15-5530
\`\`\`
A full, reproducible Buildroot build requires a capable Linux build host or CI runner; the configure-only gate alone does not produce a boot image. The optional NVIDIA vendor payload must match compute capability 8.9 and profile dell-g15-5530-v1, pass manifest SHA-256 checks, and match the exact Linux kernel ABI; the repository does not ship proprietary CUDA binaries.

**Secure Boot blocker:** the existing upstream EFI image is not signed for this Dell. Before a physical USB boot either perform a reviewed one-time owner-controlled Secure Boot key enrollment and sign every EFI-stage binary and required NVIDIA modules, or prepare another compliant vendor-signed shim chain. We do **not** remotely disable Secure Boot. Without that chain the lab image must be marked BOOT_BLOCKED. No guarantee of automatically overriding firmware boot priority.

**Power-on/network blocker:** Ethernet is currently unplugged. To monitor this Dell while it runs NodeOS rather than Windows, an independent console is necessary (Android SSH client on the same routed Wi-Fi/LAN, a second computer, or approved out-of-band hardware). A laptop cannot be its own Windows controller during the USB boot. For Wi-Fi phone access, connect the Dell *Ethernet* to a router on the phone's network (DHCP); a direct isolated Ethernet cable requires the controlling computer on 192.168.77.1/30. The owner must import the matching SSH private key into an authorized client without committing or sharing it.

## Gate sequence
1. Dell image defconfig, shell syntax, schema and preflight tests PASS on CI.
2. Full signed image, pinned NVIDIA payload and sm_89 probe verified before touching media.
3. Explicit disk selection, file backup confirmation, offline image hash and controlled USB imaging.
4. Owner-prepared USB-first UEFI configuration, verified removable boot and recovery without modifying NVMe partitions.
5. Independent controller captures first network-reachable boot logs, SSH fingerprint, capability.json and sm_89 READY. Loss of NIC, invalid key or unsupported CUDA must remain fail-closed.
6. Next: A/B immutable rootfs, attestable enrollment, mTLS, FiberTunnel/APE production admission. None of these are claimed by the first USB lab image.
