# Create the NeuroNav runtime in .venv next to this script and install requirements.
#
# Usage (PowerShell, in this folder):
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python C:\path\to\python.exe
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Recreate
#
# Requires Windows x64 Python 3.11-3.13 (PySide6 6.8). The Motive API service
# runs with the base interpreter of this venv, so keep that interpreter installed.
param(
    [string]$Python = '',
    [switch]$Recreate
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root '.venv'

function Test-Python([string]$exe) {
    if (-not $exe) { return $false }
    try {
        $version = & $exe -c "import sys, struct; print('%d.%d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8))" 2>$null
    } catch { return $false }
    if (-not $version) { return $false }
    $parts = $version.Trim().Split(' ')
    $minor = [int]($parts[0].Split('.')[1])
    return ($parts[0].StartsWith('3.') -and $minor -ge 11 -and $minor -le 13 -and $parts[1] -eq '64')
}

if (-not $Python) {
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in '3.11', '3.12', '3.13') {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($exe) { $candidates += $exe.Trim() }
        }
    }
    foreach ($base in "$env:USERPROFILE\anaconda3", "$env:USERPROFILE\miniconda3", "$env:LOCALAPPDATA\Programs\Python") {
        if (Test-Path $base) {
            $candidates += Get-ChildItem $base -Recurse -Depth 3 -Filter python.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '\\(Lib|Scripts|pkgs)\\' } | ForEach-Object FullName
        }
    }
    $Python = $candidates | Where-Object { Test-Python $_ } | Select-Object -First 1
}
if (-not (Test-Python $Python)) {
    throw "No suitable Python found. Install 64-bit Python 3.11-3.13, or pass -Python C:\path\to\python.exe"
}
Write-Host "Using $Python"

if ($Recreate -and (Test-Path $venv)) { Remove-Item -Recurse -Force $venv }
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    & $Python -m venv $venv
    if ($LASTEXITCODE) { throw 'venv creation failed' }
}
$venvPython = Join-Path $venv 'Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE) { throw 'pip install failed' }

$local = Join-Path $root 'local.toml'
if (-not (Test-Path $local)) {
    Copy-Item (Join-Path $root 'local.example.toml') $local
    Write-Host 'Created local.toml from local.example.toml; edit machine-specific paths there.'
}
Write-Host 'NeuroNav runtime ready. Start with "Launch NeuroNav.cmd".'
