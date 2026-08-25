# PyInstaller yapilandirmasi - SAP Proje Radari
#
#   pyinstaller packaging/radar.spec --noconfirm
#
# onedir (tek klasor) tercih edildi: onefile her aciliste kendini gecici klasore
# cikariyor, hem yavas hem de virus tarayicilarini tetikliyor.
from pathlib import Path

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "config" / "config.yaml"), "config"),
    (str(ROOT / "config" / "keywords.yaml"), "config"),
    (str(ROOT / "src" / "scanner" / "web" / "templates"), "scanner/web/templates"),
    (str(ROOT / "src" / "scanner" / "web" / "static"), "scanner/web/static"),
    (str(ROOT / "packaging" / "radar.ico"), "packaging"),
]

hiddenimports = [
    "scanner.desktop.launcher", "scanner.desktop.tray", "scanner.desktop.mini",
    "scanner.sources.adzuna", "scanner.sources.reed", "scanner.sources.careerjet",
    "scanner.sources.ted", "scanner.sources.upwork_auth",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "pystray._win32", "PIL.Image", "PIL.ImageDraw",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["pytest", "matplotlib", "numpy", "pandas", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SAP Proje Radari",
    console=False,                 # konsol penceresi acilmaz
    icon=str(ROOT / "packaging" / "radar.ico"),
    version=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="SAP Proje Radari",
)
