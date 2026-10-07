# dell-directlan-host.ps1 - Host Workstation direct-LAN helper for NODEOS-001-LAB
[CmdletBinding()]
param(
    [string]$TargetIp = "192.168.77.2",
    [string]$HostIp = "192.168.77.1",
    [string]$SshKeyPath = "$HOME\.ssh\id_ed25519",
    [switch]$StartIperfServer,
    [switch]$FetchEvidence,
    [string]$OutputDir = ".\evidence-downloads"
)

$ErrorActionPreference = "Stop"

Write-Host "=== NodeOS Direct-LAN Host Orchestrator ===" -ForegroundColor Cyan
Write-Host "Target IP: $TargetIp"
Write-Host "Host IP:   $HostIp"

# 1. ICMP Ping check
Write-Host "`n[1/4] Checking ICMP reachability to $TargetIp..." -NoNewline
$ping = Test-Connection -ComputerName $TargetIp -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($ping) {
    Write-Host " [OK]" -ForegroundColor Green
} else {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Warning "Target $TargetIp is not responding to ICMP ping. Verify cable connection & NodeOS link gate."
}

# 2. iperf3 server check
Write-Host "`n[2/4] Checking local iperf3 server on $HostIp..."
$iperfProc = Get-Process -Name "iperf3" -ErrorAction SilentlyContinue
if ($iperfProc) {
    Write-Host "iperf3 server is running (PID: $($iperfProc.Id))." -ForegroundColor Green
} else {
    if ($StartIperfServer) {
        Write-Host "Starting iperf3 -s background process..." -ForegroundColor Yellow
        Start-Process -FilePath "iperf3" -ArgumentList "-s" -WindowStyle Hidden
        Write-Host "iperf3 server started." -ForegroundColor Green
    } else {
        Write-Host "iperf3 server is NOT running. Run with -StartIperfServer or start 'iperf3 -s' manually." -ForegroundColor Yellow
    }
}

# 3. SSH Connectivity check
Write-Host "`n[3/4] Checking direct-LAN SSH reachability..."
if (Test-Path $SshKeyPath) {
    Write-Host "Executing: ssh root@$TargetIp ..." -ForegroundColor Gray
    try {
        $sshOutput = & ssh -i "$SshKeyPath" -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@$TargetIp" "nodeos-console --once" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "SSH connection SUCCESSFUL!" -ForegroundColor Green
            Write-Host "`n--- NodeOS Target Console Frame ---" -ForegroundColor DarkGray
            Write-Host $sshOutput
            Write-Host "------------------------------------`n" -ForegroundColor DarkGray
        } else {
            Write-Host "SSH command returned exit code $LASTEXITCODE" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "SSH connection failed: $_" -ForegroundColor Red
    }
} else {
    Write-Host "SSH key not found at $SshKeyPath. Skipping SSH probe." -ForegroundColor Yellow
}

# 4. Evidence Download & Verification
if ($FetchEvidence) {
    Write-Host "`n[4/4] Exporting and fetching NodeOS evidence bundle..."
    if (!(Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }
    
    Write-Host "Triggering evidence export on target..." -ForegroundColor Gray
    $expOut = & ssh -i "$SshKeyPath" -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@$TargetIp" "nodeos-evidence export" 2>&1
    
    Write-Host "Fetching evidence tarball via SCP..." -ForegroundColor Gray
    & scp -i "$SshKeyPath" -o StrictHostKeyChecking=no "root@${TargetIp}:/run/nodeos/nodeos-evidence-*.tar" "$OutputDir\"
    
    $downloaded = Get-ChildItem -Path $OutputDir -Filter "nodeos-evidence-*.tar" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($downloaded) {
        Write-Host "Downloaded: $($downloaded.FullName)" -ForegroundColor Green
        Write-Host "Running host verification tool (verify-evidence.py)..." -ForegroundColor Cyan
        python tools/verify-evidence.py "$($downloaded.FullName)"
    } else {
        Write-Host "No evidence tarball downloaded." -ForegroundColor Red
    }
}

Write-Host "`nOrchestration check complete." -ForegroundColor Cyan
