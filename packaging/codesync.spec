# -*- mode: python ; coding: utf-8 -*-
"""Freeze CodeSync as one windowed exe. Peers do not need Python or Git."""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
def _vc_runtime():
    """python3xx.dll will not load on a PC that never installed the VC++ runtime.

    PyInstaller treats vcruntime140.dll as a system library and leaves it out.
    LoadLibrary then reports 'The specified module could not be found' even
    though the Python DLL itself was unpacked.
    """
    names = ("vcruntime140.dll", "vcruntime140_1.dll")
    system = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
    found = []
    for name in names:
        for folder in (sys.base_prefix, system):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                found.append((name, path, "BINARY"))
                break
        else:
            raise SystemExit(f"Cannot bundle {name}; CodeSync.exe would fail on a clean PC")
    return found

hiddenimports = collect_submodules("codesync")
hiddenimports += collect_submodules("pystray")
hiddenimports += [
    "PIL.Image",
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.winapi",
]

# Widgets does not link Qt Network. These plugins are the only things that do.
_DROP_BINARIES = {
    "qt6network.dll",
    "qtnetwork.pyd",
    "qt6websockets.dll",
    "qtwebsockets.pyd",
    "qdirect2d.dll",
    "qminimal.dll",
    "qoffscreen.dll",
    "qjpeg.dll",
    "qgif.dll",
    "qtuiotouchplugin.dll",
    "qschannelbackend.dll",
    "qcertonlybackend.dll",
    "qnetworklistmanager.dll",
}
# Tray icons are PNG and ICO. Pillow only needs the core imaging codec.
_DROP_PREFIXES = ("_avif.", "_webp.", "_imagingtk.", "_imagingcms.")

def _keep_binary(entry) -> bool:
    base = os.path.basename(entry[0]).lower()
    if base in _DROP_BINARIES or base.startswith(_DROP_PREFIXES):
        return False
    if base in {
        "qtvirtualkeyboardplugin.dll",
        "qt6virtualkeyboard.dll",
        "qt6quick.dll",
        "qt6qml.dll",
        "qt6qmlmeta.dll",
        "qt6qmlmodels.dll",
        "qt6qmlworkerscript.dll",
    }:
        return False
    return True

# A tuple so PyInstaller can append "__main__" without rewriting this list.
# A mutated list never matches the cached Analysis, so every build starts over.
excludes = (
    "PySide6.QtNetwork",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DExtras",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtHttpServer",
    "PySide6.QtGraphs",
    "PySide6.QtLocation",
    "PySide6.QtNfc",
    "PIL._avif",
    "PIL._webp",
    "PIL._imagingtk",
    "PIL._imagingcms",
)

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets", "icon.png"), "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=2,
)
a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]
a.binaries += _vc_runtime()
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CodeSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "CodeSync.ico"),
)
