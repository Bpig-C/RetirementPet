"""Generate and validate immutable RetirementPet build identity.

``build_lock.json`` is deliberately machine-neutral and safe to publish.  A
formal release additionally requires a private, exact toolchain attestation
under ``.release``.  The artifact embeds only the attestation's irreversible
digest; readable machine details never enter tracked source or the bundle.
This script never installs packages.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import pprint
import shutil
import stat
import subprocess
import sys
import sysconfig
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retirement_pet.build_info import (  # noqa: E402
    compute_build_id,
    validate_build_info,
)

LOCK_PATH = ROOT / "scripts" / "build_lock.json"
LOCAL_ATTESTATION_PATH = ROOT / ".release" / "toolchain-attestation.json"
PUBLIC_LOCK_SCHEMA = 2
LOCAL_ATTESTATION_SCHEMA = 1
_SHA256_HEX = frozenset("0123456789abcdef")
_BUILD_INPUT_PATHS = (
    "src",
    "assets",
    "character-work",
    "config",
    "tests",
    "scripts",
    "RetirementPet.spec",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)


class ReleaseIdentityError(RuntimeError):
    """A release identity precondition was not met."""


def _normalized_package(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise ReleaseIdentityError("Git metadata is unavailable")
    return result.stdout.strip()


def git_identity(root: Path = ROOT) -> dict[str, object]:
    status = _run_git(root, "status", "--porcelain", "--untracked-files=all")
    tagged = _run_git(root, "ls-files", "-v")
    special_index_paths = [
        line[2:] for line in tagged.splitlines()
        if len(line) > 2 and (line[0].islower() or line[0] == "S")
    ]
    return {
        "commit": _run_git(root, "rev-parse", "HEAD"),
        "git_tree": _run_git(root, "rev-parse", "HEAD^{tree}"),
        "source_clean": not bool(status) and not special_index_paths,
        "dirty_entries": status.splitlines(),
        "special_index_paths": special_index_paths,
    }


def load_lock(path: Path = LOCK_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIdentityError("build lock is unreadable") from exc
    return validate_public_lock(value)


def _is_sha256(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value).issubset(_SHA256_HEX))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def validate_public_lock(value: object) -> dict:
    """Reject private or unknown fields from the tracked logical lock."""
    if not isinstance(value, dict) or set(value) != {
            "schema", "python", "packages", "packaging", "qt_version"} \
            or value.get("schema") != PUBLIC_LOCK_SCHEMA:
        raise ReleaseIdentityError("unsupported public build lock")
    python_lock = value.get("python")
    if not isinstance(python_lock, dict) or set(python_lock) != {
            "version", "implementation", "architecture"} \
            or any(not isinstance(python_lock.get(key), str)
                   or not python_lock[key]
                   for key in python_lock):
        raise ReleaseIdentityError("public Python lock is malformed")
    packages = value.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ReleaseIdentityError("public package lock is malformed")
    normalized: dict[str, str] = {}
    for name, version in packages.items():
        if not isinstance(name, str) or not isinstance(version, str) \
                or not name or not version:
            raise ReleaseIdentityError("public package lock is malformed")
        canonical_name = _normalized_package(name)
        if canonical_name != name or canonical_name in normalized:
            raise ReleaseIdentityError("public package names are not canonical")
        normalized[canonical_name] = version
    if value.get("packaging") != {"mode": "onedir", "upx": False}:
        raise ReleaseIdentityError("public packaging lock is unsupported")
    if not isinstance(value.get("qt_version"), str) \
            or not value["qt_version"]:
        raise ReleaseIdentityError("public Qt lock is malformed")
    # A JSON round-trip is a defensive deep copy without accepting custom
    # mappings or retaining references supplied by callers.
    return json.loads(json.dumps(value))


def public_lock_digest(lock: object) -> str:
    """Bind logical lock content independent of whitespace or line endings."""
    validated = validate_public_lock(lock)
    return hashlib.sha256(_canonical_json(validated)).hexdigest()


def installed_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            packages[_normalized_package(name)] = distribution.version
    return dict(sorted(packages.items()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_fingerprint(
        root: Path, *, excluded_parts: frozenset[str] = frozenset()) \
        -> dict[str, object]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts \
                or excluded_parts.intersection(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() in {".pyc", ".pyo"}:
            raise ReleaseIdentityError(
                f"legacy sourceless bytecode is forbidden: {rel}")
        file_digest = _sha256(path)
        size = path.stat().st_size
        digest.update(f"{file_digest}  {rel}\n".encode("utf-8"))
        count += 1
        total += size
    return {"sha256": digest.hexdigest(), "file_count": count,
            "total_bytes": total}


def toolchain_fingerprints() -> dict[str, object]:
    runtime_dll = Path(sys.base_prefix) / (
        f"python{sys.version_info.major}{sys.version_info.minor}.dll")
    site_packages = Path(sysconfig.get_paths()["purelib"])
    if not runtime_dll.is_file() or not site_packages.is_dir():
        raise ReleaseIdentityError("Python runtime files are incomplete")
    base = Path(sys.base_prefix)
    git_exe_raw = shutil.which("git")
    if not git_exe_raw or not Path(git_exe_raw).is_file():
        raise ReleaseIdentityError("Git executable is unavailable")
    git_version = subprocess.run(
        [git_exe_raw, "--version"], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    if git_version.returncode != 0:
        raise ReleaseIdentityError("Git version is unavailable")
    base_runtime_roots = [
        base / "DLLs",
        base / "Library" / "bin",
    ]
    base_runtime = {
        path.relative_to(base).as_posix(): _tree_fingerprint(path)
        for path in base_runtime_roots if path.is_dir()
    }
    return {
        "python_executable_sha256": _sha256(Path(sys.executable)),
        "python_runtime_dll_sha256": _sha256(runtime_dll),
        "site_packages": _tree_fingerprint(site_packages),
        "base_stdlib": _tree_fingerprint(
            base / "Lib", excluded_parts=frozenset({"site-packages"})),
        "base_runtime": base_runtime,
        "git": {
            "version": git_version.stdout.strip(),
            "exe_sha256": _sha256(Path(git_exe_raw)),
        },
    }


def exact_toolchain_snapshot() -> dict[str, object]:
    """Capture private, byte-exact release machinery without path strings."""
    from PySide6.QtCore import qVersion

    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "architecture": platform.architecture()[0],
            "compiler": platform.python_compiler(),
            "distribution": sys.version,
            "machine": platform.machine(),
        },
        "qt_version": qVersion(),
        "packages": installed_packages(),
        "fingerprints": toolchain_fingerprints(),
    }


def verify_toolchain(lock: dict | None = None) -> list[str]:
    """Verify only the public, machine-neutral logical constraints."""
    lock = validate_public_lock(lock) if lock is not None else load_lock()
    failures: list[str] = []
    python_lock = lock.get("python", {})
    if platform.python_version() != python_lock.get("version"):
        failures.append("Python version differs from build lock")
    if platform.python_implementation() != python_lock.get("implementation"):
        failures.append("Python implementation differs from build lock")
    if platform.architecture()[0] != python_lock.get("architecture"):
        failures.append("Python architecture differs from build lock")
    expected = {
        _normalized_package(name): str(version)
        for name, version in lock.get("packages", {}).items()
    }
    actual = installed_packages()
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            name for name in set(actual) & set(expected)
            if actual[name] != expected[name]
        )
        if missing:
            failures.append(f"locked packages missing: {', '.join(missing)}")
        if extra:
            failures.append(f"unlocked packages present: {', '.join(extra)}")
        if changed:
            failures.append(f"package versions differ: {', '.join(changed)}")
    packaging_lock = lock.get("packaging", {})
    if packaging_lock != {"mode": "onedir", "upx": False}:
        failures.append("packaging lock must freeze onedir with UPX disabled")
    try:
        from PySide6.QtCore import qVersion
        qt_version = qVersion()
    except Exception:  # noqa: BLE001
        qt_version = "unavailable"
    if qt_version != lock.get("qt_version"):
        failures.append("Qt runtime version differs from build lock")
    return failures


def _validate_tree_fingerprint(value: object, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "sha256", "file_count", "total_bytes"} \
            or not _is_sha256(value.get("sha256")) \
            or not isinstance(value.get("file_count"), int) \
            or value["file_count"] < 0 \
            or not isinstance(value.get("total_bytes"), int) \
            or value["total_bytes"] < 0:
        raise ReleaseIdentityError(f"{label} fingerprint is malformed")
    return dict(value)


def validate_exact_toolchain(value: object) -> dict:
    """Validate the private exact snapshot before it influences a build."""
    if not isinstance(value, dict) or set(value) != {
            "python", "qt_version", "packages", "fingerprints"}:
        raise ReleaseIdentityError("exact toolchain snapshot is malformed")
    python_info = value.get("python")
    if not isinstance(python_info, dict) or set(python_info) != {
            "version", "implementation", "architecture", "compiler",
            "distribution", "machine"} \
            or any(not isinstance(item, str) or not item
                   for item in python_info.values()):
        raise ReleaseIdentityError("exact Python snapshot is malformed")
    packages = value.get("packages")
    if not isinstance(packages, dict) or any(
            not isinstance(name, str) or not name
            or _normalized_package(name) != name
            or not isinstance(version, str) or not version
            for name, version in packages.items()):
        raise ReleaseIdentityError("exact package snapshot is malformed")
    if not isinstance(value.get("qt_version"), str) \
            or not value["qt_version"]:
        raise ReleaseIdentityError("exact Qt snapshot is malformed")
    fingerprints = value.get("fingerprints")
    if not isinstance(fingerprints, dict) or set(fingerprints) != {
            "python_executable_sha256", "python_runtime_dll_sha256",
            "site_packages", "base_stdlib", "base_runtime", "git"} \
            or not _is_sha256(fingerprints.get("python_executable_sha256")) \
            or not _is_sha256(fingerprints.get("python_runtime_dll_sha256")):
        raise ReleaseIdentityError("exact toolchain fingerprints are malformed")
    _validate_tree_fingerprint(
        fingerprints.get("site_packages"), "site-packages")
    _validate_tree_fingerprint(fingerprints.get("base_stdlib"), "stdlib")
    base_runtime = fingerprints.get("base_runtime")
    if not isinstance(base_runtime, dict) or any(
            not isinstance(name, str) or not name
            or "\\" in name or name.startswith("/")
            for name in base_runtime):
        raise ReleaseIdentityError("base runtime fingerprints are malformed")
    for name, fingerprint in base_runtime.items():
        _validate_tree_fingerprint(fingerprint, f"base runtime {name}")
    git_info = fingerprints.get("git")
    if not isinstance(git_info, dict) or set(git_info) != {
            "version", "exe_sha256"} \
            or not isinstance(git_info.get("version"), str) \
            or not git_info["version"] \
            or not _is_sha256(git_info.get("exe_sha256")):
        raise ReleaseIdentityError("Git fingerprint is malformed")
    return json.loads(json.dumps(value))


def create_toolchain_attestation(
        lock: dict | None = None) -> dict[str, object]:
    """Create a private exact proof after the public lock has passed."""
    lock = validate_public_lock(lock) if lock is not None else load_lock()
    failures = verify_toolchain(lock)
    if failures:
        raise ReleaseIdentityError("; ".join(failures))
    return {
        "schema": LOCAL_ATTESTATION_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "public_lock_sha256": public_lock_digest(lock),
        "toolchain": validate_exact_toolchain(exact_toolchain_snapshot()),
    }


def validate_toolchain_attestation(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "schema", "generated_at_utc", "public_lock_sha256", "toolchain"} \
            or value.get("schema") != LOCAL_ATTESTATION_SCHEMA \
            or not _is_sha256(value.get("public_lock_sha256")):
        raise ReleaseIdentityError("local toolchain attestation is malformed")
    try:
        generated = datetime.fromisoformat(str(value.get("generated_at_utc")))
    except ValueError as exc:
        raise ReleaseIdentityError(
            "local toolchain attestation timestamp is malformed") from exc
    if generated.tzinfo is None:
        raise ReleaseIdentityError(
            "local toolchain attestation timestamp lacks a timezone")
    return {
        "schema": LOCAL_ATTESTATION_SCHEMA,
        "generated_at_utc": str(value["generated_at_utc"]),
        "public_lock_sha256": str(value["public_lock_sha256"]),
        "toolchain": validate_exact_toolchain(value.get("toolchain")),
    }


def load_toolchain_attestation(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIdentityError(
            "local toolchain attestation is unreadable; run "
            "attest-toolchain first") from exc
    return validate_toolchain_attestation(value)


def toolchain_attestation_digest(attestation: object) -> str:
    """Return a high-entropy digest over the complete canonical proof."""
    validated = validate_toolchain_attestation(attestation)
    return hashlib.sha256(_canonical_json(validated)).hexdigest()


def verify_release_toolchain(
        lock_path: Path, attestation_path: Path,
        expected_attestation_sha256: str | None = None,
        ) -> tuple[list[str], str | None]:
    """Verify public constraints and the private exact release proof."""
    lock = load_lock(lock_path)
    failures = verify_toolchain(lock)
    try:
        attestation = load_toolchain_attestation(attestation_path)
    except ReleaseIdentityError as exc:
        return [str(exc)], None
    digest = toolchain_attestation_digest(attestation)
    if attestation["public_lock_sha256"] != public_lock_digest(lock):
        failures.append("local attestation is for a different public lock")
    try:
        observed = validate_exact_toolchain(exact_toolchain_snapshot())
    except ReleaseIdentityError as exc:
        failures.append(str(exc))
    else:
        if observed != attestation["toolchain"]:
            failures.append(
                "exact toolchain differs from local attestation")
    if expected_attestation_sha256 is not None \
            and digest != expected_attestation_sha256:
        failures.append("local attestation differs from expected digest")
    return failures, digest


def public_toolchain_identity(
        lock: dict, attestation_sha256: str) -> dict[str, object]:
    """Metadata safe to embed in a public artifact."""
    lock = validate_public_lock(lock)
    if not _is_sha256(attestation_sha256):
        raise ReleaseIdentityError("invalid local attestation digest")
    return {
        "public_lock_sha256": public_lock_digest(lock),
        "attestation_sha256": attestation_sha256,
        "python_version": lock["python"]["version"],
        "python_implementation": lock["python"]["implementation"],
        "python_architecture": lock["python"]["architecture"],
        "qt_version": lock["qt_version"],
        "packages": dict(lock["packages"]),
    }


def tracked_build_inputs(
        git_root: Path = ROOT, source_root: Path | None = None) -> list[Path]:
    source_root = source_root or git_root
    output = _run_git(git_root, "ls-files", "-z", "--", *_BUILD_INPUT_PATHS)
    names = [name for name in output.split("\0") if name]
    paths = [source_root / Path(name) for name in names]
    if not paths or any(not path.is_file() for path in paths):
        raise ReleaseIdentityError("tracked build input inventory is incomplete")
    return sorted(
        paths, key=lambda path: path.relative_to(source_root).as_posix())


def _git_blob_oid(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _enumerated_build_inputs(source_root: Path) -> list[Path]:
    paths: set[Path] = set()
    for relative in _BUILD_INPUT_PATHS:
        candidate = source_root / relative
        if candidate.is_dir():
            paths.update(path for path in candidate.rglob("*") if path.is_file())
        elif candidate.is_file():
            paths.add(candidate)
    return sorted(paths, key=lambda path: path.relative_to(source_root).as_posix())


def verify_source_snapshot(
        git_root: Path, source_root: Path, commit: str) -> list[str]:
    """Prove every test/build input byte equals its committed Git blob."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit, "--", *_BUILD_INPUT_PATHS],
        cwd=git_root, capture_output=True, check=False,
    )
    if result.returncode != 0:
        return ["unable to enumerate committed build inputs"]
    expected: dict[str, str] = {}
    try:
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            metadata, name = raw.split(b"\t", 1)
            _mode, object_type, oid = metadata.split(b" ", 2)
            if object_type == b"blob":
                expected[name.decode("utf-8")] = oid.decode("ascii")
    except (ValueError, UnicodeError):
        return ["committed build input listing is malformed"]
    failures: list[str] = []
    actual_paths = _enumerated_build_inputs(source_root)
    actual_names = {
        path.relative_to(source_root).as_posix(): path
        for path in actual_paths
    }
    missing = sorted(set(expected) - set(actual_names))
    extra = sorted(set(actual_names) - set(expected))
    if missing:
        failures.append(f"snapshot inputs missing: {', '.join(missing)}")
    if extra:
        failures.append(f"snapshot has extra inputs: {', '.join(extra)}")
    changed = sorted(
        name for name in set(expected) & set(actual_names)
        if _git_blob_oid(actual_names[name]) != expected[name]
    )
    if changed:
        failures.append(f"snapshot inputs differ from Git blobs: {', '.join(changed)}")
    return failures


def input_inventory(
        paths: Iterable[Path], root: Path = ROOT) -> dict[str, object]:
    files = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in paths
    }
    canonical = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(files.items())
    ).encode("utf-8")
    return {
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def project_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def create_build_info(
        git_root: Path = ROOT, source_root: Path | None = None,
        attestation_path: Path | None = None) \
        -> dict[str, object]:
    source_root = source_root or git_root
    if attestation_path is None:
        raise ReleaseIdentityError(
            "formal release generation requires a local toolchain "
            "attestation")
    identity = git_identity(git_root)
    if not identity["source_clean"]:
        raise ReleaseIdentityError("release build requires a clean worktree")
    snapshot_failures = verify_source_snapshot(
        git_root, source_root, str(identity["commit"]))
    if snapshot_failures:
        raise ReleaseIdentityError("; ".join(snapshot_failures))
    lock_path = source_root / "scripts" / "build_lock.json"
    lock = load_lock(lock_path)
    failures, attestation_sha256 = verify_release_toolchain(
        lock_path, attestation_path)
    if failures:
        raise ReleaseIdentityError("; ".join(failures))
    if attestation_sha256 is None:
        raise ReleaseIdentityError("local toolchain attestation has no digest")
    info: dict[str, object] = {
        "schema": 1,
        "artifact": "RetirementPet",
        "artifact_kind": "onedir",
        "version": project_version(source_root),
        "commit": identity["commit"],
        "git_tree": identity["git_tree"],
        "source_clean": True,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": input_inventory(
            tracked_build_inputs(git_root, source_root), source_root),
        "toolchain": public_toolchain_identity(lock, attestation_sha256),
        "packaging": {"mode": "onedir", "upx": False,
                      "spec": "RetirementPet.spec",
                      "source": "git-archive"},
    }
    info["build_id"] = compute_build_id(info)
    return validate_build_info(info)


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_python_module_atomic(path: Path, info: dict[str, object]) -> None:
    """Write the frozen-only identity module included in the PYZ archive."""
    validated = validate_build_info(info)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    body = (
        '"""Generated release identity; do not edit."""\n\n'
        f"BUILD_INFO = {pprint.pformat(validated, sort_dicts=True)}\n"
    )
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def _private_attestation_path(path: Path) -> Path:
    """Keep readable machine details below the ignored release root."""
    raw_release_root = ROOT / ".release"
    if raw_release_root.exists():
        attributes = getattr(
            raw_release_root.lstat(), "st_file_attributes", 0)
        if raw_release_root.is_symlink() or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ReleaseIdentityError(
                ".release must not be a symbolic link or reparse point")
    release_root = raw_release_root.resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(release_root)
    except ValueError as exc:
        raise ReleaseIdentityError(
            "toolchain attestation must stay under .release") from exc
    if candidate.exists() and candidate.is_symlink():
        raise ReleaseIdentityError(
            "toolchain attestation must not be a symbolic link")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("assert-clean")
    subparsers.add_parser("verify-public-lock")
    verify_tool = subparsers.add_parser("verify-toolchain")
    verify_tool.add_argument(
        "--attestation", type=Path, default=LOCAL_ATTESTATION_PATH)
    verify_tool.add_argument("--expected-attestation-sha256")
    attest = subparsers.add_parser("attest-toolchain")
    attest.add_argument(
        "--output", type=Path, default=LOCAL_ATTESTATION_PATH)
    verify_snapshot = subparsers.add_parser("verify-snapshot")
    verify_snapshot.add_argument("--source-root", type=Path, required=True)
    verify_snapshot.add_argument("--git-root", type=Path, default=ROOT)
    verify_snapshot.add_argument("--commit", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--module-output", type=Path, required=True)
    generate.add_argument("--source-root", type=Path, default=ROOT)
    generate.add_argument("--git-root", type=Path, default=ROOT)
    generate.add_argument(
        "--attestation", type=Path, default=LOCAL_ATTESTATION_PATH)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "assert-clean":
            if not git_identity()["source_clean"]:
                raise ReleaseIdentityError(
                    "release build requires a clean worktree")
            print("clean worktree")
        elif args.command == "verify-public-lock":
            failures = verify_toolchain()
            if failures:
                raise ReleaseIdentityError("; ".join(failures))
            print("toolchain matches public scripts/build_lock.json")
        elif args.command == "verify-toolchain":
            attestation_path = _private_attestation_path(args.attestation)
            failures, digest = verify_release_toolchain(
                LOCK_PATH, attestation_path,
                args.expected_attestation_sha256)
            if failures:
                raise ReleaseIdentityError("; ".join(failures))
            print(json.dumps({
                "result": "PASS",
                "public_lock_sha256": public_lock_digest(load_lock()),
                "attestation_sha256": digest,
            }, sort_keys=True))
        elif args.command == "attest-toolchain":
            output = _private_attestation_path(args.output)
            attestation = create_toolchain_attestation()
            write_json_atomic(output, attestation)
            print(json.dumps({
                "result": "CREATED",
                "public_lock_sha256": attestation["public_lock_sha256"],
                "attestation_sha256": toolchain_attestation_digest(
                    attestation),
            }, sort_keys=True))
        elif args.command == "verify-snapshot":
            failures = verify_source_snapshot(
                args.git_root.resolve(), args.source_root.resolve(),
                args.commit)
            if failures:
                raise ReleaseIdentityError("; ".join(failures))
            print("archived source matches committed Git blobs")
        elif args.command == "generate":
            attestation_path = _private_attestation_path(args.attestation)
            info = create_build_info(
                args.git_root.resolve(), args.source_root.resolve(),
                attestation_path)
            write_json_atomic(args.output, info)
            write_python_module_atomic(args.module_output, info)
            print(json.dumps({key: info[key] for key in
                              ("build_id", "commit", "git_tree", "version")},
                             sort_keys=True))
        else:
            value = json.loads(args.path.read_text(encoding="utf-8"))
            info = validate_build_info(value)
            print(json.dumps({key: info[key] for key in
                              ("build_id", "commit", "git_tree", "version")},
                             sort_keys=True))
        return 0
    except (ReleaseIdentityError, OSError, UnicodeError,
            json.JSONDecodeError, ValueError) as exc:
        print(f"release identity failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
