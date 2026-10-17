param(
    [switch]$SkipTests,
    [switch]$Installer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows.' }

$taskRoot = Split-Path -Parent $PSScriptRoot
$taskUvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
$taskUvPath = if ($taskUvCommand) { $taskUvCommand.Source } else {
    Join-Path $env:USERPROFILE '.local\bin\uv.exe'
}
if (-not (Test-Path -LiteralPath $taskUvPath)) { throw 'Install uv, then run this script again.' }

function Invoke-Uv {
    & $taskUvPath @args
    if ($LASTEXITCODE -ne 0) { throw "uv failed with exit code $LASTEXITCODE." }
}

$taskIsccPath = $null
if ($Installer) {
    $taskIsccCommand = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($taskIsccCommand) { $taskIsccPath = $taskIsccCommand.Source }
    foreach ($taskProgramFiles in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if (-not $taskIsccPath -and $taskProgramFiles) {
            $taskCandidate = Join-Path $taskProgramFiles 'Inno Setup 6\ISCC.exe'
            if (Test-Path -LiteralPath $taskCandidate) { $taskIsccPath = $taskCandidate }
        }
    }
    if (-not $taskIsccPath) { throw 'Inno Setup 6 is required for -Installer; install it or omit -Installer.' }
}

$taskPreviousCache = $env:UV_CACHE_DIR
$taskPreviousPython = $env:UV_PYTHON_INSTALL_DIR
$taskPreviousQt = $env:QT_QPA_PLATFORM
$taskPreviousArchitecture = $env:PROCESSOR_ARCHITECTURE
Push-Location -LiteralPath $taskRoot
try {
    $env:UV_CACHE_DIR = Join-Path $taskRoot 'build\uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot 'build\python'
    $taskPython = 'cpython-3.12-windows-x86_64-none'
    Invoke-Uv python install $taskPython --no-bin --no-registry
    Invoke-Uv sync --locked --python $taskPython

    $taskArchCheck = "import struct, sys, sysconfig; target=sysconfig.get_platform(); bits=struct.calcsize('P') * 8; print(f'Python target: {target}, {bits}-bit'); sys.exit(0 if target == 'win-amd64' and bits == 64 else 1)"
    Invoke-Uv run --locked --no-sync python -c $taskArchCheck
    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = 'offscreen'
        Invoke-Uv run --locked --no-sync pytest -q
        $env:QT_QPA_PLATFORM = $taskPreviousQt
    }
    Invoke-Uv run --locked --no-sync ruff check .

    # PyInstaller needs the interpreter architecture under Windows ARM emulation.
    $env:PROCESSOR_ARCHITECTURE = 'AMD64'
    Invoke-Uv run --locked --no-sync --with pyinstaller --with pillow pyinstaller packaging/nwologger.spec --noconfirm
    $taskExe = Join-Path $taskRoot 'dist\NWOlogger\NWOlogger.exe'
    $taskImage = [System.IO.File]::ReadAllBytes($taskExe)
    $taskPeOffset = [BitConverter]::ToInt32($taskImage, 0x3c)
    if ([BitConverter]::ToUInt32($taskImage, $taskPeOffset) -ne 0x00004550 -or
        [BitConverter]::ToUInt16($taskImage, $taskPeOffset + 4) -ne 0x8664) {
        throw 'Built executable is not Windows x64 (AMD64).'
    }
    $taskVersionCode = "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
    $taskVersion = (Invoke-Uv run --locked --no-sync python -c $taskVersionCode).Trim()
    $taskZip = Join-Path $taskRoot "dist\NWOlogger-$taskVersion-windows-x64.zip"
    Invoke-Uv run --locked --no-sync python -m zipfile -c $taskZip (Join-Path $taskRoot 'dist\NWOlogger')
    Write-Host "Windows x64 executable: $taskExe"
    Write-Host "Shareable ZIP: $taskZip"

    if ($Installer) {
        & $taskIsccPath "/DAppVersion=$taskVersion" (Join-Path $taskRoot 'packaging\nwologger.iss')
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE." }
        Write-Host "Installer: $(Join-Path $taskRoot "dist\NWOlogger-$taskVersion-windows-setup.exe")"
    }
}
finally {
    $env:UV_CACHE_DIR = $taskPreviousCache
    $env:UV_PYTHON_INSTALL_DIR = $taskPreviousPython
    $env:QT_QPA_PLATFORM = $taskPreviousQt
    $env:PROCESSOR_ARCHITECTURE = $taskPreviousArchitecture
    Pop-Location
}
