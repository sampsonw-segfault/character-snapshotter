# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for single-file CharacterSnapshotter.exe."""

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

EXCLUDED_MODULES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtNetworkAuth",
    "PySide6.QtSql",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHttpServer",
    "PySide6.QtNfc",
    "PySide6.QtWebView",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
    "PySide6.QtSvgWidgets",
    "tkinter",
    "unittest",
    "pytest",
]

UNUSED_PLUGIN_DIRS = (
    "pyside6/plugins/networkinformation/",
    "pyside6/plugins/tls/",
    "pyside6/plugins/generic/",
    "pyside6/plugins/platforminputcontexts/",
    "pyside6/plugins/multimedia/",
    "pyside6/plugins/sqldrivers/",
    "pyside6/plugins/webview/",
    "pyside6/plugins/position/",
    "pyside6/plugins/sensors/",
    "pyside6/plugins/designer/",
    "pyside6/plugins/canbus/",
    "pyside6/plugins/geoservices/",
    "pyside6/plugins/renderers/",
    "pyside6/plugins/renderplugins/",
    "pyside6/plugins/sceneparsers/",
    "pyside6/plugins/scxmldatamodel/",
    "pyside6/plugins/texttospeech/",
    "pyside6/plugins/assetimporters/",
    "pyside6/plugins/geometryloaders/",
    "pyside6/plugins/qmltooling/",
    "pyside6/plugins/vectorimageformats/",
)


def _should_keep_qt_entry(dest_name: str) -> bool:
    norm = dest_name.replace("\\", "/").lower()
    if "/translations/" in norm or norm.startswith("pyside6/translations/"):
        return False
    if any(norm.startswith(prefix) or f"/{prefix}" in norm for prefix in UNUSED_PLUGIN_DIRS):
        return False
    if "pyside6/plugins/platforms/" in norm:
        return norm.endswith("/qwindows.dll")
    if "pyside6/plugins/imageformats/" in norm:
        return norm.endswith("/qsvg.dll") or norm.endswith("/qico.dll")
    return True


a = Analysis(
    ["src/main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)

a.binaries = [entry for entry in a.binaries if _should_keep_qt_entry(entry[0])]
a.datas = [entry for entry in a.datas if _should_keep_qt_entry(entry[0])]

pyz = PYZ(a.pure)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 0, 0, 0),
        prodvers=(1, 0, 0, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("FileDescription", "Character Snapshotter"),
                        StringStruct("FileVersion", "1.0.0"),
                        StringStruct("InternalName", "CharacterSnapshotter"),
                        StringStruct("OriginalFilename", "CharacterSnapshotter.exe"),
                        StringStruct("ProductName", "Character Snapshotter"),
                        StringStruct("ProductVersion", "1.0.0"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CharacterSnapshotter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico",
    version=version_info,
)
