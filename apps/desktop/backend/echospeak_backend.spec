# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

spec_dir = Path(SPECPATH).resolve()
desktop_dir = spec_dir.parent
repo_root = desktop_dir.parents[1]
backend_root = repo_root / "apps" / "backend"


def data_tree(source: Path, destination: str):
    if not source.exists():
        return []
    rows = []
    for path in source.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative_parent = path.relative_to(source).parent
        rows.append((str(path), str(Path(destination) / relative_parent)))
    return rows


datas = []
for filename in ("SOUL.md",):
    path = backend_root / filename
    if path.exists():
        datas.append((str(path), "."))
datas += data_tree(backend_root / "skills", "skills")
datas += data_tree(backend_root / "workspaces", "workspaces")

for distribution in (
    "langchain",
    "langchain-core",
    "langchain-community",
    "langchain-openai",
    "langgraph",
    "transformers",
    "sentence-transformers",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

hiddenimports = collect_submodules("agent") + collect_submodules("api")

a = Analysis(
    [str(desktop_dir / "backend" / "echospeak_backend.py")],
    pathex=[str(backend_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest.mock"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="echospeak-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
