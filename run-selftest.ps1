# dbasim self-test for Windows: starts Oracle Database Free in Docker, installs dbasim,
# and proves every scenario breaks before the fix and passes after it.
# Output is saved to selftest-result.txt and selftest-dbasim.txt next to this script.
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$result = Join-Path $here "selftest-result.txt"
Start-Transcript -Path $result -Force | Out-Null

function Fail($msg) {
    Write-Host ""
    Write-Host "STOP: $msg" -ForegroundColor Red
    exit 1
}

try {
    Write-Host "== 1/5 Docker"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "Docker was not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ (WSL2 must be enabled), then run this file again"
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is installed but not running. Open Docker Desktop, wait for Engine running, then run this file again"
    }
    docker --version

    Write-Host "== 2/5 Python"
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Fail "Python was not found. Install Python 3.9 or newer from https://www.python.org/downloads/ (tick Add to PATH), then run this again" }
    python --version
    if ($LASTEXITCODE -ne 0) { Fail "The python command does not work (it may be the Microsoft Store shortcut). Install Python from python.org, then run this again" }

    Write-Host "== 3/5 Oracle Database Free"
    $pwFile = Join-Path $here ".oracle-password"
    $exists = docker ps -a --filter "name=^dbasim-oracle$" --format "{{.Names}}"
    if (-not $exists) {
        $chars = [char[]]((48..57) + (65..90) + (97..122))
        $pw = "Db" + (-join (1..14 | ForEach-Object { $chars | Get-Random }))
        Set-Content -Path $pwFile -Value $pw -NoNewline
        Write-Host "Downloading the image and creating the container (the first run downloads several GB and can take a while) ..."
        docker run -d --name dbasim-oracle -p 1521:1521 -e ORACLE_PWD=$pw container-registry.oracle.com/database/free:latest
        if ($LASTEXITCODE -ne 0) { Fail "Could not create the container. See the error above (for example, port 1521 is already in use)" }
    } else {
        if (-not (Test-Path $pwFile)) { Fail "A container named dbasim-oracle already exists but the password file .oracle-password is missing. Remove the old container (docker rm -f dbasim-oracle), then run this again" }
        $pw = Get-Content $pwFile -Raw
        docker start dbasim-oracle | Out-Null
    }
    Write-Host "Waiting for the database to be ready (up to 20 minutes) ..."
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        $logs = docker logs dbasim-oracle 2>&1 | Out-String
        if ($logs -match "DATABASE IS READY TO USE") { $ready = $true; break }
        Start-Sleep -Seconds 10
    }
    if (-not $ready) {
        docker logs --tail 40 dbasim-oracle 2>&1
        Fail "The database was not ready within 20 minutes. See the log above"
    }
    Write-Host "Database is ready"

    Write-Host "== 4/5 Install dbasim"
    if (-not (Test-Path ".venv")) { python -m venv .venv }
    .\.venv\Scripts\python -m pip install -q --upgrade pip
    .\.venv\Scripts\python -m pip install -q -e .
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed. See the error above" }

    Write-Host "== 5/5 selftest (about 10-20 minutes)"
    $env:DBASIM_ADMIN_PASSWORD = $pw
    $env:DBASIM_DSN = "localhost:1521/FREEPDB1"
    $env:DBASIM_SCALE = "0.3"
    $env:PYTHONIOENCODING = "utf-8"
    # Windows PowerShell 5.1 transcripts can miss native output, so keep dbasim's own copy
    $dbasimOut = Join-Path $here "selftest-dbasim.txt"
    & .\.venv\Scripts\dbasim doctor 2>&1 | Tee-Object -Variable doctorOut
    & .\.venv\Scripts\dbasim selftest 2>&1 | Tee-Object -Variable selftestOut
    ($doctorOut + $selftestOut) | Out-File -FilePath $dbasimOut -Encoding utf8
    Write-Host ""
    Write-Host "Done. Results are in selftest-result.txt and selftest-dbasim.txt" -ForegroundColor Green
}
finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
