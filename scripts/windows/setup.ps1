<#
  One-step installer for a restaurant branch server on Windows with Docker Desktop.

  What it does (safe to run again; existing passwords and data are never replaced):
    1. checks Docker Desktop is installed and running (starts it if needed);
    2. creates .env and the secret files (JWT secret, database password, backup passphrase);
    3. detects this computer's address on the restaurant network;
    4. builds and starts the system (database, app, sync worker, backups, HTTPS);
    5. waits until it is healthy;
    6. installs the local HTTPS certificate on this computer and opens the firewall for phones/tablets.

  Usage (from the repository folder):   double-click Setup-Windows.cmd
     or:  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 [-OffsitePath D:\RestaurantBackups]
#>
[CmdletBinding()]
param(
    [string]$OffsitePath = "",       # USB disk / network share for off-site backup copies
    [string]$ServerIp = "",          # override the detected network address
    [switch]$SkipCertificate,        # do not install the HTTPS certificate on this computer
    [switch]$SkipFirewall            # do not add the Windows Firewall rule
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host ""; Write-Host "SETUP STOPPED: $m" -ForegroundColor Red; Write-Host ""; exit 1 }

function New-RandomHex([int]$bytes) {
    $b = New-Object byte[] $bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b); $rng.Dispose()
    return -join ($b | ForEach-Object { $_.ToString('x2') })
}
# Files read inside Linux containers: UTF-8 without BOM, LF line endings.
function Write-UnixFile([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n"), $Utf8NoBom)
}
function Get-EnvValue([string]$key) {
    foreach ($line in [System.IO.File]::ReadAllLines((Join-Path $Root '.env'))) {
        if ($line -match "^\s*$([regex]::Escape($key))=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}
function Set-EnvValue([string]$key, [string]$value) {
    $path = Join-Path $Root '.env'
    $lines = [System.Collections.Generic.List[string]]([System.IO.File]::ReadAllLines($path))
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*#?\s*$([regex]::Escape($key))=") { $lines[$i] = "$key=$value"; $found = $true; break }
    }
    if (-not $found) { $lines.Add("$key=$value") }
    Write-UnixFile $path (($lines -join "`n") + "`n")
}
# Native commands run through cmd.exe: Windows PowerShell 5.1 turns their stderr output (normal
# progress messages from Docker) into errors when redirected.
function Test-Cmd([string]$line) { cmd /c "$line >NUL 2>&1"; return ($LASTEXITCODE -eq 0) }
function Get-CmdOutput([string]$line) { return (cmd /c "$line 2>NUL") }

Write-Host "Restaurant inventory system - Windows setup" -ForegroundColor White
Write-Host "Folder: $Root"
if (-not (Test-Path (Join-Path $Root 'docker-compose.yml'))) { Fail "docker-compose.yml not found. Run this from the repository folder." }

# -- 1. Docker Desktop -----------------------------------------------------------
Step "Checking Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again."
}
if (-not (Test-Cmd 'docker info')) {
    $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dd) { Warn "Docker Desktop is not running - starting it (this can take a minute)"; Start-Process $dd }
    $deadline = (Get-Date).AddMinutes(4)
    do { Start-Sleep -Seconds 5; $up = Test-Cmd 'docker info' } while (-not $up -and (Get-Date) -lt $deadline)
    if (-not $up) { Fail "Docker Desktop is not running. Open Docker Desktop, wait until it says 'Engine running', then run this again." }
}
if (-not (Test-Cmd 'docker compose version')) { Fail "'docker compose' is not available. Update Docker Desktop." }
$osType = "$(Get-CmdOutput 'docker info --format {{.OSType}}')".Trim()
if ($osType -ne 'linux') { Fail "Docker Desktop is in Windows-containers mode. Right-click the Docker icon and choose 'Switch to Linux containers'." }
Ok "Docker Desktop is running"

# -- 2. Configuration and secrets ------------------------------------------------
Step "Creating configuration and passwords (existing ones are kept)"
$envPath = Join-Path $Root '.env'
if (-not (Test-Path $envPath)) {
    Write-UnixFile $envPath ([System.IO.File]::ReadAllText((Join-Path $Root '.env.example')))
    Ok "created .env"
}
$jwt = Get-EnvValue 'APP_JWT_SECRET'
if (-not $jwt -or $jwt -like 'replace-*' -or $jwt.Length -lt 32) { Set-EnvValue 'APP_JWT_SECRET' (New-RandomHex 32); Ok "generated sign-in secret" }
$release = ([System.IO.File]::ReadAllText((Join-Path $Root 'VERSION'))).Trim()
Set-EnvValue 'KL_VERSION' $release
Ok "release $release"
if (-not (Get-EnvValue 'APP_METRICS_TOKEN')) { Set-EnvValue 'APP_METRICS_TOKEN' (New-RandomHex 24); Ok "generated monitoring token" }

$secrets = Join-Path $Root 'secrets'
New-Item -ItemType Directory -Force -Path $secrets | Out-Null
$dbPwFile = Join-Path $secrets 'db_password.txt'
$project = (Split-Path $Root -Leaf).ToLower() -replace '[^a-z0-9_-]', ''
$dataExists = Test-Cmd "docker volume inspect ${project}_dbdata"
if (-not (Test-Path $dbPwFile)) {
    if ($dataExists) { Fail "A database already exists but secrets\db_password.txt is missing. Restore that file from your safe copy." }
    Write-UnixFile $dbPwFile (New-RandomHex 24); Ok "generated database password"
}
$dbPw = ([System.IO.File]::ReadAllText($dbPwFile)).Trim()
Write-UnixFile (Join-Path $secrets 'pgpass') "db:5432:*:app:$dbPw`n"
$passFile = Join-Path $secrets 'backup_passphrase.txt'
$newPassphrase = $false
if (-not (Test-Path $passFile)) { Write-UnixFile $passFile (New-RandomHex 32); $newPassphrase = $true; Ok "generated backup passphrase" }

# -- 3. Network address and off-site folder --------------------------------------
Step "Detecting this computer's network address"
if (-not $ServerIp) {
    try {
        $cfg = Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch 'vEthernet|WSL|Docker|VirtualBox|VMware|Hyper-V' } |
            Select-Object -First 1
        if ($cfg) { $ServerIp = ($cfg.IPv4Address | Select-Object -First 1).IPAddress }
    } catch { }
}
if ($ServerIp) { Set-EnvValue 'SERVER_IP' $ServerIp; Ok "network address: $ServerIp" }
else { $ServerIp = '127.0.0.1'; Warn "no network connection found - only this computer will be able to open the app" }

if ($OffsitePath) {
    New-Item -ItemType Directory -Force -Path $OffsitePath | Out-Null
    Set-EnvValue 'OFFSITE_PATH' (((Resolve-Path $OffsitePath).Path) -replace '\\', '/')
    Ok "off-site backup copies go to $OffsitePath"
} elseif (-not (Get-EnvValue 'OFFSITE_PATH')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'offsite') | Out-Null
    Warn "off-site copies go to .\offsite (same disk). For real protection re-run with -OffsitePath <USB or network folder>"
}

# -- 4. Build and start ----------------------------------------------------------
Step "Building and starting the system (the first time takes 5-15 minutes)"
cmd /c "docker compose up -d --build"
if ($LASTEXITCODE -ne 0) { Fail "Docker could not build or start the system. The messages above say why; send them to support." }

# -- 5. Health -------------------------------------------------------------------
Step "Waiting for the system to be ready"
$ready = $false
$deadline = (Get-Date).AddMinutes(5)
while (-not $ready -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $code = & curl.exe -sk --ssl-no-revoke --noproxy localhost -o NUL -w "%{http_code}" https://localhost/api/v1/health/ready
    if ($code -eq '200') { $ready = $true }
}
if (-not $ready) {
    cmd /c "docker compose ps"
    cmd /c "docker compose logs --tail 40 app"
    Fail "The system did not become ready within 5 minutes (see the log above)."
}
$version = (& curl.exe -sk --ssl-no-revoke --noproxy localhost https://localhost/api/v1/health/live)
Ok "ready: $version"

# -- 6. HTTPS certificate and firewall -------------------------------------------
$certPath = Join-Path $Root 'restaurant-root.crt'
# relative target (the current folder is the repository): no quoting differences between PowerShell versions
if (Test-Cmd 'docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt restaurant-root.crt') {
    Ok "HTTPS certificate saved as restaurant-root.crt (install it once on each phone/tablet)"
    if (-not $SkipCertificate) {
        Step "Trusting the HTTPS certificate on this computer (Windows will ask you to confirm)"
        try { Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\CurrentUser\Root | Out-Null; Ok "certificate trusted" }
        catch { Warn "certificate not installed ($($_.Exception.Message)). The browser will show a warning you can accept." }
    }
} else { Warn "could not copy the HTTPS certificate yet; re-run setup later" }

if (-not $SkipFirewall) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        if (-not (Get-NetFirewallRule -DisplayName 'Restaurant inventory (HTTPS)' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName 'Restaurant inventory (HTTPS)' -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Private,Domain | Out-Null
        }
        Ok "firewall allows phones and tablets on the private network"
    } else {
        Warn "to let phones/tablets connect, run Setup-Windows.cmd once 'as administrator' (adds a firewall rule),"
        Warn "and make sure Windows treats the restaurant Wi-Fi as a Private network."
    }
}

# -- Done ------------------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host " The system is running." -ForegroundColor Green
Write-Host "   On this computer:        https://localhost/"
if ($ServerIp -ne '127.0.0.1') { Write-Host "   Phones/tablets (Wi-Fi):  https://$ServerIp/" }
Write-Host "   First visit: create the branch and the owner account on the setup screen."
Write-Host ""
Write-Host " Backups run every 6 hours (encrypted), with a restore test every day."
if ($newPassphrase) {
    Write-Host ""
    Write-Host " IMPORTANT: copy the backup passphrase somewhere OFF this computer" -ForegroundColor Yellow
    Write-Host " (password manager + a sealed paper copy). Without it backups cannot be restored:" -ForegroundColor Yellow
    Write-Host "   $passFile" -ForegroundColor Yellow
    Write-Host " Also keep a copy of the whole 'secrets' folder and the .env file." -ForegroundColor Yellow
}
Write-Host ""
Write-Host " Keep this computer on: Docker Desktop > Settings > General > 'Start Docker Desktop when you sign in',"
Write-Host " and turn off sleep in Windows power settings."
Write-Host "==========================================================================" -ForegroundColor Green
