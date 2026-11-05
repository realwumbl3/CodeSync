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
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.winapi",
]

excludes = [
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
]

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# PySide's automatic plugin collection pulls Qt Quick in through the virtual
# keyboard. CodeSync uses the native Windows input context.
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(entry[0]).lower() not in {
        "qtvirtualkeyboardplugin.dll",
        "qt6virtualkeyboard.dll",
        "qt6quick.dll",
        "qt6qml.dll",
        "qt6qmlmeta.dll",
        "qt6qmlmodels.dll",
        "qt6qmlworkerscript.dll",
    }
]
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
