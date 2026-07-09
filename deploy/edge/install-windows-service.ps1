<#
.SYNOPSIS
    Installs the Cologic Shop Floor Tracker Edge_Agent as a Windows service
    using NSSM (the Non-Sucking Service Manager).

.DESCRIPTION
    Native Windows services cannot host a plain console program directly, so we
    wrap `python -m edge.agent` with NSSM. NSSM supervises the process and
    provides the auto-start-on-boot and auto-restart-on-failure behavior the
    Edge_Agent requires.

    Satisfies:
      - Req 12.1: service is configured Start=SERVICE_AUTO_START, so it launches
                  automatically when the on-site machine boots.
      - Req 12.2: NSSM's exit action is set to "Restart", so any unexpected
                  termination of the agent process restarts it automatically,
                  with a throttle to avoid crash loops.
      - Req 14.1: installable on a Windows on-site machine.

    Secrets and connection settings (INGEST_API_KEY, CLOUD_SERVER_BASE_URL,
    CAMERA_CONFIG_PATH, FERNET_KEY, ...) are read by the agent from the
    git-excluded .env file in the project root. This script does NOT bake
    secrets into the service definition; it only points the service at the
    project directory as its working directory so the agent loads .env itself.

.PARAMETER ProjectRoot
    Absolute path to the checked-out project root (where edge/ and .env live).
    Defaults to two directories above this script (...\deploy\edge\ -> project root).

.PARAMETER PythonExe
    Path to the Python interpreter that has the project dependencies installed.
    Defaults to "$ProjectRoot\.venv\Scripts\python.exe".

.PARAMETER NssmExe
    Path to nssm.exe. Defaults to "nssm" on PATH. Download from https://nssm.cc/.

.PARAMETER ServiceName
    Windows service name. Defaults to "CologicEdgeAgent".

.EXAMPLE
    # Run from an elevated (Administrator) PowerShell prompt:
    .\install-windows-service.ps1

.EXAMPLE
    .\install-windows-service.ps1 -ProjectRoot "C:\cologic-edge" -NssmExe "C:\tools\nssm\nssm.exe"

.NOTES
    Must be run as Administrator. See deploy/edge/README.md for details,
    including a native-Windows alternative if NSSM is not permitted.
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$PythonExe,
    [string]$NssmExe = "nssm",
    [string]$ServiceName = "CologicEdgeAgent"
)

$ErrorActionPreference = "Stop"

# --- Require elevation ------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent() `
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "This script must be run from an elevated (Administrator) PowerShell prompt."
}

# --- Resolve paths ----------------------------------------------------------
if (-not $PythonExe) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}

Write-Host "Project root : $ProjectRoot"
Write-Host "Python exe   : $PythonExe"
Write-Host "NSSM exe     : $NssmExe"
Write-Host "Service name : $ServiceName"

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}
if (-not (Test-Path $PythonExe)) {
    throw "Python interpreter not found: $PythonExe. Create a venv and install requirements, or pass -PythonExe."
}
$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning "No .env found at $envFile. The Edge_Agent reads INGEST_API_KEY, CLOUD_SERVER_BASE_URL, CAMERA_CONFIG_PATH, etc. from it. Create it before starting the service (see .env.example)."
}

# Ensure NSSM is available.
try {
    & $NssmExe version | Out-Null
} catch {
    throw "Could not run NSSM ('$NssmExe'). Download it from https://nssm.cc/ and pass -NssmExe with the full path to nssm.exe."
}

$logDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# --- Remove any prior install (idempotent) ----------------------------------
$existing = & $NssmExe status $ServiceName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Existing service '$ServiceName' found - stopping and removing it first."
    & $NssmExe stop $ServiceName | Out-Null
    & $NssmExe remove $ServiceName confirm | Out-Null
}

# --- Install the service ----------------------------------------------------
# NSSM runs:  <PythonExe> -m edge.agent   with AppDirectory = ProjectRoot,
# so the agent loads .env / camera_config.json relative to the project root.
Write-Host "Installing service '$ServiceName'..."
& $NssmExe install $ServiceName $PythonExe "-m" "edge.agent"
& $NssmExe set $ServiceName AppDirectory $ProjectRoot
& $NssmExe set $ServiceName DisplayName "Cologic Shop Floor Tracker - Edge Agent"
& $NssmExe set $ServiceName Description  "On-site CV compute + durable push to Cloud_Server. Reads secrets from .env in the project root."

# --- Auto-start on boot (Req 12.1) ------------------------------------------
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# --- Auto-restart on unexpected termination (Req 12.2) ----------------------
# AppExit Default Restart -> restart on ANY exit (expected or not); combined
# with the throttle below this yields resilient restart-on-failure behavior.
& $NssmExe set $ServiceName AppExit Default Restart
# Wait 5000 ms before restarting after a crash.
& $NssmExe set $ServiceName AppRestartDelay 5000
# Crash-loop guard: if the process runs for fewer than 60000 ms (60 s) before
# exiting, NSSM widens the restart delay via this throttle window instead of
# hammering restarts.
& $NssmExe set $ServiceName AppThrottle 60000

# --- Logging ----------------------------------------------------------------
& $NssmExe set $ServiceName AppStdout (Join-Path $logDir "edge-agent.out.log")
& $NssmExe set $ServiceName AppStderr (Join-Path $logDir "edge-agent.err.log")
# Rotate logs online when they exceed ~10 MB.
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateOnline 1
& $NssmExe set $ServiceName AppRotateBytes 10485760

# --- Graceful shutdown ------------------------------------------------------
# Give background loops (flusher, heartbeat, poller) time to drain on stop.
& $NssmExe set $ServiceName AppStopMethodConsole 15000

Write-Host ""
Write-Host "Service '$ServiceName' installed."
Write-Host "  Auto-start on boot : SERVICE_AUTO_START (Req 12.1)"
Write-Host "  Restart on failure : AppExit Default Restart, 5 s delay (Req 12.2)"
Write-Host ""
Write-Host "Start it now with:   nssm start $ServiceName"
Write-Host "Check status with:   nssm status $ServiceName"
Write-Host "Uninstall with:      nssm stop $ServiceName; nssm remove $ServiceName confirm"

# Start immediately so monitoring begins without waiting for a reboot.
Write-Host ""
Write-Host "Starting service..."
& $NssmExe start $ServiceName
& $NssmExe status $ServiceName
