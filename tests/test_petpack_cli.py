"""Author toolchain: deterministic build, validate, inspect (PETPACK_SPEC 23)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "petpack_cli.py"
REF_PACK = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
    "petpack" / "minimal-static.petpack"


@pytest.fixture(scope="module")
def cli_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("petpack_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli_main(cli_module):
    return cli_module.main


def test_reference_pack_validates(cli_main):
    assert cli_main(["validate", str(REF_PACK)]) == 0


def test_author_preflight_matches_gui_import_gate(
        cli_main, qt_application, capsys):
    preview = (Path(__file__).resolve().parent.parent / "assets" / "petpack" /
               "examples" / "realistic-retirement-cat-0.1.1.petpack")

    assert cli_main(["preflight", str(preview)]) == 0
    output = capsys.readouterr().out
    assert "preflight      ACCEPT" in output
    assert "realistic-cat" in output


def test_deterministic_build(tmp_path, cli_main):
    source = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
        "petpack" / "minimal-static"
    out_a = tmp_path / "a.petpack"
    out_b = tmp_path / "b.petpack"
    cli_main(["build", str(source), str(out_a)])
    cli_main(["build", str(source), str(out_b)])
    hash_a = hashlib.sha256(out_a.read_bytes()).hexdigest()
    hash_b = hashlib.sha256(out_b.read_bytes()).hexdigest()
    assert hash_a == hash_b
    # and it matches the committed reference artifact
    assert hash_a == hashlib.sha256(REF_PACK.read_bytes()).hexdigest()


def test_inspect_reports_identity(cli_main, capsys):
    assert cli_main(["inspect", str(REF_PACK)]) == 0
    out = capsys.readouterr().out
    assert "community.retirementpet/minimal-static" in out
    assert "demo" in out


def test_validate_rejects_corrupted_pack(cli_main, tmp_path, capsys):
    data = bytearray(REF_PACK.read_bytes())
    data[len(data) // 2] ^= 0xFF  # flip a byte mid-archive
    corrupt = tmp_path / "corrupt.petpack"
    corrupt.write_bytes(bytes(data))
    assert cli_main(["validate", str(corrupt)]) == 1
    out = capsys.readouterr().out
    assert "REJECT" in out


def test_init_creates_nested_directory_and_refuses_manifest_overwrite(
        cli_main, tmp_path):
    target = tmp_path / "new" / "character-pack"

    assert cli_main(["init", str(target)]) == 0
    manifest = target / "petpack.json"
    original = manifest.read_bytes()
    assert (target / "assets").is_dir()

    assert cli_main(["init", str(target)]) == 1
    assert manifest.read_bytes() == original


def test_build_rejects_frozen_output_without_touching_it(
        cli_module, cli_main, monkeypatch, tmp_path):
    source = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
        "petpack" / "minimal-static"
    frozen = tmp_path / "frozen.petpack"
    frozen.write_bytes(b"frozen-canary")
    monkeypatch.setattr(cli_module, "FROZEN_OUTPUTS", frozenset({frozen.resolve()}))

    assert cli_main(["build", str(source), str(frozen)]) == 2
    assert frozen.read_bytes() == b"frozen-canary"
