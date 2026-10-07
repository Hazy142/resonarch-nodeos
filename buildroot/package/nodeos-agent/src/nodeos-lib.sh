#!/bin/sh
# resonArch NodeOS shared shell library (POSIX sh / BusyBox ash). Source only.
#
# All filesystem roots are overridable so the exact same code runs on the
# target and inside the CI test harness against a fake /sys, /proc and PATH.

NODEOS_RUN=${NODEOS_RUN:-/run/nodeos}
NODEOS_SYSFS=${NODEOS_SYSFS:-/sys}
NODEOS_PROCFS=${NODEOS_PROCFS:-/proc}
NODEOS_ETC=${NODEOS_ETC:-/etc/nodeos}
NODEOS_LOG=${NODEOS_LOG:-/var/log/nodeos-agent.log}
NODEOS_BIN=${NODEOS_BIN:-/usr/sbin}

# Ordered gate chain that drives the state machine (uefi is informational).
NODEOS_CHAIN="kernel nic link gpu payload driver cuda network"
NODEOS_ALL_GATES="uefi $NODEOS_CHAIN"

nodeos_load_conf() {
    if [ -r "$NODEOS_ETC/nodeos.conf" ]; then . "$NODEOS_ETC/nodeos.conf"; fi
    if [ -r "$NODEOS_ETC/build-info" ]; then . "$NODEOS_ETC/build-info"; fi
    return 0
}

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/[[:cntrl:]]/ /g'; }

# Valid JSON number or null.
json_num() {
    case "$1" in
        ''|*[!0-9.]*|*.*.*|.*|*.) printf 'null' ;;
        *) printf '%s' "$1" ;;
    esac
}

# true / false / null from "1"/"0"/other.
json_bool() {
    case "$1" in
        1|true|PASS) printf 'true' ;;
        0|false|FAIL) printf 'false' ;;
        *) printf 'null' ;;
    esac
}

# Shell-quote a value for a sourceable .env file.
sq() {
    _sqv=$(printf '%s' "$1" | tr -d '\r\n' | sed "s/'/'\\\\''/g")
    printf "'%s'" "$_sqv"
}

# Read a (possibly failing) sysfs/procfs file; empty output on any error.
rd() { [ -r "$1" ] && cat "$1" 2>/dev/null || true; }

# json_get FILE KEY -> first value of KEY (one key per line or flat object).
json_get() {
    [ -r "$1" ] || return 0
    sed -n 's/.*"'"$2"'":"*\([^",}]*\).*/\1/p' "$1" | head -n 1
}

nlog() {
    mkdir -p "$(dirname "$NODEOS_LOG")" 2>/dev/null || true
    printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >>"$NODEOS_LOG" 2>/dev/null || true
}

# Keep the volatile log bounded (tmpfs/rootfs friendly).
nodeos_log_trim() {
    [ -f "$NODEOS_LOG" ] || return 0
    _sz=$(wc -c <"$NODEOS_LOG" 2>/dev/null || echo 0)
    if [ "${_sz:-0}" -gt 524288 ]; then
        tail -n 500 "$NODEOS_LOG" >"$NODEOS_LOG.trim" 2>/dev/null &&
            mv "$NODEOS_LOG.trim" "$NODEOS_LOG" || true
    fi
}

# Atomic write helper: nodeos_publish TMPFILE FINALFILE
nodeos_publish() { mv -f "$1" "$2"; }

nodeos_find_iface() {
    _net="$NODEOS_SYSFS/class/net"
    if [ -n "${NODEOS_INTERFACE:-}" ] && [ -d "$_net/$NODEOS_INTERFACE" ]; then
        printf '%s\n' "$NODEOS_INTERFACE"; return 0
    fi
    for _p in "$_net"/en* "$_net"/eth*; do
        [ -d "$_p" ] || continue
        printf '%s\n' "$(basename "$_p")"; return 0
    done
    for _p in "$_net"/*; do
        [ -d "$_p" ] || continue
        _n=$(basename "$_p"); [ "$_n" = lo ] && continue
        [ -e "$_p/device" ] || continue
        printf '%s\n' "$_n"; return 0
    done
    return 1
}

# Fills NET_* with one live snapshot of the fabric NIC.
nodeos_net_collect() {
    NET_IFACE=""; NET_IPV4=""; NET_MAC=""; NET_DRIVER=""; NET_SPEED=""
    NET_DUPLEX=""; NET_CARRIER=""; NET_OPERSTATE=""; NET_MTU=""
    NET_RX_BYTES=""; NET_TX_BYTES=""; NET_RX_PACKETS=""; NET_TX_PACKETS=""
    NET_RX_ERRORS=""; NET_TX_ERRORS=""; NET_RX_DROPPED=""; NET_TX_DROPPED=""
    NET_IFACE=$(nodeos_find_iface 2>/dev/null || true)
    [ -n "$NET_IFACE" ] || return 0
    _d="$NODEOS_SYSFS/class/net/$NET_IFACE"
    NET_IPV4=$(ip -o -4 addr show dev "$NET_IFACE" 2>/dev/null | awk '{print $4; exit}' || true)
    NET_MAC=$(rd "$_d/address")
    NET_DRIVER=""
    if [ -L "$_d/device/driver" ]; then NET_DRIVER=$(basename "$(readlink "$_d/device/driver")"); fi
    NET_SPEED=$(rd "$_d/speed")
    case "$NET_SPEED" in -*) NET_SPEED="" ;; esac
    NET_DUPLEX=$(rd "$_d/duplex")
    case "$NET_DUPLEX" in unknown) NET_DUPLEX="" ;; esac
    NET_CARRIER=$(rd "$_d/carrier")
    NET_OPERSTATE=$(rd "$_d/operstate")
    NET_MTU=$(rd "$_d/mtu")
    NET_RX_BYTES=$(rd "$_d/statistics/rx_bytes")
    NET_TX_BYTES=$(rd "$_d/statistics/tx_bytes")
    NET_RX_PACKETS=$(rd "$_d/statistics/rx_packets")
    NET_TX_PACKETS=$(rd "$_d/statistics/tx_packets")
    NET_RX_ERRORS=$(rd "$_d/statistics/rx_errors")
    NET_TX_ERRORS=$(rd "$_d/statistics/tx_errors")
    NET_RX_DROPPED=$(rd "$_d/statistics/rx_dropped")
    NET_TX_DROPPED=$(rd "$_d/statistics/tx_dropped")
    return 0
}

nodeos_stage_of() {
    case "$1" in
        uefi) echo UEFI_BOOT ;;
        kernel) echo KERNEL_READY ;;
        nic) echo NIC_DISCOVERED ;;
        link) echo LINK_READY ;;
        gpu) echo GPU_DISCOVERED ;;
        payload) echo NVIDIA_PAYLOAD_VERIFIED ;;
        driver) echo DRIVER_READY ;;
        cuda) echo CUDA_PROBE_PASS ;;
        network) echo NODE_READY ;;
        *) echo BOOTING ;;
    esac
}

# nodeos_blocked_of GATE REASON -> failure state name
nodeos_blocked_of() {
    case "$1" in
        kernel) echo BOOTING ;;
        nic) echo BLOCKED_NO_NIC ;;
        link) case "$2" in no-ipv4) echo BLOCKED_NO_IP ;; *) echo BLOCKED_NO_CARRIER ;; esac ;;
        gpu) echo BLOCKED_GPU_MISSING ;;
        payload|driver) echo BLOCKED_DRIVER ;;
        cuda) echo BLOCKED_CUDA ;;
        network) echo DEGRADED_LINK ;;
        *) echo BOOTING ;;
    esac
}

# Human hint for a reason code (used by the console diagnostics).
nodeos_hint() {
    case "$1" in
        no-nic) echo "No Ethernet device: check kernel NIC driver (r8169/e1000e/igb/igc); dmesg | grep -i eth" ;;
        no-carrier) echo "No carrier: check the direct LAN cable and the workstation adapter" ;;
        no-ipv4) echo "NIC up but no IPv4 address: check S20nodeos-net and nodeos.conf" ;;
        gpu-missing) echo "No NVIDIA PCI function (10de:*): reseat card, PEG power, UEFI primary display" ;;
        payload-missing) echo "Image built without vendor/nvidia/payload: rebuild with a verified payload" ;;
        payload-digests-missing) echo "Payload digest list missing in image: rebuild (post-build.sh)" ;;
        payload-digest-mismatch) echo "Vendor payload files differ from manifest: image corrupted or tampered" ;;
        nvidia-smi-missing) echo "nvidia-smi not in image: the NVIDIA payload did not install userspace" ;;
        driver-not-ready) echo "Driver not loaded: dmesg | grep -i -e nvrm -e nvidia; Secure Boot must be off" ;;
        cuda-smoke-missing|cuda-probe-missing) echo "CUDA probe binary missing: payload must ship /usr/libexec/nodeos-cuda-probe" ;;
        cuda-probe-failed|cuda-probe-nonpass) echo "sm_61 probe failed: see cuda-smoke.json stage and dmesg for NVRM/Xid" ;;
        cuda-capability-mismatch) echo "GPU compute capability differs from NODEOS_EXPECTED_CC" ;;
        peer-unreachable) echo "Peer does not answer: set the workstation to 192.168.77.1/30, allow ICMP" ;;
        ping-loss) echo "Packet loss on the direct link: cable/port/duplex problem" ;;
        link-speed-low|link-speed-unknown) echo "Link below required speed: check cable (Cat5e+) and both ports" ;;
        link-duplex) echo "Link is not full duplex: renegotiate or replace cable" ;;
        link-errors) echo "NIC reports RX/TX errors: ethtool -S <if>; replace cable" ;;
        throughput-low) echo "iperf3 throughput below threshold: check duplex, cable, CPU governor" ;;
        iperf-unavailable) echo "No iperf3 server on the peer: run 'iperf3 -s' on the workstation" ;;
        not-run) echo "Network gate has not run yet" ;;
        *) echo "" ;;
    esac
}
