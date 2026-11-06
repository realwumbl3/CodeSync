$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    py -3.12 -m venv .venv
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install CodeSync's Python dependencies." }
& $python -c "from PySide6.QtWidgets import QWidget; assert hasattr(QWidget, 'setWindowAnchor')"
if ($LASTEXITCODE -ne 0) { throw "The patched PySide6 wheel is required." }
& $python -m unittest discover -s tests
New-Item -ItemType Directory -Force -Path assets | Out-Null
& $python -c "from codesync.flyout.icons import save_application_icon; save_application_icon('assets/CodeSync.ico')"
& $python -c "from codesync.sync.update import bump_version; bump_version()"
& $python -m PyInstaller packaging\codesync.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "Could not package CodeSync." }
Write-Host "Built dist\CodeSync.exe"
