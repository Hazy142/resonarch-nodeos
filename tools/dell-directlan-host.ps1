# dell-directlan-host.ps1 - Host Workstation direct-LAN helper for NODEOS-001-LAB
[CmdletBinding()]
param(
    [string]$TargetIp = "192.168.77.2",
    [string]$HostIp = "192.168.77.1",
    [int]$IperfPort = 5201,
    [string]$SshKeyPath = "$HOME\.ssh\id_ed25519",
    [string]$KnownHostsFile = "",
    [string]$StrictHostKeyChecking = "yes",
    [switch]$StartIperfServer,
    [switch]$FetchEvidence,
    [string]$ExpectedCc = "6.1",
    [string]$ExpectedProfile = "haswell-gtx1070ti",
    [string]$OutputDir = ".\evidence-downloads",
    [string]$SshCmd = "ssh",
    [string]$ScpCmd = "scp",
    [string]$PythonCmd = "python",
    [switch]$SkipNetworkProbe
)

$runId = [guid]::NewGuid().ToString().Substring(0,8)
$tempRoot = [System.IO.Path]::GetTempPath()
$sshOutPath = Join-Path $tempRoot "nodeos-ssh-$runId-out.tmp"
$sshErrPath = Join-Path $tempRoot "nodeos-ssh-$runId-err.tmp"
$exportOutPath = Join-Path $tempRoot "nodeos-export-$runId-out.tmp"
$exportErrPath = Join-Path $tempRoot "nodeos-export-$runId-err.tmp"
$scpOutPath = Join-Path $tempRoot "nodeos-scp-$runId-out.tmp"
$scpErrPath = Join-Path $tempRoot "nodeos-scp-$runId-err.tmp"
$verifyOutPath = Join-Path $tempRoot "nodeos-verify-$runId-out.tmp"
$verifyErrPath = Join-Path $tempRoot "nodeos-verify-$runId-err.tmp"

$ErrorActionPreference = "Stop"

function Write-Diag {
    param([string]$Message)
    [Console]::Error.WriteLine("[DIAG] $Message")
}

function Test-TcpListener {
    param([string]$Address, [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $asyncResult = $client.BeginConnect($Address, $Port, $null, $null)
        $waitSuccess = $asyncResult.AsyncWaitHandle.WaitOne(500, $false)
        if ($waitSuccess -and $client.Connected) {
            $client.EndConnect($asyncResult)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

# Resolve verifier script relative to script directory
$scriptDir = $PSScriptRoot
if (!$scriptDir) { $scriptDir = Get-Location }
$verifierPath = Join-Path $scriptDir "verify-evidence.py"

Write-Diag "=== NodeOS Direct-LAN Host Orchestrator ==="
Write-Diag "Target IP: $TargetIp"
Write-Diag "Host IP:   $HostIp"

# 0. Prerequisite Binary & File Checks
if ($FetchEvidence -or $StartIperfServer) {
    if (!(Get-Command $SshCmd -ErrorAction SilentlyContinue) -and !(Test-Path $SshCmd)) {
        Write-Diag "ERROR: Required SSH binary '$SshCmd' not found."
        exit 1
    }
    if ($FetchEvidence) {
        if (!(Get-Command $ScpCmd -ErrorAction SilentlyContinue) -and !(Test-Path $ScpCmd)) {
            Write-Diag "ERROR: Required SCP binary '$ScpCmd' not found."
            exit 1
        }
        if (!(Get-Command $PythonCmd -ErrorAction SilentlyContinue) -and !(Test-Path $PythonCmd)) {
            Write-Diag "ERROR: Required Python binary '$PythonCmd' not found."
            exit 1
        }
        if (!(Test-Path $verifierPath)) {
            Write-Diag "ERROR: Verifier script not found at '$verifierPath'."
            exit 1
        }
        if (!(Test-Path $SshKeyPath)) {
            Write-Diag "ERROR: SSH key not found at '$SshKeyPath'."
            exit 1
        }
    }
}

if (-not $SkipNetworkProbe) {
    # 1. ICMP Ping check
    Write-Diag "[1/4] Checking ICMP reachability to $TargetIp..."
    $ping = Test-Connection -ComputerName $TargetIp -Count 2 -Quiet -ErrorAction SilentlyContinue
    if ($ping) {
        Write-Diag "ICMP reachability OK."
    } else {
        Write-Diag "WARNING: Target $TargetIp is not responding to ICMP ping."
    }

    # 2. iperf3 server listener check
    Write-Diag "[2/4] Checking local iperf3 listener on ${HostIp}:${IperfPort}..."
    $listenerActive = Test-TcpListener -Address $HostIp -Port $IperfPort
    if ($listenerActive) {
        Write-Diag "iperf3 TCP listener active on ${HostIp}:${IperfPort}."
    } else {
        if ($StartIperfServer) {
            Write-Diag "Starting iperf3 server bound to ${HostIp}:${IperfPort}..."
            if (!(Get-Command "iperf3" -ErrorAction SilentlyContinue)) {
                Write-Diag "ERROR: 'iperf3' executable not found on host."
                exit 1
            }
            Start-Process -FilePath "iperf3" -ArgumentList "-s", "-B", $HostIp, "-p", $IperfPort -WindowStyle Hidden
            # Poll for readiness up to 5 seconds
            $ready = $false
            for ($i = 0; $i -lt 10; $i++) {
                Start-Sleep -Milliseconds 500
                if (Test-TcpListener -Address $HostIp -Port $IperfPort) {
                    $ready = $true
                    break
                }
            }
            if ($ready) {
                Write-Diag "iperf3 server started and listening on ${HostIp}:${IperfPort}."
            } else {
                Write-Diag "ERROR: iperf3 server failed to bind/listen on ${HostIp}:${IperfPort} within 5 seconds."
                exit 1
            }
        } else {
            Write-Diag "WARNING: No iperf3 listener active on ${HostIp}:${IperfPort}."
        }
    }
} else {
    Write-Diag "[1/4] Skipping ICMP ping check (-SkipNetworkProbe)."
    Write-Diag "[2/4] Skipping iperf3 listener check (-SkipNetworkProbe)."
}

# Common SSH options array
$sshOpts = @("-i", $SshKeyPath, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=$StrictHostKeyChecking", "-o", "ConnectTimeout=5")
if ($KnownHostsFile) {
    $sshOpts += @("-o", "UserKnownHostsFile=$KnownHostsFile")
}

# 3. Direct-LAN SSH console check
Write-Diag "[3/4] Checking direct-LAN SSH reachability..."
if (Test-Path $SshKeyPath) {
    $sshArgs = $sshOpts + @("root@$TargetIp", "nodeos-console", "--once")
    & $SshCmd $sshArgs > $sshOutPath 2> $sshErrPath
    $sshCode = $LASTEXITCODE
    if ($sshCode -eq 0) {
        Write-Diag "SSH console probe succeeded."
        $out = Get-Content $sshOutPath -Raw -ErrorAction SilentlyContinue
        Write-Diag "--- NodeOS Target Console Frame ---"
        Write-Diag $out
    } else {
        $err = Get-Content $sshErrPath -Raw -ErrorAction SilentlyContinue
        Write-Diag "ERROR: SSH console command failed with exit code ${sshCode}: $err"
        if (!$FetchEvidence) {
            exit $sshCode
        }
    }
}

# 4. Evidence Download & Verification
if ($FetchEvidence) {
    Write-Diag "[4/4] Exporting and fetching NodeOS evidence bundle..."

    # Run evidence export on target
    $exportArgs = $sshOpts + @("root@$TargetIp", "nodeos-evidence", "export")
    & $SshCmd $exportArgs > $exportOutPath 2> $exportErrPath
    $expCode = $LASTEXITCODE
    $expOut = Get-Content $exportOutPath -Raw -ErrorAction SilentlyContinue
    $expErr = Get-Content $exportErrPath -Raw -ErrorAction SilentlyContinue

    if ($expCode -ne 0) {
        Write-Diag "ERROR: nodeos-evidence export failed with exit code ${expCode}: $expErr"
        exit $expCode
    }

    # Parse EXPORT_BUNDLE and EXPORT_EVIDENCE_ID
    $remoteBundles = @()
    $expectedEvIds = @()
    foreach ($line in ($expOut -split "`r?`n")) {
        if ($line -match "^EXPORT_BUNDLE=(.+)$") {
            $remoteBundles += $matches[1].Trim()
        }
        if ($line -match "^EXPORT_EVIDENCE_ID=(.+)$") {
            $expectedEvIds += $matches[1].Trim()
        }
    }

    if ($remoteBundles.Count -eq 0) {
        # Fallback parsing for bundle: <path>
        foreach ($line in ($expOut -split "`r?`n")) {
            if ($line -match "^bundle:\s*(.+)$") {
                $remoteBundles += $matches[1].Trim()
            }
        }
    }

    if ($remoteBundles.Count -ne 1) {
        Write-Diag "ERROR: Expected exactly 1 remote export bundle path from export output, found $($remoteBundles.Count): $expOut"
        exit 1
    }
    $remoteBundle = $remoteBundles[0]

    if ($expectedEvIds.Count -gt 1) {
        Write-Diag "ERROR: Found multiple EXPORT_EVIDENCE_ID entries. Refusing ambiguous export."
        exit 1
    }
    $expectedEvId = if ($expectedEvIds.Count -eq 1) { $expectedEvIds[0] } else { "" }

    # Validate remote path to prevent shell injection / ambiguous downloads
    if ($remoteBundle -match "[;&|<>`$]") {
        Write-Diag "ERROR: Remote bundle path contains invalid characters: $remoteBundle"
        exit 1
    }
    if ($remoteBundle -notmatch "^/run/nodeos/nodeos-evidence-[a-f0-9]+\.tar$" -and $remoteBundle -notmatch "^/var/lib/nodeos/evidence/.*\.tar$") {
        Write-Diag "ERROR: Remote bundle path does not match expected pattern: $remoteBundle"
        exit 1
    }

    # Create brand-new, isolated local download subdirectory
    $timestamp = (Get-Date).ToString("yyyyMMdd-HHmmss-fff")
    $runSubdir = Join-Path $OutputDir "download-$timestamp-$runId"
    New-Item -ItemType Directory -Path $runSubdir | Out-Null

    Write-Diag "Fetching remote bundle '$remoteBundle' into isolated directory '$runSubdir'..."
    $scpArgs = $sshOpts + @("root@${TargetIp}:${remoteBundle}", "$runSubdir\")
    & $ScpCmd $scpArgs > $scpOutPath 2> $scpErrPath
    $scpCode = $LASTEXITCODE
    if ($scpCode -ne 0) {
        $scpErr = Get-Content $scpErrPath -Raw -ErrorAction SilentlyContinue
        Write-Diag "ERROR: SCP transfer failed with exit code ${scpCode}: $scpErr"
        exit $scpCode
    }

    # Find downloaded file in the isolated subfolder
    $downloadedFiles = @(Get-ChildItem -Path $runSubdir -Filter "*.tar")
    if ($downloadedFiles.Count -ne 1) {
        Write-Diag "ERROR: Expected exactly 1 tarball in $runSubdir, found $($downloadedFiles.Count)."
        exit 1
    }

    $tarFile = $downloadedFiles[0]
    if ($tarFile.Length -eq 0) {
        Write-Diag "ERROR: Downloaded evidence tarball is 0 bytes: $($tarFile.FullName)"
        exit 1
    }

    Write-Diag "Running host verifier (verify-evidence.py) on $($tarFile.FullName)..."
    $pyArgs = @($verifierPath, $tarFile.FullName)
    if ($ExpectedCc) { $pyArgs += @("--expected-cc", $ExpectedCc) }
    if ($ExpectedProfile) { $pyArgs += @("--expected-profile", $ExpectedProfile) }

    & $PythonCmd $pyArgs > $verifyOutPath 2> $verifyErrPath
    $pyCode = $LASTEXITCODE
    $pyOut = Get-Content $verifyOutPath -Raw -ErrorAction SilentlyContinue
    $pyErr = Get-Content $verifyErrPath -Raw -ErrorAction SilentlyContinue

    Write-Diag $pyOut
    if ($pyCode -ne 0) {
        Write-Diag "ERROR: Host verifier rejected evidence bundle with exit code ${pyCode}."
        exit $pyCode
    }

    # Extract verified Evidence ID from output and bind strictly
    $verifiedEvIds = @()
    foreach ($line in ($pyOut -split "`r?`n")) {
        if ($line -match "^Evidence ID:\s*([a-f0-9]{64})$") {
            $verifiedEvIds += $matches[1].Trim()
        }
    }

    if ($verifiedEvIds.Count -ne 1) {
        Write-Diag "ERROR: Verifier output must contain exactly one Evidence ID, found $($verifiedEvIds.Count)."
        exit 1
    }
    $verifiedEvId = $verifiedEvIds[0]

    if (!$expectedEvId) {
        Write-Diag "ERROR: Export output did not provide EXPORT_EVIDENCE_ID to bind against."
        exit 1
    }

    if ($expectedEvId -ne $verifiedEvId) {
        Write-Diag "ERROR: Evidence ID mismatch: export claimed '$expectedEvId', verified bundle is '$verifiedEvId'."
        exit 1
    }

    Write-Diag "Evidence verification SUCCESSFUL! ID: $verifiedEvId"
    # Print clean machine-readable success line on stdout
    Write-Output "SUCCESS EVIDENCE_ID=$verifiedEvId FILE=$($tarFile.FullName)"
}

exit 0
