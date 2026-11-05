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
& $python -c @"
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
size = 256
image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=48, fill=(32, 32, 36, 255))
try:
    font = ImageFont.truetype('segoeui.ttf', 108)
except OSError:
    font = ImageFont.load_default()
text = 'CS'
box = draw.textbbox((0, 0), text, font=font)
width, height = box[2] - box[0], box[3] - box[1]
draw.text(((size - width) / 2, (size - height) / 2 - 8), text, fill=(243, 243, 243, 255), font=font)
Path('assets').mkdir(exist_ok=True)
image.save('assets/CodeSync.ico', sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
"@
& $python -c "from codesync.sync.update import bump_version; bump_version()"
& $python -m PyInstaller packaging\codesync.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Could not package CodeSync." }
Write-Host "Built dist\CodeSync.exe"
