"""Build a standalone Windows executable for the upgraded na-iwa pet.

Run on Windows:  python tools/build_windows.py
Output:          dist/奶小蛙-Windows-<version>.zip
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.3.0"
APP_NAME = "奶小蛙"
BUILD_NAME = "naiwa"
SKIP_SUFFIXES = {".pyc", ".pyo"}


def asset_paths():
    """Every runtime file, minus local backup folders."""
    items = []
    for base in ("assets", "media", "licenses"):
        for path in sorted((ROOT / base).rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if any(part.startswith("_") or part.endswith("_backup") for part in rel.parts):
                continue
            if path.suffix in SKIP_SUFFIXES or path.name == ".DS_Store":
                continue
            items.append(rel)
    for name in ("media_map.json", "QUICKSTART.txt", "THIRD_PARTY_NOTICES.md"):
        if (ROOT / name).is_file():
            items.append(Path(name))
    return items


def default_settings(staging):
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / "pet_settings.json"
    target.write_text(json.dumps({
        "displayScale": 0.7, "fps": None, "randomMinSec": 180,
        "randomMaxSec": 240, "windowPad": 8,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def write_spec(files, spec_path):
    datas = []
    for rel in files:
        source = (ROOT / rel).as_posix()
        dest = rel.parent.as_posix()
        datas.append((source, "." if dest == "." else dest))
    excludes = ["numpy", "imageio_ffmpeg", "matplotlib", "scipy", "IPython",
                "pytest", "pygame.tests", "PIL.ImageQt"]
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "a = Analysis([{entry!r}], pathex=[{root!r}], binaries=[], datas={datas!r},\n"
        "             hiddenimports=[], hookspath=[], hooksconfig={{}}, runtime_hooks=[],\n"
        "             excludes={excludes!r}, noarchive=False)\n"
        "pyz = PYZ(a.pure)\n"
        "exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name={name!r},\n"
        "          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,\n"
        "          console=False, icon={icon!r})\n".format(
            entry=(ROOT / "pet.pyw").as_posix(), root=ROOT.as_posix(), datas=datas,
            excludes=excludes, name=BUILD_NAME, icon=(ROOT / "assets/app.ico").as_posix()),
        encoding="utf-8")


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows exe on Windows (or the windows-latest runner).")
    for package in ("PIL", "pygame", "psutil", "pymunk", "PyInstaller"):
        __import__(package)

    files = asset_paths()
    settings = default_settings(ROOT / "build/windows-defaults")
    files.append(settings.relative_to(ROOT))

    spec = ROOT / "build/specs/naiva.spec"
    write_spec(files, spec)
    print(f"resources: {len(files)} files")

    shutil.rmtree(ROOT / "build/work", ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/work"),
         str(spec)],
        check=True, cwd=ROOT)

    built = ROOT / "dist" / f"{BUILD_NAME}.exe"
    target = ROOT / "dist" / f"{APP_NAME}.exe"
    if not built.is_file():
        raise SystemExit(f"missing build output: {built}")
    shutil.copy2(built, target)

    readme = ROOT / "dist/使用说明.txt"
    readme.write_text(
        "奶小蛙 Windows 版（macOS 升级版的 Windows 构建）\n"
        "========================================\n\n"
        "系统要求：Windows 10 / 11，64 位。\n\n"
        "使用：\n"
        "1. 双击 奶小蛙.exe。首次运行 Windows 可能提示来自未知发布者，点「更多信息」→「仍要运行」。\n"
        "2. 宠物会出现在屏幕右下角，右键可打开菜单。\n"
        "3. 数据与日志保存在 %LOCALAPPDATA%\\naiwa。\n\n"
        "新增功能：右键「形态」可切换奶蛙 / 奶蛋 / 奶狗 / 奶杯 / 奶豆 / 奶鼠；\n"
        "「全体形态（一起出来）」让六种形态同时登场。\n\n"
        "快捷键：Control+Alt+1 全体大笑，Control+Alt+C 复制分身，\n"
        "Control+Alt+V 粘贴到鼠标位置，Control+Alt+Q 退出。\n\n"
        "已知限制：奶豆没有真实大笑素材，触发大笑时会改用蹦跳动作。\n",
        encoding="utf-8")

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (ROOT / "dist/SHA256.txt").write_text(f"{digest}  奶小蛙.exe\n", encoding="utf-8")

    archive = ROOT / "dist" / f"奶小蛙-Windows-{VERSION}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for item in (target, readme, ROOT / "dist/SHA256.txt"):
            bundle.write(item, item.name)
    print(f"built: {archive}  ({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
