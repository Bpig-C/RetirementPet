"""petpack author toolchain (PETPACK_SPEC 23; ADR-V2-018).

Commands (the M4 lifecycle install-local is deliberately absent: the author
tool never writes a LibraryRoot):

    python scripts/petpack_cli.py init <dir>
    python scripts/petpack_cli.py build <source_dir> <out.petpack>
    python scripts/petpack_cli.py validate <out.petpack>
    python scripts/petpack_cli.py preflight <out.petpack>
    python scripts/petpack_cli.py inspect <out.petpack>

``build`` is deterministic: members sorted, fixed timestamps, fixed
permissions, canonical JSON - identical input trees produce identical
archive hashes (PETPACK_SPEC 23).  The tool never executes anything from
the pack; ``validate`` re-runs the FULL Runtime validator on the built
container because author-tool reports are not trusted by the Runtime.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

# src-layout bootstrap: the project package is not pip-installed
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

MANIFEST_NAME = "petpack.json"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_ROOT = Path(__file__).resolve().parent.parent
FROZEN_OUTPUTS = frozenset(
    (_ROOT / "assets" / "petpack" / name).resolve(strict=False)
    for name in (
        "retirement-cat-official.petpack",
        "retirement-cat-official-1.0.1.petpack",
    )
)


def _source_manifest(source_dir: Path) -> dict:
    path = source_dir / MANIFEST_NAME
    if not path.is_file():
        raise SystemExit(f"missing {MANIFEST_NAME} in {source_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_files(source_dir: Path) -> dict[str, bytes]:
    """Every file under the source dir except the manifest itself."""
    files: dict[str, bytes] = {}
    for path in sorted(source_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(source_dir).as_posix()
        if rel == MANIFEST_NAME:
            continue
        files[rel] = path.read_bytes()
    return files


def _canonicalize_manifest(manifest: dict, files: dict[str, bytes]) -> dict:
    """Fill byte_size/sha256 for every declared asset/legal file."""
    for entry in manifest.get("assets", []) + manifest.get("legal_files", []):
        rel = str(entry.get("path", ""))
        if rel in files:
            entry["byte_size"] = len(files[rel])
            entry["sha256"] = hashlib.sha256(files[rel]).hexdigest()
    return manifest


def _safe_output_path(out_path: Path) -> Path:
    resolved = out_path.resolve(strict=False)
    if resolved in FROZEN_OUTPUTS:
        raise ValueError("refusing to overwrite frozen official release media")
    if resolved.suffix.lower() != ".petpack":
        raise ValueError("output filename must end with .petpack")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build(source_dir: Path, out_path: Path) -> None:
    out_path = _safe_output_path(out_path)
    manifest = _source_manifest(source_dir)
    files = _collect_files(source_dir)
    manifest = _canonicalize_manifest(manifest, files)
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=1
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=FIXED_DATE)
        info.external_attr = 0o600 << 16
        zf.writestr(info, manifest_bytes)
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE)
            info.external_attr = 0o600 << 16
            zf.writestr(info, files[rel])
    payload = buffer.getvalue()
    _atomic_write(out_path, payload)
    archive_hash = hashlib.sha256(payload).hexdigest()
    print(f"built {out_path}")
    print(f"archive_sha256 {archive_hash}")
    print(f"files          {len(files) + 1}")


def validate(pack_path: Path) -> int:
    from retirement_pet.petpack.validator import validate_petpack

    report = validate_petpack(pack_path.read_bytes())
    print(f"result         {report.result}")
    if report.pack_key:
        print(f"pack           {report.pack_key}")
    if report.content_digest:
        print(f"digest         {report.content_digest}")
    for diag in report.diagnostics:
        print(f"  {diag.code} [{diag.phase}] {diag.message_key}")
    return 0 if report.accepted else 1


def preflight(pack_path: Path) -> int:
    """Run the same narrow PNG/renderer/Alpha gate used by GUI import."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from retirement_pet.petpack.local_import import (
        LocalImportError,
        preflight_local_pack,
    )

    app = QApplication.instance()
    owns_application = app is None
    if app is None:
        app = QApplication([])
    try:
        preview = preflight_local_pack(pack_path)
    except LocalImportError as exc:
        print(f"preflight      REJECT: {exc}")
        return 1
    finally:
        if owns_application:
            app.quit()
    print("preflight      ACCEPT")
    print(f"pack           {preview.revision_key.pack}")
    print(f"characters     {', '.join(preview.character_ids)}")
    print(f"archive_sha256 {preview.archive_sha256}")
    if preview.warning_codes:
        print(f"warnings       {', '.join(preview.warning_codes)}")
    return 0


def inspect(pack_path: Path) -> int:
    from retirement_pet.petpack.validator import validate_petpack

    data = pack_path.read_bytes()
    report = validate_petpack(data)
    m = report.manifest
    print(f"result         {report.result}")
    if not report.accepted:
        for diag in report.diagnostics:
            print(f"  {diag.code} [{diag.phase}] {diag.message_key}")
        return 1
    pkg = m.get("package", {})
    print(f"pack           {report.pack_key}")
    print(f"version        {pkg.get('version')}")
    print(f"series         {m.get('series', {}).get('id')}")
    print(f"characters     {', '.join(c.get('id', '') for c in m.get('characters', []))}")
    print(f"actions        {len(m.get('actions', []))}")
    print(f"assets         {len(m.get('assets', []))}")
    print(f"uncompressed   {len(data)} archive bytes")
    print(f"digest         {report.content_digest}")
    return 0


def init(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / MANIFEST_NAME
    if manifest_path.exists():
        print(f"refusing to overwrite existing {manifest_path}", file=sys.stderr)
        return 1
    manifest = {
        "schema_version": "1.0",
        "package": {
            "publisher_id": "community.example",
            "id": "my-pack",
            "version": "0.1.0",
            "display_name": {"zh-CN": "我的角色包"},
        },
        "series": {"id": "main", "display_name": {"zh-CN": "主系列"}},
        "assets": [],
        "characters": [],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "assets").mkdir(parents=True, exist_ok=True)
    print(f"initialized pack skeleton in {target}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command, *rest = argv
    if command == "init" and rest:
        return init(Path(rest[0]))
    if command == "build" and len(rest) == 2:
        try:
            build(Path(rest[0]), Path(rest[1]))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    if command == "validate" and rest:
        return validate(Path(rest[0]))
    if command == "preflight" and rest:
        return preflight(Path(rest[0]))
    if command == "inspect" and rest:
        return inspect(Path(rest[0]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
