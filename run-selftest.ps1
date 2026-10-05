# dbasim self-test for Windows: starts Oracle Database Free in Docker, installs dbasim,
# and proves every scenario breaks before the fix and passes after it.
# Output is saved to selftest-result.txt and selftest-dbasim.txt next to this script - send both back.
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
        Fail "ไม่พบ Docker ให้ติดตั้ง Docker Desktop จาก https://www.docker.com/products/docker-desktop/ (ต้องเปิด WSL2) แล้วรันไฟล์นี้ใหม่"
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker ติดตั้งแล้วแต่ยังไม่ทำงาน เปิด Docker Desktop รอจนขึ้นว่า Engine running แล้วรันไฟล์นี้ใหม่"
    }
    docker --version

    Write-Host "== 2/5 Python"
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Fail "ไม่พบ Python ติดตั้ง Python 3.9 ขึ้นไปจาก https://www.python.org/downloads/ (ติ๊ก Add to PATH) แล้วรันใหม่" }
    python --version
    if ($LASTEXITCODE -ne 0) { Fail "คำสั่ง python ใช้ไม่ได้ (อาจเป็นตัวลัดของ Microsoft Store) ติดตั้ง Python จาก python.org แล้วรันใหม่" }

    Write-Host "== 3/5 Oracle Database Free"
    $pwFile = Join-Path $here ".oracle-password"
    $exists = docker ps -a --filter "name=^dbasim-oracle$" --format "{{.Names}}"
    if (-not $exists) {
        $chars = [char[]]((48..57) + (65..90) + (97..122))
        $pw = "Db" + (-join (1..14 | ForEach-Object { $chars | Get-Random }))
        Set-Content -Path $pwFile -Value $pw -NoNewline
        Write-Host "กำลังดาวน์โหลดและสร้าง container (ครั้งแรกโหลดหลาย GB อาจใช้เวลานาน) ..."
        docker run -d --name dbasim-oracle -p 1521:1521 -e ORACLE_PWD=$pw container-registry.oracle.com/database/free:latest
        if ($LASTEXITCODE -ne 0) { Fail "สร้าง container ไม่สำเร็จ ดู error ด้านบน (เช่น port 1521 ถูกใช้อยู่)" }
    } else {
        if (-not (Test-Path $pwFile)) { Fail "มี container ชื่อ dbasim-oracle อยู่แล้วแต่ไม่พบไฟล์รหัสผ่าน .oracle-password ลบ container เก่า (docker rm -f dbasim-oracle) แล้วรันใหม่" }
        $pw = Get-Content $pwFile -Raw
        docker start dbasim-oracle | Out-Null
    }
    Write-Host "รอ DB พร้อมใช้ (สูงสุด 20 นาที) ..."
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        $logs = docker logs dbasim-oracle 2>&1 | Out-String
        if ($logs -match "DATABASE IS READY TO USE") { $ready = $true; break }
        Start-Sleep -Seconds 10
    }
    if (-not $ready) {
        docker logs --tail 40 dbasim-oracle 2>&1
        Fail "DB ยังไม่พร้อมภายใน 20 นาที ดู log ด้านบน"
    }
    Write-Host "DB พร้อมแล้ว"

    Write-Host "== 4/5 ติดตั้ง dbasim"
    if (-not (Test-Path ".venv")) { python -m venv .venv }
    .\.venv\Scripts\python -m pip install -q --upgrade pip
    .\.venv\Scripts\python -m pip install -q -e .
    if ($LASTEXITCODE -ne 0) { Fail "pip install ไม่สำเร็จ ดู error ด้านบน" }

    Write-Host "== 5/5 selftest (ประมาณ 10-20 นาที)"
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
    Write-Host "เสร็จแล้ว ส่งไฟล์ selftest-result.txt และ selftest-dbasim.txt กลับมาให้ Claude" -ForegroundColor Green
}
finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
