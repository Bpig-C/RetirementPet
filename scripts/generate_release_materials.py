"""Generate release license materials from a REAL onedir artifact (V13-08).

Fails closed: any file that cannot be attributed to a known component
aborts the whole run, so a silently new DLL can never ship unlicensed.

Outputs under ``<dist>/release-materials/``:

- ``sbom.json``       per-file inventory: component, version, license,
                      source; plus component rollups and the receipt
                      binding (commit/build_id/exe sha256);
- ``LICENSES/``       full license texts for every distributed component;
- ``NOTICE``          user-facing notice entry point;
- ``LGPL-SOURCES.md`` where to obtain corresponding sources and how to
                      relink/replace the LGPL (Qt) libraries.

Usage::

    python scripts/generate_release_materials.py --dist dist/RetirementPet \\
        --receipt <release-receipt.json> --output <dist>/release-materials
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: component attribution rules (CR13-05): each distributed file resolves
#: to exactly one component with a NON-NULL version and a FULL license
#: text under scripts/release_licenses/.  Order matters: the specific
#: third-party rules (openssl, ffmpeg) must run BEFORE the broad python
#: and pyside catch-alls.
COMPONENTS = [
    {
        "id": "openssl",
        "version_from": "dll:libssl-3-x64.dll",
        "license": "Apache-2.0",
        "license_file": "apache-2.0.txt",
        "match": lambda rel: rel.endswith(".dll")
        and rel.startswith(("libcrypto", "libssl")),
    },
    {
        "id": "ffmpeg",
        "version_from": "dll:auto",
        "license": "LGPL-2.1-or-later",
        "license_file": "lgpl-2.1.txt",
        "match": lambda rel: rel.startswith("PySide6/")
        and (Path(rel).name.startswith("av")
             or "ffmpeg" in Path(rel).name.lower()),
    },
    {
        "id": "pyside6-qt6",
        "version_from": "dll:PySide6/Qt6Core.dll",
        "license": "LGPL-3.0-only",
        "license_file": "lgpl-3.0.txt",
        "match": lambda rel: rel.startswith(("PySide6/", "shiboken6/")),
    },
    {
        "id": "winrt-projections",
        "version_from": "lock-pkg:winrt-runtime",
        "license": "Apache-2.0",
        "license_file": "apache-2.0.txt",
        "match": lambda rel: rel.startswith("winrt/"),
    },
    {
        "id": "pyinstaller-bootloader",
        "version_from": "lock-pkg:pyinstaller",
        "license": "GPL-2.0-or-later WITH PyInstaller-Bootloader-Exception",
        "license_file": "pyinstaller-bootloader.txt",
        # primary attribution for the two PyInstaller container files;
        # their OTHER embedded components are recorded in "contains"
        "match": lambda rel: rel == "RetirementPet.exe"
        or rel.startswith("base_library.zip"),
    },
    {
        # project-owned data: icons, character packs.  Packs carry their
        # own legal metadata inside; the asset license map lives in
        # assets/LICENSE.md of the repository.
        "id": "bundled-assets",
        "version_from": "runtime",
        "license": "ProjectAsset",
        "license_file": "project-assets.notice",
        "match": lambda rel: rel.startswith("assets/"),
    },
    {
        "id": "retirement-pet",
        "version_from": "runtime",
        "license": "MIT",
        "license_file": "retirement-pet.mit.txt",
        "match": lambda rel: rel.endswith(".py")
        or rel in ("build-info.json", "THIRD_PARTY_NOTICES.md",
                   "config/defaults.json", "config/fixture_registry.json",
                   "config/fixture_registry.schema.json",
                   "config/public_snapshot.json", "docs/LICENSING.md"),
    },
    {
        "id": "python",
        "version_from": "lock-python",
        "license": "PSF-2.0",
        "license_file": "python.psf-2.0.txt",
        # CPython runtime pieces ONLY - deliberately NOT a catch-all, so
        # an unknown DLL fails closed instead of riding along (CR13-05)
        "match": lambda rel: (
            rel.startswith("python3") and rel.endswith(".dll")
            or rel.endswith(".pyd")
            and not rel.startswith(("PySide6", "winrt"))
            or rel.startswith("api-ms-win-")
            or rel in ("sqlite3.dll", "libexpat.dll", "liblzma.dll",
                       "zlib.dll", "ucrtbase.dll", "CONCRT140.dll",
                       "ffi.dll", "LIBBZ2.dll", "LICENSE",
                       "MSVCP140.dll", "MSVCP140_1.dll", "MSVCP140_2.dll",
                       "VCRUNTIME140.dll", "VCRUNTIME140_1.dll")),
    },
]

#: where each FULL license text comes from:
#: ("release_licenses", name) -> scripts/release_licenses/<name>;
#: ("repo", rel) / ("dist", rel) -> verbatim file copy
LICENSE_SOURCES = {
    "retirement-pet.mit.txt": ("repo", "LICENSE"),
    # NOTE: the MIT file PyInstaller drops at _internal/LICENSE belongs to
    # a bundled third-party component (libffi et al.) and is copied verbatim
    # as thirdparty-libffi.et-al.mit.txt below
    "python.psf-2.0.txt": ("release_licenses", "python.psf-2.0.txt"),
    "lgpl-3.0.txt": ("release_licenses", "lgpl-3.0.txt"),
    "lgpl-2.1.txt": ("release_licenses", "lgpl-2.1.txt"),
    "apache-2.0.txt": ("release_licenses", "apache-2.0.txt"),
    "pyinstaller-bootloader.txt": ("release_licenses",
                                   "pyinstaller-bootloader.txt"),
    "project-assets.notice": None,  # generated text (see below)
}

#: distinctive substring each shipped full text must contain, so a
#: truncated or wrong file can never be distributed
LICENSE_CONTAINS = {
    "retirement-pet.mit.txt": "Permission is hereby granted, free of charge",
    "python.psf-2.0.txt": "PYTHON SOFTWARE FOUNDATION LICENSE",
    "lgpl-3.0.txt": "GNU LESSER GENERAL PUBLIC LICENSE",
    "lgpl-2.1.txt": "GNU LESSER GENERAL PUBLIC LICENSE",
    "apache-2.0.txt": "Apache License",
    "pyinstaller-bootloader.txt": "PyInstaller",
}

PROJECT_ASSETS_NOTICE = """随包资产许可说明（project-assets.notice）

本目录中的图标与角色 PetPack 数据包为项目自有或按包内许可元数据分发：
每个 .petpack 的 petpack.json 声明其 rights_declarations 与 legal 文件；
资产级许可映射见仓库 assets/LICENSE.md。第三方 IP 的角色内容
（如罗小黑）不在官方分发内，见 docs/CONTENT_POLICY.md。
"""

LGPL_SOURCES_DOC = """# LGPL 对应源码与组件替换说明

本便携目录中的 Qt（经 PySide6 wheel 分发）以 GNU Lesser General Public
License v3 发布。依据 LGPL 第 4 与第 6 条，本文件说明如何获得对应源码，
以及如何在本目录中替换/重新链接这些库。

## 对应源码获取方式

- PySide6/Qt 官方源码：https://code.qt.io/ （qt/qtbase 分支 {qt_version}）；
  PySide6 绑定源码：https://code.qt.io/pyside/pyside-setup （版本 {pyside_version}）。
- The Qt Company 的公开下载页同时提供与 wheel 相同版本的完整源码包；
  亦可使用 `pip download pyside6=={pyside_version} --no-binary :all:`
  获取 sdist 形式的构建源码。
- 上述 wheel 对应的构建配置（shiboken6 生成器版本）随 sdist 一起发布。

## 替换 / 重新链接步骤

1. 关闭 RetirementPet.exe。
2. 用按上述源码自行构建（或从 Qt 官方获得）的同版本 Qt 6 动态库替换
   `_internal/PySide6/` 下对应的 `Qt6*.dll` 与 `plugins/` 中的插件；
3. 替换后重新启动即可完成重新链接；无需重新安装整个应用。
4. `_internal/PySide6/*.pyd` 是 Python 绑定扩展（shiboken6 生成），
   属于同一 LGPL 交付；替换 Qt 动态库即可满足修改调试需求。

## FFmpeg（LGPL-2.1-or-later）

PySide6 的多媒体后端携带 Qt 官方构建的 FFmpeg 共享库
（`_internal/PySide6/avcodec-61.dll`、`avformat-61.dll`、`avutil-59.dll`
及 `plugins/multimedia/ffmpegmediaplugin.dll`），以 LGPL-2.1-or-later
发布。

- 对应源码：https://ffmpeg.org/download.html ；Qt 使用的构建配置与
  补丁随 Qt 源码发布（qt/qtwebengine-chromium 之外的
  `qtmultimedia` 仓库含构建脚本），版本对应本包 SBOM 中记录的
  DLL 版本资源（如 61.19.100 对应 FFmpeg 7.x 分支的库版本编号）。
- 替换/重新链接：关闭 RetirementPet.exe 后，用自行构建的同 major
  版本 FFmpeg DLL 替换 `_internal/PySide6/` 下同名文件即可，无需
  重装整个应用。

## 本包满足 LGPL 义务的方式

- 以动态链接（onedir 共享 DLL）形式发布，允许用户替换库文件；
- 本目录 LICENSES/ 与本文件共同提供许可文本与源码入口；
- 应用自身代码（MIT）不拷贝 Qt 头文件之外的任何 LGPL 源码。
"""


def dll_product_version(path: Path) -> str:
    """Read FileVersion from a DLL's version resource; fail closed."""
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise RuntimeError(f"no version resource: {path.name}")
    data = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(
            str(path), 0, size, data):
        raise RuntimeError(f"cannot read version info: {path.name}")
    value = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(
            data, "\\VarFileInfo\\Translation",
            ctypes.byref(value), ctypes.byref(length)) or not length.value:
        raise RuntimeError(f"no translation table: {path.name}")
    translation = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint16 * 2))
    lang_id, code_page = translation.contents[0], translation.contents[1]
    key = f"\\StringFileInfo\\{lang_id:04x}{code_page:04x}\\FileVersion"
    if not ctypes.windll.version.VerQueryValueW(
            data, key, ctypes.byref(value), ctypes.byref(length)):
        raise RuntimeError(f"no FileVersion string: {path.name}")
    return ctypes.wstring_at(value.value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock() -> dict:
    """Return the whole public build lock (python + packages)."""
    return json.loads((ROOT / "scripts" / "build_lock.json")
                      .read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version-override", action="append",
                        metavar="COMPONENT=VERSION", default=[],
                        help="test hook: pin a component version when the "
                             "synthetic dist carries no real version "
                             "resources; never used for release runs")
    args = parser.parse_args()
    overrides = dict(
        item.split("=", 1) for item in args.version_override)

    dist = args.dist.resolve()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    lock = load_lock()

    # the app version comes from the artifact's own embedded identity,
    # never from a mutable top-level field (CR13-05: no null versions)
    build_info_path = dist / "_internal" / "build-info.json"
    build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    runtime_version = build_info.get("version")
    if not runtime_version:
        print("FAIL: artifact build-info has no version", file=sys.stderr)
        return 2

    def resolve_version(source: str, rel: str) -> str:
        if source == "runtime":
            return runtime_version
        if source.startswith("lock-pkg:"):
            value = lock.get("packages", {}).get(source.split(":", 1)[1])
        elif source == "lock-python":
            value = lock.get("python", {}).get("version")
        elif source.startswith("dll:"):
            target = source.split(":", 1)[1]
            dll_path = (dist / "_internal" / target) if target != "auto"                 else (dist / "_internal" / Path(rel).parent
                      / Path(rel).name)
            if not dll_path.is_file():
                # ffmpeg dlls live beside Qt6Core; resolve "auto" by name
                candidates = sorted(
                    (dist / "_internal").rglob(Path(rel).name))
                if not candidates:
                    raise RuntimeError(f"version probe target missing: {rel}")
                dll_path = candidates[0]
            value = dll_product_version(dll_path)
        else:
            raise RuntimeError(f"unknown version source: {source}")
        if not value:
            raise RuntimeError(f"version resolved to null via {source}")
        return str(value)

    files: list[dict] = []
    unattributed: list[str] = []
    versions: dict[str, str] = {}
    # container files hold MORE than their primary component: record the
    # embedded components explicitly instead of dropping them to
    # first-match-wins (RR13-02)
    CONTAINS = {
        "RetirementPet.exe": [
            {"component": "pyinstaller-bootloader",
             "version_from": "lock-pkg:pyinstaller",
             "license": "GPL-2.0-or-later WITH PyInstaller-Bootloader-Exception",
             "role": "bootloader and archive container"},
            {"component": "retirement-pet",
             "version_from": "runtime", "license": "MIT",
             "role": "application bytecode archive"},
        ],
        "base_library.zip": [
            {"component": "pyinstaller-bootloader",
             "version_from": "lock-pkg:pyinstaller",
             "license": "GPL-2.0-or-later WITH PyInstaller-Bootloader-Exception",
             "role": "archive container"},
            {"component": "python",
             "version_from": "lock-python", "license": "PSF-2.0",
             "role": "compressed CPython standard library"},
        ],
    }
    for path in sorted(dist.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(dist).as_posix()
        if rel.startswith("release-materials/"):
            continue
        # attribution works on the in-package layout (PySide6/...,
        # assets/...) regardless of the onedir _internal/ wrapper
        norm = rel.removeprefix("_internal/")
        for component in COMPONENTS:
            if component["match"](norm):
                if component["id"] not in versions:
                    versions[component["id"]] = overrides.get(
                        component["id"]) or resolve_version(
                        component["version_from"], norm)
                entry = {
                    "file": rel,
                    "component": component["id"],
                    "version": versions[component["id"]],
                    "license": component["license"],
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                contains = CONTAINS.get(norm)
                if contains:
                    entry["contains"] = [
                        {"component": part["component"],
                         "version": overrides.get(part["component"])
                         or resolve_version(part["version_from"], norm),
                         "license": part["license"],
                         "role": part["role"]}
                        for part in contains]
                files.append(entry)
                break
        else:
            unattributed.append(rel)

    if unattributed:
        print("FAIL: files without a component attribution:",
              file=sys.stderr)
        for rel in unattributed[:20]:
            print("  ", rel, file=sys.stderr)
        return 2

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    licenses_dir = output / "LICENSES"
    licenses_dir.mkdir(parents=True, exist_ok=True)

    # copy FULL license texts; verify each against its distinctive marker
    # so a truncated or wrong file can never ship (CR13-05)
    license_files_used = sorted({
        component["license_file"] for component in COMPONENTS
    } | {"project-assets.notice",
         "thirdparty-libffi.et-al.mit.txt"})
    for license_file in license_files_used:
        source_ref = LICENSE_SOURCES.get(license_file)
        if source_ref is None:
            text = PROJECT_ASSETS_NOTICE
        elif source_ref[0] == "repo":
            text = (ROOT / source_ref[1]).read_text(encoding="utf-8")
        elif source_ref[0] == "dist":
            text = (dist / source_ref[1]).read_text(encoding="utf-8")
        else:
            text = (ROOT / "scripts" / "release_licenses"
                    / source_ref[1]).read_text(encoding="utf-8")
        if license_file == "thirdparty-libffi.et-al.mit.txt":
            # the MIT text PyInstaller ships at _internal/LICENSE covers
            # bundled third-party bits (libffi, libbz2, ...); copy it so
            # those notices reach users verbatim
            text = (dist / "_internal" / "LICENSE").read_text(
                encoding="utf-8")
        marker = LICENSE_CONTAINS.get(license_file)
        if marker and marker not in text:
            print(f"FAIL: license text {license_file} lacks marker "
                  f"{marker!r}", file=sys.stderr)
            return 3
        (licenses_dir / license_file).write_text(text, encoding="utf-8")

    pyside_version = versions.get("pyside6-qt6", "unknown")
    (output / "LGPL-SOURCES.md").write_text(
        LGPL_SOURCES_DOC.format(pyside_version=pyside_version,
                                qt_version=pyside_version),
        encoding="utf-8")

    components: dict[str, dict] = {}
    for entry in files:
        rollup = components.setdefault(entry["component"], {
            "version": entry["version"],
            "license": entry["license"],
            "files": 0,
            "bytes": 0,
        })
        rollup["files"] += 1
        rollup["bytes"] += entry["bytes"]

    sbom = {
        "schema": "retirement-pet.release-sbom/2",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "artifact": {
            "commit": receipt.get("commit"),
            "recipe_id": receipt.get("recipe_id"),
            "artifact_id": receipt.get("artifact_id"),
            "exe_sha256": receipt.get("exe_sha256"),
            "version": runtime_version,
            "receipt_sha256": sha256_file(args.receipt),
        },
        "components": dict(sorted(components.items())),
        "file_count": len(files),
        "files": files,
    }
    (output / "sbom.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    notice = f"""RetirementPet {runtime_version} 第三方组件声明（NOTICE）

本目录（release-materials/）是本次便携分发的许可材料入口：

- sbom.json        逐文件组件清单（含 SHA-256），绑定 receipt
                   commit={receipt.get('commit')} recipe={receipt.get('recipe_id')}
- LICENSES/        各分发组件的完整许可文本
- LGPL-SOURCES.md  Qt/PySide6（LGPL-3.0）与 FFmpeg（LGPL-2.1）
                   对应源码入口与替换说明

本应用自身代码采用 MIT（LICENSES/retirement-pet.mit.txt）。
分发组件：{', '.join(sorted(components))}
完整义务描述见仓库 THIRD_PARTY_NOTICES.md 与 docs/LICENSING.md。
"""
    (output / "NOTICE").write_text(notice, encoding="utf-8")

    # RR13-03: the materials directory carries its OWN identity - a
    # manifest of every file plus a receipt binding the whole directory
    # to the candidate.  Without this, a modified or missing SBOM or
    # license file would be undetectable.
    material_files = sorted(
        path for path in output.rglob("*") if path.is_file())
    manifest_entries = [
        {
            "file": path.relative_to(output).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in material_files
    ]
    if "sbom.json" not in {entry["file"] for entry in manifest_entries}:
        raise RuntimeError("sbom.json missing from materials output")
    combined = hashlib.sha256()
    for entry in manifest_entries:
        combined.update(entry["sha256"].encode("ascii"))
    materials_receipt = {
        "schema": "retirement-pet.materials-receipt/1",
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "candidate": {
            "commit": receipt.get("commit"),
            "recipe_id": receipt.get("recipe_id"),
            "artifact_id": receipt.get("artifact_id"),
            "exe_sha256": receipt.get("exe_sha256"),
            "version": runtime_version,
            "receipt_sha256": sha256_file(args.receipt),
        },
        "materials": {
            "file_count": len(manifest_entries),
            "files": manifest_entries,
            "combined_sha256": combined.hexdigest(),
        },
    }
    (output / "materials-receipt.json").write_text(
        json.dumps(materials_receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"release materials written: {output}")
    print(f"  files inventoried: {len(files)}")
    for component_id, rollup in sorted(components.items()):
        print(f"  {component_id}: {rollup['files']} files, "
              f"{rollup['license']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
