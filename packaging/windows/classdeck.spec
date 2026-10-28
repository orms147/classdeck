# PyInstaller spec for the ClassDeck desktop app: one folder, windowed, with the icon.
# Run through build.ps1 (it sets the paths); see docs/local-app.md, "Desktop app (Windows)".
# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parents[1]

datas = collect_data_files("classdeck", excludes=["**/__pycache__/*"])
datas += [(str(path), str(Path("curriculum") / path.parent.relative_to(ROOT / "curriculum")))
          for path in (ROOT / "curriculum").rglob("*") if path.is_file()]
hiddenimports = (
    collect_submodules("classdeck")
    + collect_submodules("uvicorn")
    + ["multipart", "python_multipart"]
)

a = Analysis(
    [str(HERE / "classdeck_app.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "pytest", "_pytest", "IPython", "matplotlib", "mypy",
        # PyMuPDF imports its optional AI layout analyser when present; ClassDeck never uses it
        "pymupdf.layout", "pymupdf4llm", "onnxruntime", "numpy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClassDeck",
    icon=str(HERE / "classdeck.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClassDeck", upx=False)
