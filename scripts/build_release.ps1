param([string]$Version = '0.1.0')
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskRoot '.venv-release/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv-release and install requirements-release.txt first.'
}
Push-Location $taskRoot
try {
    & $taskPython -m PyInstaller --noconfirm StockLab.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'LICENSE') -Destination 'dist/StockLab/LICENSE'
    Copy-Item -LiteralPath (Join-Path $taskRoot 'docs/PORTABLE.md') -Destination 'dist/StockLab/README.md'
    & $taskPython scripts/collect_release_licenses.py dist/StockLab
    if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
    & $taskPython scripts/package_portable.py dist/StockLab "release/StockLab-v$Version-windows-x64.zip"
    if ($LASTEXITCODE -ne 0) { throw 'ZIP packaging failed' }
} finally { Pop-Location }
