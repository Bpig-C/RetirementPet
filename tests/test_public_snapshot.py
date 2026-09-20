from __future__ import annotations

import io
import json
import sys
import os
from pathlib import Path
import socket
import struct
import subprocess
import zipfile
import zlib

import pytest

from scripts import export_public_snapshot as public_export


POLICY = {
    "schema_version": 1,
    "additional_exclude_paths": [],
    "additional_exclude_prefixes": [],
    "forbidden_username_sha256": [
        "9a6ef30b9544e3eff29790c9694360a172ca674acc302ca14a0f6164d7122a25"
    ],
    "limits": {
        "max_archive_members": 128,
        "max_archive_uncompressed_bytes": 4 * 1024 * 1024,
        "max_blob_bytes": 2 * 1024 * 1024,
        "max_files": 256,
        "max_total_bytes": 8 * 1024 * 1024,
    },
    "required_paths": [
        ".github/CODEOWNERS",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/workflows/tests.yml",
        "assets/LICENSE.md",
        "config/public_snapshot.json",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/LICENSING.md",
        "evidence/README.md",
        "pyproject.toml",
        "scripts/export_public_snapshot.py",
        "src/retirement_pet/__init__.py",
        "tests/test_public_snapshot.py",
    ],
}

PRIVATE_USERNAME_CANARY = "Snapshot" + "PrivateCanary"
PRIVATE_KEY_CANARY = "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n"


def _windows_user_path(username: str, tail: str) -> str:
    separator = chr(92)
    return f"C:{separator}Users{separator}{username}{separator}{tail}"


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8", "strict").strip()


def _write_files(root: Path, files: dict[str, str | bytes]) -> None:
    for relative, content in files.items():
        path = root.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path, additions: dict[str, str | bytes] | None = None) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    files: dict[str, str | bytes] = {
        ".github/CODEOWNERS": "* @fixture-owner\n",
        ".github/PULL_REQUEST_TEMPLATE.md": "# Pull request\n",
        ".github/workflows/tests.yml": "name: tests\non: [push]\njobs: {}\n",
        "assets/LICENSE.md": "# Asset license map\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "LICENSE": "Fixture license\n",
        "README.md": "# Safe public fixture\n\nUse repository-relative commands.\n",
        "SECURITY.md": "# Security\n",
        "THIRD_PARTY_NOTICES.md": "# Third-party notices\n",
        "docs/LICENSING.md": "# Licensing\n",
        "evidence/README.md": "# Public evidence policy\n\nRaw machine runs are not published.\n",
        "pyproject.toml": "[project]\nname='snapshot-fixture'\nversion='1.0.0'\n",
        "scripts/export_public_snapshot.py": "# Fixture exporter entry point.\n",
        "src/retirement_pet/__init__.py": "__version__ = '1.0.0'\n",
        "tests/test_public_snapshot.py": "# Fixture policy tests.\n",
        "config/public_snapshot.json": json.dumps(POLICY, indent=2) + "\n",
        "evidence/20260101-local-run/settings.json": json.dumps(
            {"pid": 4217, "path": _windows_user_path("PrivateFixture", "x")}
        ) + "\n",
        "evidence/20260101-local-run/app.log": "raw local log\n",
    }
    if additions:
        files.update(additions)
    _write_files(root, files)
    _git(root, "add", "--all")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return root


def _archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def _png(extra_chunks: list[tuple[bytes, bytes]] | None = None) -> bytes:
    def chunk(chunk_type: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    chunks = [(b"IHDR", ihdr), *(extra_chunks or []), (b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")), (b"IEND", b"")]
    return public_export.PNG_SIGNATURE + b"".join(chunk(kind, payload) for kind, payload in chunks)


def _ico_with_png(png: bytes) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def _audit(output: Path) -> dict:
    return json.loads(public_export.default_audit_path(output).read_text(encoding="utf-8"))


def test_exports_head_as_clean_single_commit_with_auditable_exclusions(tmp_path: Path):
    repo = _repo(tmp_path, {"assets/icon.png": _png()})
    output = tmp_path / "public"

    result = public_export.export_snapshot(repo, output)

    assert output.is_dir()
    assert _git(output, "rev-list", "--count", "HEAD") == "1"
    assert _git(output, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert [path.relative_to(output).as_posix() for path in (output / "evidence").rglob("*") if path.is_file()] == [
        "evidence/README.md"
    ]
    assert not (output / ".git" / "objects" / "info" / "alternates").exists()
    assert (output / "README.md").read_text(encoding="utf-8").startswith("# Safe")
    assert (output / "evidence" / "README.md").is_file()
    assert (output / public_export.MANIFEST_NAME).is_file()
    assert result.source_commit == _git(repo, "rev-parse", "HEAD")
    assert result.public_commit == _git(output, "rev-parse", "HEAD")
    assert _git(output, "config", "--local", "--get", "core.hooksPath") == ".git/disabled-hooks"
    local_config = (output / ".git" / "config").read_text(encoding="utf-8")
    assert str(repo.resolve()).casefold() not in local_config.casefold()
    assert str(output.parent.resolve()).casefold() not in local_config.casefold()

    manifest = json.loads((output / public_export.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "source" not in manifest
    tracked_manifest = (output / public_export.MANIFEST_NAME).read_text(encoding="utf-8")
    assert result.source_commit not in tracked_manifest
    assert result.source_tree not in tracked_manifest
    assert _git(output, "log", "-1", "--format=%s") == "Initial public source snapshot"
    assert result.source_commit not in _git(output, "log", "-1", "--format=%B")
    assert manifest["inventory"]["excluded_files"] == 2
    assert manifest["inventory"]["excluded_by_rule"] == {"excluded_prefix:evidence/": 2}
    assert [item["path"] for item in manifest["files"] if item["path"].startswith("evidence/")] == [
        "evidence/README.md"
    ]
    assert set(POLICY["required_paths"]) <= {item["path"] for item in manifest["files"]}

    audit = _audit(output)
    assert audit["status"] == "ACCEPTED"
    assert audit["public_commit"] == result.public_commit
    assert {item["path"] for item in audit["excluded"]} == {
        "evidence/20260101-local-run/app.log",
        "evidence/20260101-local-run/settings.json",
    }


@pytest.mark.parametrize(
    ("path", "content", "rule"),
    [
        (
            "notes/user.md",
            "private path " + _windows_user_path("Alice", "Desktop\\x") + "\n",
            "windows_user_path",
        ),
        (
            "notes/identity.md",
            "private username: " + PRIVATE_USERNAME_CANARY + "\n",
            "forbidden_username",
        ),
        ("artifacts/tasks.db", b"SQLite format 3\x00", "forbidden_data_file"),
        ("artifacts/app.log", "a log\n", "forbidden_data_file"),
        ("artifacts/events.jsonl", "{}\n", "forbidden_data_file"),
        ("artifacts/.env", "SAFE_LOOKING=value\n", "secret_like_path"),
        (
            "notes/key.txt",
            PRIVATE_KEY_CANARY,
            "secret_content:private_key",
        ),
        (
            "notes/contact.md",
            "contact: alice" + "@example.com\n",
            "email_address",
        ),
        ("notes/network.md", "host: 10.24.3.9\n", "ip_address"),
        ("notes/network-v6.md", "host: fd12:3456:789a::42\n", "ip_address"),
        ("artifacts/report.json", '{"pid": 9090}\n', "raw_process_identifier"),
        ("artifacts/display.json", '{"device": "\\\\\\\\.\\\\DISPLAY1"}\n', "raw_display_identifier"),
    ],
)
def test_rejects_private_paths_files_and_raw_machine_evidence(
    tmp_path: Path,
    path: str,
    content: str | bytes,
    rule: str,
):
    repo = _repo(tmp_path, {path: content})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert not output.exists()
    assert rule in {item.rule for item in caught.value.violations}
    audit = _audit(output)
    assert audit["status"] == "REJECTED"
    assert rule in {item["rule"] for item in audit["violations"]}
    assert all("not-a-real-key" not in json.dumps(item) for item in audit["violations"])


def test_reserved_invalid_email_and_documentation_ip_are_allowed(tmp_path: Path):
    repo = _repo(
        tmp_path,
        {
            "notes/examples.md": (
                "contact: fixture@example.invalid; endpoints: 192.0.2.44 and 2001:db8::44\n"
            ),
            "scripts/version_info.txt": (
                'StringStruct("FileVersion", "1.2.0.0")\n'
                'StringStruct("ProductVersion", "1.2.0.0")\n'
            ),
        },
    )
    output = tmp_path / "public"

    public_export.export_snapshot(repo, output)

    assert output.is_dir()


def test_colon_heavy_text_does_not_stall_ipv6_privacy_scan(tmp_path: Path):
    repo = _repo(
        tmp_path,
        {"notes/colon-heavy.md": "field: value\n" * 20_000},
    )
    output = tmp_path / "public"

    public_export.export_snapshot(repo, output)

    assert output.is_dir()


def test_rejects_current_host_machine_name_without_persisting_it_in_policy(tmp_path: Path):
    machine_name = socket.gethostname()
    assert machine_name
    repo = _repo(tmp_path, {"notes/host.md": f"build host: {machine_name}\n"})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "forbidden_machine_name" in {item.rule for item in caught.value.violations}
    assert machine_name not in json.dumps(_audit(output))


@pytest.mark.parametrize(
    "policy_change",
    [
        {"additional_exclude_paths": ["README.md"]},
        {"additional_exclude_prefixes": ["docs/"]},
    ],
)
def test_policy_cannot_exclude_a_required_path(tmp_path: Path, policy_change: dict[str, list[str]]):
    policy = json.loads(json.dumps(POLICY))
    policy.update(policy_change)
    repo = _repo(
        tmp_path,
        {"config/public_snapshot.json": json.dumps(policy, indent=2) + "\n"},
    )
    output = tmp_path / "public"

    with pytest.raises(public_export.SnapshotError, match="required public paths may not be excluded"):
        public_export.export_snapshot(repo, output)

    assert not output.exists()


def test_required_path_must_pass_content_scan_to_be_included(tmp_path: Path):
    repo = _repo(
        tmp_path,
        {"README.md": "private path " + _windows_user_path("Alice", "Desktop\\x") + "\n"},
    )
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    rules = {item.rule for item in caught.value.violations}
    assert {"windows_user_path", "required_path_not_included"} <= rules


def test_rejects_this_export_hosts_absolute_source_path(tmp_path: Path):
    repo = _repo(tmp_path)
    readme = repo / "README.md"
    readme.write_text(f"local checkout: {repo.resolve()}\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "local path",
    )
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "local_absolute_path" in {item.rule for item in caught.value.violations}
    assert not output.exists()


def test_dirty_source_is_rejected_without_reading_untracked_content(tmp_path: Path):
    repo = _repo(tmp_path)
    canary = "DO-NOT-COPY-UNTRACKED-CONTENT"
    (repo / "untracked.txt").write_text(canary, encoding="utf-8")
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert {item.rule for item in caught.value.violations} == {"source_worktree_dirty"}
    assert not output.exists()
    assert canary not in public_export.default_audit_path(output).read_text(encoding="utf-8")


def test_archive_members_are_recursively_scanned(tmp_path: Path):
    package = _archive(
        {
            "petpack.json": b'{"schema_version":"1.0"}\n',
            "legal/source.txt": (_windows_user_path("Alice", "private.txt") + "\n").encode(),
        }
    )
    repo = _repo(tmp_path, {"assets/unsafe.petpack": package})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "windows_user_path" in {item.rule for item in caught.value.violations}
    assert any("unsafe.petpack!/legal/source.txt" in item.path for item in caught.value.violations)
    assert not output.exists()


@pytest.mark.parametrize(
    "chunk_type",
    [b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"iCCP"],
)
def test_png_privacy_metadata_is_rejected(tmp_path: Path, chunk_type: bytes):
    repo = _repo(tmp_path, {"assets/metadata.png": _png([(chunk_type, b"private-metadata")])})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "png_metadata_chunk" in {item.rule for item in caught.value.violations}
    assert not output.exists()


def test_unknown_png_ancillary_chunk_fails_closed(tmp_path: Path):
    repo = _repo(tmp_path, {"assets/metadata.png": _png([(b"raNd", b"opaque")])})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "png_unapproved_chunk" in {item.rule for item in caught.value.violations}


def test_malformed_png_fails_closed(tmp_path: Path):
    repo = _repo(tmp_path, {"assets/broken.png": public_export.PNG_SIGNATURE + b"truncated"})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "invalid_png" in {item.rule for item in caught.value.violations}


def test_petpack_png_and_ico_embedded_png_metadata_are_scanned(tmp_path: Path):
    private_png = _png([(b"tEXt", b"author=private")])
    package = _archive(
        {
            "petpack.json": b'{"schema_version":"1.0"}\n',
            "sprites/frame.png": private_png,
        }
    )
    repo = _repo(
        tmp_path,
        {
            "assets/metadata.petpack": package,
            "assets/metadata.ico": _ico_with_png(private_png),
        },
    )
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    metadata_paths = {
        item.path for item in caught.value.violations if item.rule == "png_metadata_chunk"
    }
    assert "assets/metadata.petpack!/sprites/frame.png" in metadata_paths
    assert "assets/metadata.ico!/image-0.png" in metadata_paths


def test_archive_traversal_and_nested_archives_fail_closed(tmp_path: Path):
    nested = _archive({"safe.txt": b"safe\n"})
    package = _archive({"../escape.txt": b"escape\n", "nested.zip": nested})
    repo = _repo(tmp_path, {"assets/unsafe.petpack": package})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    rules = {item.rule for item in caught.value.violations}
    assert {"unsafe_archive_path", "nested_archive"} <= rules
    assert not output.exists()


def test_archive_comments_fail_closed_instead_of_hiding_machine_data(tmp_path: Path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("petpack.json", b'{}\n')
        archive.comment = b"opaque-build-host-metadata"
    repo = _repo(tmp_path, {"assets/commented.petpack": stream.getvalue()})
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "archive_metadata" in {item.rule for item in caught.value.violations}


def test_public_tree_verification_accepts_snapshot_manifest_but_rejects_raw_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    repo = _repo(tmp_path)
    output = tmp_path / "public"
    public_export.export_snapshot(repo, output)

    verified = public_export.verify_public_tree(output)
    assert verified.source_commit == _git(output, "rev-parse", "HEAD")

    _write_files(output, {"evidence/local-run/settings.txt": "private runtime evidence\n"})
    _git(output, "add", "--all")
    _git(
        output,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "unsafe evidence",
    )

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.verify_public_tree(output)

    rules = {item.rule for item in caught.value.violations}
    assert "public_tree_excluded_path" in rules
    assert any(item.path == "evidence/local-run/settings.txt" for item in caught.value.violations)

    exit_code = public_export.main(
        ["--source-root", str(output), "--verify-public-tree"]
    )
    reported = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert reported["violation_count"] == sum(
        item["count"] for item in reported["violations"]
    )
    assert {
        "rule": "public_tree_excluded_path",
        "count": 1,
    } in reported["violations"]
    assert all(set(item) == {"rule", "count"} for item in reported["violations"])
    assert "private runtime evidence" not in json.dumps(reported)


def test_public_tree_verification_scans_committed_head_not_worktree(tmp_path: Path):
    source = _repo(tmp_path)
    repo = tmp_path / "public"
    public_export.export_snapshot(source, repo)
    committed_readme = (repo / "README.md").read_bytes()
    (repo / "README.md").write_text(
        _windows_user_path("UncommittedUser", "private.txt"),
        encoding="utf-8",
    )
    (repo / "untracked.txt").write_text(
        "DO-NOT-SCAN-UNTRACKED-CONTENT",
        encoding="utf-8",
    )

    result = public_export.verify_public_tree(repo)

    assert result.source_commit == _git(repo, "rev-parse", "HEAD^{commit}")
    assert committed_readme.startswith(b"# Safe public fixture")
    assert "UncommittedUser" not in _git(repo, "show", "HEAD:README.md")


def test_special_git_entry_and_unsafe_repository_path_are_rejected(tmp_path: Path):
    with pytest.raises(public_export.ExportRejected) as caught:
        public_export._validate_repo_path("safe/../escape.txt")
    assert caught.value.violations[0].rule == "unsafe_path"

    repo = _repo(tmp_path)
    blob = _git(repo, "hash-object", "-w", "--stdin", input_bytes=b"target\n")
    (repo / "link").write_text("target\n", encoding="utf-8")
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},link")
    _git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "symlink",
    )
    _git(repo, "config", "core.symlinks", "false")
    _git(repo, "reset", "--hard", "HEAD")
    output = tmp_path / "public"

    with pytest.raises(public_export.ExportRejected) as caught:
        public_export.export_snapshot(repo, output)

    assert "special_git_entry" in {item.rule for item in caught.value.violations}
    assert not output.exists()


def test_output_must_be_new_and_outside_source(tmp_path: Path):
    repo = _repo(tmp_path)
    inside = repo / "public"
    with pytest.raises(public_export.SnapshotError, match="outside"):
        public_export.export_snapshot(repo, inside)

    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(public_export.SnapshotError, match="already exists"):
        public_export.export_snapshot(repo, output)

    outside = tmp_path / "candidate"
    with pytest.raises(public_export.SnapshotError, match="audit report.*outside"):
        public_export.export_snapshot(repo, outside, audit_report=repo / "audit.json")


def test_cli_returns_two_and_emits_only_a_rejection_audit(tmp_path: Path):
    repo = _repo(tmp_path, {"notes/identity.md": PRIVATE_USERNAME_CANARY + "\n"})
    output = tmp_path / "public"

    exit_code = public_export.main(["--source-root", str(repo), "--output", str(output)])

    assert exit_code == 2
    assert not output.exists()
    assert _audit(output)["status"] == "REJECTED"


def test_ci_scans_real_checkout_before_installing_or_running_fixture_tests():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"
    ).read_text(encoding="utf-8")
    scan_command = (
        "python -I -B scripts/export_public_snapshot.py --source-root . --verify-public-tree"
    )

    assert "pull_request:" in workflow
    assert "pull_request_target" not in workflow
    assert scan_command in workflow
    assert workflow.index(scan_command) < workflow.index("pip install")
    assert workflow.index(scan_command) < workflow.index("pytest")
    assert "secrets." not in workflow


def test_committed_public_policy_does_not_persist_a_private_username_hash():
    policy_path = Path(__file__).resolve().parents[1] / "config" / "public_snapshot.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["forbidden_username_sha256"] == []
    assert {
        ".github/CODEOWNERS",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/workflows/tests.yml",
        "assets/LICENSE.md",
        "config/public_snapshot.json",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/LICENSING.md",
        "evidence/README.md",
        "pyproject.toml",
        "scripts/export_public_snapshot.py",
        "src/retirement_pet/__init__.py",
        "tests/test_public_snapshot.py",
    } <= set(policy["required_paths"])


def test_cr13_04_policy_excludes_luo_xiaohei_local_channel(tmp_path: Path):
    """User ruling (CR13-04), stated EXACTLY (review RR13-04): the ONLY
    Luo Xiaohei artefact in the tracked tree is the dedicated build
    script, and the REAL policy (unmodified) excludes it.  No fixture
    policy injection is involved."""
    repo_root = Path(__file__).resolve().parent.parent
    real_policy = json.loads(
        (repo_root / "config" / "public_snapshot.json").read_text("utf-8"))
    assert ("scripts/build_xiaohei_local.py"
            in real_policy["additional_exclude_paths"])

    # In the private repo the script exists and must be excluded; in a
    # PUBLIC snapshot it was already excluded upstream, so the fixture
    # simply omits it and the export must not resurrect it.
    in_private_repo = (repo_root / "scripts"
                       / "build_xiaohei_local.py").is_file()

    additions = {}
    if in_private_repo:
        additions["scripts/build_xiaohei_local.py"] = (
            b"import os\nimport sys\nfrom pathlib import Path\n"
            b"print('local-only builder')\n")
    root = _repo(tmp_path, additions)
    # embed the REAL production policy verbatim (not the fixture POLICY):
    # the exclusion under test lives in config/public_snapshot.json and
    # this is the unmodified file the exporter will read (RR13-04)
    (root / "config" / "public_snapshot.json").write_text(
        (repo_root / "config" / "public_snapshot.json").read_text(
            encoding="utf-8"), encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "embed real policy")
    output = tmp_path / "out"
    public_export.export_snapshot(root, output)
    exported = {p.relative_to(output).as_posix()
                for p in output.rglob("*") if p.is_file()
                and ".git" not in p.parts}
    assert "scripts/build_xiaohei_local.py" not in exported
