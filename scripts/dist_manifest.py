"""Validate a provenance-bound onedir artifact and write its inventory.

The artifact's embedded build identity is authoritative.  The current Git
HEAD is only a comparison input and can never be substituted for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retirement_pet.build_info import (  # noqa: E402
    BUILD_ATTESTATION_PROTOCOL,
    BuildInfoError,
    validate_build_attestation,
    validate_build_info,
)

DEFAULT_DIST = ROOT / "dist" / "RetirementPet"
HARD_CAP_BYTES = 200 * (1 << 20)
HARD_FILE_CAP = 5_000
_EXPECTED_QT = {
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll",
    "Qt6Multimedia.dll", "Qt6OpenGL.dll", "Qt6Svg.dll", "opengl32sw.dll",
}
_FORBIDDEN_NAMES = {
    "Qt6Bluetooth.dll", "Qt6Location.dll", "Qt6Nfc.dll",
    "Qt6Positioning.dll", "Qt6WebChannel.dll", "Qt6WebSockets.dll",
}
_REQUIRED_FILES = {
    "RetirementPet.exe",
    "_internal/build-info.json",
    "_internal/assets/manifest.json",
    "_internal/assets/icons/retirement_pet.png",
    "_internal/assets/petpack/retirement-cat-official.petpack",
    "_internal/assets/petpack/retirement-cat-official-1.0.1.petpack",
    "_internal/assets/petpack/examples/realistic-retirement-cat-0.1.1.petpack",
    "_internal/config/defaults.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def current_source(root: Path = ROOT) -> tuple[str | None, str | None, bool]:
    commit = _git_value(root, "rev-parse", "HEAD")
    tree = _git_value(root, "rev-parse", "HEAD^{tree}")
    status = _git_value(
        root, "status", "--porcelain", "--untracked-files=all")
    return commit, tree, status == "" if status is not None else False


def load_embedded_identity(dist: Path) -> dict:
    path = dist / "_internal" / "build-info.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildInfoError("artifact build info is unreadable") from exc
    return validate_build_info(value)


def _is_pe_executable(path: Path) -> bool:
    """Reject malformed Windows executables before invoking CreateProcess."""
    try:
        with path.open("rb") as handle:
            header = handle.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(header[0x3C:0x40], "little")
            if pe_offset < 64 or pe_offset > 16 * (1 << 20):
                return False
            handle.seek(pe_offset)
            return handle.read(4) == b"PE\0\0"
    except OSError:
        return False


def probe_runtime_identity(dist: Path) -> tuple[dict | None, str | None]:
    """Require the v2 attestation that legacy EXEs do not understand."""
    exe = dist / "RetirementPet.exe"
    if not exe.is_file():
        return None, "artifact executable is missing"
    if not _is_pe_executable(exe):
        return None, "artifact executable is not a valid PE image"
    with tempfile.TemporaryDirectory(prefix="retirement-pet-identity-") as raw:
        run_dir = Path(raw)
        output = run_dir / "runtime-attestation-v2.json"
        import os
        env = dict(os.environ)
        env["RETIREMENT_PET_DATA_DIR"] = str(run_dir / "data")
        env["RETIREMENT_PET_INSTANCE_NAME"] = \
            f"identity-{run_dir.name}"
        env.pop("QT_QPA_PLATFORM", None)
        try:
            result = subprocess.run(
                [str(exe), "--build-attestation-v2-out", str(output)],
                cwd=run_dir,
                env=env, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, "frozen runtime identity probe failed"
        if result.returncode != 0 or not output.is_file():
            return None, "frozen runtime identity probe failed"
        try:
            value = json.loads(output.read_text(encoding="utf-8"))
            attestation = validate_build_attestation(value)
            return attestation["compiled_identity"], None
        except (OSError, UnicodeError, json.JSONDecodeError,
                BuildInfoError):
            return None, "frozen runtime identity probe was invalid"


def validate_distribution(
        dist: Path, *, expected_commit: str | None,
        expected_build_id: str | None, source_commit: str | None,
        source_clean: bool, source_tree: str | None = None,
        expected_artifact_id: str | None = None,
        expected_exe_sha256: str | None = None,
        expected_file_count: int | None = None,
        expected_total_bytes: int | None = None,
        runtime_identity: dict | None = None,
        runtime_probe_error: str | None = None,
        require_runtime: bool = True) \
        -> tuple[dict, list[str], list[str]]:
    """Return ``(manifest, inventory_lines, failures)`` for ``dist``."""
    failures: list[str] = []
    if not dist.is_dir():
        return ({"result": "FAIL", "dist_dir": str(dist)}, [],
                ["distribution directory is missing"])

    try:
        build_info = load_embedded_identity(dist)
    except BuildInfoError as exc:
        build_info = None
        failures.append(str(exc))

    if require_runtime:
        if runtime_probe_error:
            failures.append(runtime_probe_error)
        elif runtime_identity is None:
            failures.append("frozen runtime identity was not verified")
        elif build_info is not None and runtime_identity != build_info:
            failures.append(
                "frozen runtime identity differs from external metadata")

    contributions: dict[str, int] = defaultdict(int)
    inventory: list[str] = []
    total = 0
    relative_names: set[str] = set()
    reparse_paths: list[str] = []
    for path in sorted(dist.rglob("*")):
        stat = path.lstat()
        file_attributes = getattr(stat, "st_file_attributes", 0)
        if path.is_symlink() or file_attributes & 0x400:
            reparse_paths.append(path.relative_to(dist).as_posix())
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(dist).as_posix()
        relative_names.add(rel)
        size = path.stat().st_size
        total += size
        parts = Path(rel).parts
        bucket = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        contributions[bucket] += size
        inventory.append(f"{sha256(path)}  {rel}")

    if reparse_paths:
        failures.append(
            f"reparse points are forbidden: {', '.join(reparse_paths)}")

    missing = sorted(_REQUIRED_FILES - relative_names)
    if missing:
        failures.append(f"required files missing: {', '.join(missing)}")
    if len(inventory) > HARD_FILE_CAP:
        failures.append(
            f"file count {len(inventory)} exceeds hard cap {HARD_FILE_CAP}")
    if total > HARD_CAP_BYTES:
        failures.append(
            f"size {total} exceeds hard cap {HARD_CAP_BYTES}")

    forbidden_names = {name.casefold() for name in _FORBIDDEN_NAMES}
    forbidden = sorted(
        rel for rel in relative_names
        if Path(rel).name.casefold() in forbidden_names)
    if forbidden:
        failures.append(f"forbidden runtime files present: {', '.join(forbidden)}")

    qt_dlls = sorted({
        path.name for path in dist.rglob("*")
        if path.is_file() and path.name.casefold().startswith("qt6")
        and path.suffix.casefold() == ".dll"
    })
    expected_qt = {name.casefold() for name in _EXPECTED_QT}
    unexplained = [name for name in qt_dlls
                   if name.casefold() not in expected_qt]
    if unexplained:
        failures.append(f"unexplained Qt DLLs: {', '.join(unexplained)}")

    if build_info is not None:
        if expected_commit and build_info["commit"] != expected_commit:
            failures.append("artifact commit differs from expected commit")
        if expected_build_id and build_info["build_id"] != expected_build_id:
            failures.append("artifact build id differs from expected build id")
        if source_commit and build_info["commit"] != source_commit:
            failures.append("artifact commit differs from current source HEAD")
        if source_tree and build_info["git_tree"] != source_tree:
            failures.append("artifact tree differs from current source tree")
    if not source_clean:
        failures.append("current source worktree is not clean")

    inventory_bytes = ("\n".join(inventory) + "\n").encode("utf-8")
    dist_digest = hashlib.sha256(inventory_bytes).hexdigest()
    exe = dist / "RetirementPet.exe"
    exe_digest = sha256(exe) if exe.is_file() else None
    if expected_artifact_id and dist_digest != expected_artifact_id:
        failures.append("artifact inventory differs from expected artifact id")
    if expected_exe_sha256 and exe_digest != expected_exe_sha256:
        failures.append("artifact executable differs from expected digest")
    if expected_file_count is not None and len(inventory) != expected_file_count:
        failures.append("artifact file count differs from expected count")
    if expected_total_bytes is not None and total != expected_total_bytes:
        failures.append("artifact byte count differs from expected total")
    manifest = {
        "schema": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "result": "PASS" if not failures else "FAIL",
        "artifact": build_info,
        "runtime_identity_verified": (
            build_info is not None and runtime_identity == build_info),
        "attestation_protocol": BUILD_ATTESTATION_PROTOCOL
        if build_info is not None and runtime_identity == build_info else None,
        "source_comparison": {
            "commit": source_commit,
            "git_tree": source_tree,
            "worktree_clean": source_clean,
        },
        "dist_dir_name": dist.name,
        "dist_tree_sha256": dist_digest,
        "artifact_id": dist_digest,
        "exe_sha256": exe_digest,
        "total_bytes": total,
        "total_mib": round(total / (1 << 20), 2),
        "file_count": len(inventory),
        "contributions_mib": {
            bucket: round(size / (1 << 20), 2)
            for bucket, size in sorted(
                contributions.items(), key=lambda item: -item[1])
        },
        "qt_dlls_present": qt_dlls,
        "hard_limits": {
            "total_bytes": HARD_CAP_BYTES,
            "file_count": HARD_FILE_CAP,
        },
        "required_files": sorted(_REQUIRED_FILES),
        "failures": failures,
    }
    return manifest, inventory, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-build-id")
    parser.add_argument("--expected-artifact-id")
    parser.add_argument("--expected-exe-sha256")
    parser.add_argument("--expected-file-count", type=int)
    parser.add_argument("--expected-total-bytes", type=int)
    parser.add_argument("--evidence-root", type=Path,
                        default=ROOT / "evidence")
    parser.add_argument("--git-root", type=Path, default=ROOT)
    args = parser.parse_args()

    inventory_expectations = (
        args.expected_artifact_id,
        args.expected_exe_sha256,
        args.expected_file_count,
        args.expected_total_bytes,
    )
    if any(value is not None for value in inventory_expectations) \
            and not all(value is not None for value in inventory_expectations):
        parser.error(
            "expected artifact id, EXE digest, file count, and byte count "
            "must be supplied together")

    source_commit, source_tree, source_clean = current_source(
        args.git_root.resolve())
    dist = args.dist.resolve()
    validation_args = dict(
        expected_commit=args.expected_commit,
        expected_build_id=args.expected_build_id,
        expected_artifact_id=args.expected_artifact_id,
        expected_exe_sha256=args.expected_exe_sha256,
        expected_file_count=args.expected_file_count,
        expected_total_bytes=args.expected_total_bytes,
        source_commit=source_commit, source_clean=source_clean,
        source_tree=source_tree,
    )
    # This first pass is intentionally data-only.  Never let an unbound
    # onedir artifact load one of its DLLs before its entire inventory agrees
    # with the trusted receipt supplied by the caller.
    manifest, inventory, failures = validate_distribution(
        dist, require_runtime=False, **validation_args)
    if not failures:
        runtime_identity, runtime_probe_error = probe_runtime_identity(dist)
        # Re-scan after the probe as a final mutation check.  The initial
        # static pass is the security boundary; this second pass binds the
        # emitted evidence to the post-probe bytes as well.
        manifest, inventory, failures = validate_distribution(
            dist, runtime_identity=runtime_identity,
            runtime_probe_error=runtime_probe_error, **validation_args)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    build_prefix = "invalid"
    if isinstance(manifest.get("artifact"), dict):
        build_prefix = manifest["artifact"]["build_id"][:12]
    out_dir = args.evidence_root / f"{stamp}-{build_prefix}-dist-manifest"
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "files.sha256").write_text(
        "\n".join(inventory) + "\n", encoding="utf-8")
    artifact = manifest.get("artifact")
    receipt = {
        "schema": 1,
        "result": manifest["result"],
        "recipe_id": artifact.get("build_id")
        if isinstance(artifact, dict) else None,
        "artifact_id": manifest.get("artifact_id"),
        "exe_sha256": manifest.get("exe_sha256"),
        "commit": artifact.get("commit")
        if isinstance(artifact, dict) else None,
        "git_tree": artifact.get("git_tree")
        if isinstance(artifact, dict) else None,
        "file_count": manifest.get("file_count"),
        "total_bytes": manifest.get("total_bytes"),
        "attestation_protocol": manifest.get("attestation_protocol"),
    }
    (out_dir / "release-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "result": manifest["result"],
        "build_id": manifest.get("artifact", {}).get("build_id")
        if isinstance(manifest.get("artifact"), dict) else None,
        "dist_tree_sha256": manifest.get("dist_tree_sha256"),
        "artifact_id": manifest.get("artifact_id"),
        "exe_sha256": manifest.get("exe_sha256"),
        "total_mib": manifest.get("total_mib"),
        "file_count": manifest.get("file_count"),
        "failures": failures,
        "evidence": str(out_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
