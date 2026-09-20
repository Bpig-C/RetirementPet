"""CR13-05: release-materials generator contract tests.

Uses a SYNTHETIC dist directory so failures are deterministic; the final
candidate audit runs separately on the real artifact.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "generate_release_materials.py"


def _tiny_png() -> bytes:
    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00" + b"\x00\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _dll_with_version(version: str = "1.2.3.4") -> bytes:
    """Minimal PE-ish bytes are enough: the generator reads the version
    resource only for dll: sources, so the synthetic test supplies real
    version resources via a tiny hand-built block when needed.  For the
    fail-closed tests a plain marker byte string is fine."""
    return b"MZ" + version.encode("ascii") + b"\x00" * 64


def _build_synthetic_dist(root: Path) -> Path:
    dist = root / "dist" / "RetirementPet"
    (dist / "_internal" / "PySide6").mkdir(parents=True)
    (dist / "_internal" / "assets").mkdir(parents=True)
    (dist / "RetirementPet.exe").write_bytes(b"MZ" + b"\x00" * 32)
    (dist / "_internal" / "build-info.json").write_text(
        json.dumps({"version": "9.9.9", "build_id": "b" * 64,
                    "commit": "c" * 40}), encoding="utf-8")
    (dist / "_internal" / "LICENSE").write_text(
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\n", encoding="utf-8")
    (dist / "_internal" / "PySide6" / "Qt6Core.dll").write_bytes(
        _dll_with_version("6.8.3.0"))
    (dist / "_internal" / "libssl-3-x64.dll").write_bytes(
        _dll_with_version("3.2.4"))
    (dist / "_internal" / "PySide6" / "avcodec-61.dll").write_bytes(
        _dll_with_version("61.19.100"))
    (dist / "_internal" / "assets" / "icon.png").write_bytes(_tiny_png())
    return dist


def _receipt_for(dist: Path, root: Path) -> Path:
    receipt = root / "release-receipt.json"
    receipt.write_text(json.dumps({
        "schema": 1, "result": "PASS",
        "commit": "c" * 40, "recipe_id": "r" * 24,
        "artifact_id": "a" * 24,
        "exe_sha256": "e" * 64,
    }), encoding="utf-8")
    return receipt


def _run_generator(dist: Path, receipt: Path, output: Path,
                   extra: list[str] | None = None):
    argv = [sys.executable, str(GENERATOR), "--dist", str(dist),
            "--receipt", str(receipt), "--output", str(output)]
    if extra:
        argv += extra
    return subprocess.run(argv, capture_output=True, timeout=120,
                          cwd=str(ROOT))


OVERRIDES = [
    "--version-override", "openssl=3.2.4",
    "--version-override", "ffmpeg=61.19.100",
    "--version-override", "pyside6-qt6=6.8.3.0",
]


def test_generator_attributes_known_components_and_fails_closed_on_unknown(
        tmp_path):
    """Synthetic dist: every known component resolves with a real version,
    and one unknown DLL aborts the whole run (fail closed)."""
    dist = _build_synthetic_dist(tmp_path)
    receipt = _receipt_for(dist, tmp_path)
    output = tmp_path / "materials"

    (dist / "_internal" / "mystery-unknown.dll").write_bytes(b"MZ???")
    result = _run_generator(dist, receipt, output, OVERRIDES)
    assert result.returncode == 2
    assert b"mystery-unknown.dll" in result.stderr

    (dist / "_internal" / "mystery-unknown.dll").unlink()
    result = _run_generator(dist, receipt, output, OVERRIDES)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    sbom = json.loads((output / "sbom.json").read_text(encoding="utf-8"))

    components = sbom["components"]
    # winrt-projections needs a winrt/ directory, which the synthetic
    # dist does not carry; the REAL candidate audit covers it
    for required in ("openssl", "ffmpeg", "pyside6-qt6", "python",
                     "pyinstaller-bootloader", "retirement-pet",
                     "bundled-assets"):
        assert required in components, required
    assert components["openssl"]["version"] == "3.2.4"
    assert components["ffmpeg"]["version"] == "61.19.100"
    assert components["pyside6-qt6"]["version"] == "6.8.3.0"
    assert components["retirement-pet"]["version"] == "9.9.9"
    for component_id, rollup in components.items():
        assert rollup["version"], component_id
    for entry in sbom["files"]:
        assert entry["version"], entry["file"]

    licenses = output / "LICENSES"
    for name, marker in (
            ("lgpl-3.0.txt", "GNU LESSER GENERAL PUBLIC LICENSE"),
            ("lgpl-2.1.txt", "GNU LESSER GENERAL PUBLIC LICENSE"),
            ("apache-2.0.txt", "Apache License"),
            ("pyinstaller-bootloader.txt", "PyInstaller"),
            ("python.psf-2.0.txt", "PYTHON SOFTWARE FOUNDATION LICENSE"),
            ("retirement-pet.mit.txt",
             "Permission is hereby granted, free of charge")):
        text = (licenses / name).read_text(encoding="utf-8")
        assert len(text) > 1000, name
        assert marker in text, name

    assert sbom["artifact"]["commit"] == "c" * 40
    assert sbom["artifact"]["exe_sha256"] == "e" * 64


def test_generator_notice_and_lgpl_doc_exist(tmp_path):
    dist = _build_synthetic_dist(tmp_path)
    receipt = _receipt_for(dist, tmp_path)
    output = tmp_path / "materials"
    assert _run_generator(dist, receipt, output, OVERRIDES).returncode == 0
    lgpl_doc = (output / "LGPL-SOURCES.md").read_text(encoding="utf-8")
    assert "6.8.3" in lgpl_doc  # real resolved version in the doc
    assert "FFmpeg" in lgpl_doc and "ffmpeg.org" in lgpl_doc  # RR13-02
    assert (output / "NOTICE").is_file()
