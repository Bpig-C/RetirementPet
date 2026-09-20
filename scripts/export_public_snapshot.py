#!/usr/bin/env python3
"""Export a fail-closed, single-commit public snapshot from ``HEAD``.

The exporter deliberately reads blobs from Git's object database instead of
copying the working tree.  Raw local evidence and common private artefacts are
excluded, while suspicious paths or contents reject the complete export.  A
rejected run never leaves a candidate directory behind.

The accepted candidate is a new Git repository with exactly one root commit.
Its tracked manifest records public file hashes, policy identity, and
aggregate exclusion counts without naming the private source commit or tree.
A sidecar audit report records that private source identity and the relative
paths that were excluded or rejected; it is not part of the public commit.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
import zipfile
import zlib


EXPORTER_VERSION = "1.1"
MANIFEST_NAME = "PUBLIC_SNAPSHOT_MANIFEST.json"
DEFAULT_CONFIG_PATH = "config/public_snapshot.json"
AUDIT_SUFFIX = ".public-snapshot-audit.json"
PUBLIC_EVIDENCE_INDEX = "evidence/README.md"
PUBLIC_ROOT_COMMIT_DATE = "2000-01-01T00:00:00Z"

MANDATORY_REQUIRED_PATHS = frozenset(
    {
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
        PUBLIC_EVIDENCE_INDEX,
        "pyproject.toml",
        "scripts/export_public_snapshot.py",
        "src/retirement_pet/__init__.py",
        "tests/test_public_snapshot.py",
    }
)

MANDATORY_EXCLUDED_PREFIXES = (
    ".git/",
    ".release/",
    ".venv/",
    "build/",
    "dist/",
    "evidence/",
)
MANDATORY_EXCLUDED_COMPONENTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
FORBIDDEN_DATA_SUFFIXES = frozenset(
    {
        ".db",
        ".db-shm",
        ".db-wal",
        ".dmp",
        ".dump",
        ".etl",
        ".har",
        ".jsonl",
        ".log",
        ".pcap",
        ".sqlite",
        ".sqlite3",
    }
)
ALLOWED_BINARY_SUFFIXES = frozenset({".ico", ".petpack", ".png"})
ARCHIVE_SUFFIXES = frozenset({".petpack", ".whl", ".zip"})
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".cfg",
        ".css",
        ".gitattributes",
        ".gitignore",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".pyi",
        ".rst",
        ".spec",
        ".toml",
        ".ts",
        ".txt",
        ".yaml",
        ".yml",
    }
)
CODE_SUFFIXES = frozenset(
    {".c", ".cc", ".cpp", ".h", ".hpp", ".js", ".ps1", ".py", ".pyi", ".spec", ".ts"}
)
RAW_DIAGNOSTIC_SUFFIXES = frozenset(
    {"", ".cfg", ".csv", ".ini", ".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
)

WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)

SECRET_PATH_RE = re.compile(
    r"(?i)(?:^|[-_.])(?:auth|cookie|credential|password|private|secret|token)s?(?:$|[-_.])"
)
WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|file:///)?Users[\\/][^\\/\s\"'<>|]+"
)
POSIX_USER_PATH_RE = re.compile(
    r"(?i)/(?:home|Users)/[^/\s\"'<>]+"
)
WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
TOKEN_RE = re.compile(r"(?i)(?<![A-Za-z0-9_.-])[A-Za-z0-9][A-Za-z0-9_.-]{1,63}(?![A-Za-z0-9_.-])")
RAW_PID_RE = re.compile(
    r"(?i)[\"'](?:pid|process_id|hwnd|window_handle)[\"']\s*:\s*(?:0x)?[1-9][0-9a-f_]{0,15}"
)
RAW_DISPLAY_RE = re.compile(r"(?i)(?:\\)+\.(?:\\)+DISPLAY[0-9]+")
EMAIL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)
IPV4_CANDIDATE_RE = re.compile(
    r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])"
)
IPV6_CANDIDATE_RE = re.compile(
    r"(?i)(?<![0-9a-f:])[0-9a-f:]{2,39}(?![0-9a-f:])"
)
DOCUMENTATION_IPV4_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
DOCUMENTATION_IPV6_NETWORK = ipaddress.ip_network("2001:db8::/32")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_METADATA_CHUNKS = frozenset({b"eXIf", b"iCCP", b"iTXt", b"tEXt", b"tIME", b"zTXt"})
PNG_APPROVED_CHUNKS = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"bKGD",
        b"cHRM",
        b"gAMA",
        b"hIST",
        b"pHYs",
        b"sBIT",
        b"sRGB",
        b"tRNS",
    }
)

HIGH_CONFIDENCE_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password|passwd)\b"
            r"\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']"
        ),
    ),
)


class SnapshotError(RuntimeError):
    """Base class for controlled exporter failures."""


class Violation(NamedTuple):
    rule: str
    path: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "path": self.path, "detail": self.detail}


class ExportRejected(SnapshotError):
    """The source tree or candidate violated public-export policy."""

    def __init__(self, message: str, violations: Iterable[Violation]):
        self.violations = tuple(violations)
        super().__init__(message)


class TreeEntry(NamedTuple):
    mode: str
    object_type: str
    object_id: str
    path: str


class ExportResult(NamedTuple):
    source_commit: str
    source_tree: str
    public_commit: str
    public_tree: str
    manifest_sha256: str
    included_files: int
    excluded_files: int
    audit_report: Path
    output: Path


class PublicTreeResult(NamedTuple):
    source_commit: str
    source_tree: str
    scanned_files: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(name, None)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    if extra:
        env.update(extra)
    return env


def _git_bytes(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> bytes:
    command = ["git", "-c", "core.excludesFile=" + os.devnull, *args]
    result = subprocess.run(
        command,
        cwd=root,
        env=_safe_git_env(extra_env),
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise SnapshotError(f"git command failed ({' '.join(args)}): {stderr or result.returncode}")
    return result.stdout


def _git_text(root: Path, *args: str, extra_env: Mapping[str, str] | None = None) -> str:
    return _git_bytes(root, *args, extra_env=extra_env).decode("utf-8", "strict").strip()


def _normalise_relative_path(value: str, *, allow_trailing_slash: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotError("export policy paths must be non-empty strings")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise SnapshotError(f"export policy path is not safe POSIX-relative syntax: {value!r}")
    path = PurePosixPath(value.rstrip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotError(f"export policy path is not normalized: {value!r}")
    normalized = path.as_posix()
    if allow_trailing_slash:
        normalized += "/"
    return normalized


def _load_policy(source_root: Path, config_path: str, commit: str) -> tuple[dict[str, Any], bytes]:
    normalized = _normalise_relative_path(config_path)
    try:
        raw = _git_bytes(source_root, "show", f"{commit}:{normalized}")
    except SnapshotError as exc:
        raise SnapshotError(f"export policy must be tracked at {normalized}: {exc}") from exc
    try:
        policy = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"export policy is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(policy, dict):
        raise SnapshotError("export policy root must be an object")
    allowed_keys = {
        "schema_version",
        "additional_exclude_paths",
        "additional_exclude_prefixes",
        "forbidden_username_sha256",
        "limits",
        "required_paths",
    }
    unknown = sorted(set(policy) - allowed_keys)
    if unknown:
        raise SnapshotError(f"unknown export policy keys: {', '.join(unknown)}")
    if policy.get("schema_version") != 1:
        raise SnapshotError("unsupported public snapshot policy schema")

    for key in ("additional_exclude_paths", "additional_exclude_prefixes", "required_paths"):
        value = policy.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise SnapshotError(f"{key} must be a list of strings")
    policy["additional_exclude_paths"] = [
        _normalise_relative_path(item) for item in policy.get("additional_exclude_paths", [])
    ]
    policy["additional_exclude_prefixes"] = [
        _normalise_relative_path(item, allow_trailing_slash=True)
        for item in policy.get("additional_exclude_prefixes", [])
    ]
    policy["required_paths"] = [
        _normalise_relative_path(item) for item in policy.get("required_paths", [])
    ]
    missing_mandatory = sorted(MANDATORY_REQUIRED_PATHS - set(policy["required_paths"]))
    if missing_mandatory:
        raise SnapshotError(
            "export policy may not omit mandatory public governance paths: "
            + ", ".join(missing_mandatory)
        )
    excluded_required = {
        required: reason
        for required in policy["required_paths"]
        if (reason := _exclusion_reason(required, policy)) is not None
    }
    if excluded_required:
        details = ", ".join(
            f"{path} ({reason})" for path, reason in sorted(excluded_required.items())
        )
        raise SnapshotError(f"required public paths may not be excluded: {details}")

    username_hashes = policy.get("forbidden_username_sha256", [])
    if not isinstance(username_hashes, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
        for item in username_hashes
    ):
        raise SnapshotError("forbidden_username_sha256 must contain lowercase SHA-256 values")

    limits = policy.get("limits")
    required_limits = {
        "max_archive_members",
        "max_archive_uncompressed_bytes",
        "max_blob_bytes",
        "max_files",
        "max_total_bytes",
    }
    if not isinstance(limits, dict) or set(limits) != required_limits:
        raise SnapshotError("limits must contain exactly the required bounded-export keys")
    hard_maxima = {
        "max_archive_members": 10_000,
        "max_archive_uncompressed_bytes": 256 * 1024 * 1024,
        "max_blob_bytes": 128 * 1024 * 1024,
        "max_files": 50_000,
        "max_total_bytes": 512 * 1024 * 1024,
    }
    for name, maximum in hard_maxima.items():
        value = limits.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
            raise SnapshotError(f"invalid or unsafe export limit {name}")
    return policy, raw


def _parse_tree(raw: bytes, *, allow_generated_manifest: bool = False) -> list[TreeEntry]:
    entries: list[TreeEntry] = []
    seen_casefold: dict[str, str] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii", "strict").split(" ", 2)
            path = raw_path.decode("utf-8", "strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExportRejected(
                "Git tree contains an undecodable entry",
                [Violation("invalid_tree_entry", "<git-tree>", "tree record is not canonical UTF-8")],
            ) from exc
        _validate_repo_path(path, allow_generated_manifest=allow_generated_manifest)
        folded = path.casefold()
        prior = seen_casefold.get(folded)
        if prior is not None and prior != path:
            raise ExportRejected(
                "Git tree contains a case-insensitive path collision",
                [Violation("case_collision", path, f"collides with {prior}")],
            )
        seen_casefold[folded] = path
        entries.append(TreeEntry(mode, object_type, object_id, path))
    return entries


def _validate_repo_path(path: str, *, allow_generated_manifest: bool = False) -> None:
    try:
        normalized = _normalise_relative_path(path)
    except SnapshotError as exc:
        raise ExportRejected(
            "Git tree contains an unsafe path",
            [Violation("unsafe_path", path or "<empty>", "path is not normalized repository-relative syntax")],
        ) from exc
    if normalized != path:
        raise ExportRejected(
            "Git tree contains a non-canonical path",
            [Violation("unsafe_path", path, "path spelling is not canonical")],
        )
    if path == MANIFEST_NAME and not allow_generated_manifest:
        raise ExportRejected(
            "Source tree reserves the generated public manifest name",
            [Violation("reserved_manifest_path", path, "generated manifest would collide")],
        )
    for component in PurePosixPath(path).parts:
        if (
            component.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in component)
            or component.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        ):
            raise ExportRejected(
                "Git tree contains a Windows-unsafe path",
                [Violation("windows_unsafe_path", path, "path cannot be represented safely on Windows")],
            )


def _exclusion_reason(path: str, policy: Mapping[str, Any]) -> str | None:
    prefixes = (*MANDATORY_EXCLUDED_PREFIXES, *policy["additional_exclude_prefixes"])
    for prefix in prefixes:
        if prefix == "evidence/" and path == PUBLIC_EVIDENCE_INDEX:
            continue
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return "excluded_prefix:" + prefix
    if path in policy["additional_exclude_paths"]:
        return "excluded_path"
    if any(component in MANDATORY_EXCLUDED_COMPONENTS for component in PurePosixPath(path).parts):
        return "excluded_cache"
    return None


def _path_violations(
    path: str,
    username_hashes: frozenset[str],
    machine_name_hashes: frozenset[str] = frozenset(),
) -> list[Violation]:
    violations: list[Violation] = []
    suffix = Path(path).suffix.casefold()
    basename = PurePosixPath(path).name
    lower_basename = basename.casefold()
    if suffix in FORBIDDEN_DATA_SUFFIXES:
        violations.append(Violation("forbidden_data_file", path, f"suffix {suffix} is not public-source material"))
    components = PurePosixPath(path).parts
    if (
        lower_basename in {".env", ".git-credentials", ".npmrc", ".pypirc", "id_ed25519", "id_rsa"}
        or lower_basename.startswith(".env.")
        or suffix in {".der", ".key", ".kdbx", ".p12", ".pem", ".pfx"}
        or any(SECRET_PATH_RE.search(component) for component in components)
    ):
        violations.append(Violation("secret_like_path", path, "filename resembles credentials or private material"))
    for component in components:
        for token in TOKEN_RE.findall(component):
            token_hash = _sha256(token.casefold().encode("utf-8"))
            if token_hash in username_hashes:
                violations.append(Violation("forbidden_username", path, "path contains a private username token"))
                return violations
            if token_hash in machine_name_hashes:
                violations.append(Violation("forbidden_machine_name", path, "path contains this host's machine name"))
                return violations
    return violations


def _sensitive_local_literals(source_root: Path) -> tuple[str, ...]:
    candidates: set[str] = {
        str(source_root.resolve()),
        source_root.resolve().as_posix(),
        str(Path(sys.executable).resolve()),
        Path(sys.executable).resolve().as_posix(),
    }
    for name in (
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "USERPROFILE",
    ):
        value = os.environ.get(name)
        if value:
            candidates.add(value)
            candidates.add(value.replace("\\", "/"))
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    if home is not None:
        candidates.add(str(home))
        candidates.add(home.as_posix())
    return tuple(sorted((item for item in candidates if len(item) >= 3), key=len, reverse=True))


def _runtime_username_hashes(policy: Mapping[str, Any]) -> frozenset[str]:
    hashes = set(policy["forbidden_username_sha256"])
    usernames = {getpass.getuser(), os.environ.get("USERNAME"), os.environ.get("USER")}
    for username in usernames:
        if username and len(username) >= 2:
            hashes.add(_sha256(username.casefold().encode("utf-8")))
    return frozenset(hashes)


def _runtime_machine_name_hashes() -> frozenset[str]:
    names = {
        socket.gethostname(),
        os.environ.get("COMPUTERNAME"),
        os.environ.get("HOSTNAME"),
    }
    return frozenset(
        _sha256(name.casefold().encode("utf-8"))
        for name in names
        if name and len(name) >= 3
    )


def _decode_text(path: str, data: bytes) -> str | None:
    suffix = Path(path).suffix.casefold()
    looks_textual = suffix in TEXT_SUFFIXES or PurePosixPath(path).name.startswith(".")
    if not looks_textual:
        return None
    try:
        return data.decode("utf-8-sig", "strict")
    except UnicodeDecodeError as exc:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                return data.decode("utf-16", "strict")
            except UnicodeDecodeError:
                pass
        raise ExportRejected(
            "Text-like file is not valid UTF-8/UTF-16",
            [Violation("invalid_text_encoding", path, "text-like public source must use a supported encoding")],
        ) from exc


def _is_version_resource_quad(text: str, match: re.Match[str]) -> bool:
    """Return true for a dotted-quad Windows version resource value."""

    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    if re.search(r"(?i)\b(?:FileVersion|ProductVersion)\b", line):
        return True
    # release documents state version quads in prose ("版本均为 1.2.0.0"):
    # a quad preceded by a version word on the same line is not an address
    prefix = line[: match.start() - line_start]
    return re.search(r"(?i)(?:版本|version)\s*[^,，;；。]{0,12}$",
                     prefix) is not None


def _scan_text(
    path: str,
    text: str,
    *,
    local_literals: Sequence[str],
    username_hashes: frozenset[str],
    machine_name_hashes: frozenset[str],
) -> list[Violation]:
    violations: list[Violation] = []
    if WINDOWS_USER_PATH_RE.search(text):
        violations.append(Violation("windows_user_path", path, "content contains a Windows user-profile path"))
    if POSIX_USER_PATH_RE.search(text):
        violations.append(Violation("posix_user_path", path, "content contains a Unix/macOS user-profile path"))
    folded = text.casefold()
    if any(literal.casefold() in folded for literal in local_literals):
        violations.append(Violation("local_absolute_path", path, "content contains this export host's absolute path"))
    if Path(path).suffix.casefold() not in CODE_SUFFIXES:
        for match in WINDOWS_ABSOLUTE_RE.finditer(text):
            tail = text[match.end():match.end() + 3]
            if tail.startswith("…") or tail.startswith("..."):
                continue  # redacted reference, not a real path
            violations.append(Violation(
                "document_absolute_path", path,
                "public documentation/data contains a drive-absolute path"))
            break
    for token in TOKEN_RE.findall(text):
        token_hash = _sha256(token.casefold().encode("utf-8"))
        if token_hash in username_hashes:
            violations.append(Violation("forbidden_username", path, "content contains a private username token"))
            break
        if token_hash in machine_name_hashes:
            violations.append(Violation("forbidden_machine_name", path, "content contains this host's machine name"))
            break
    for match in EMAIL_RE.finditer(text):
        domain = match.group(0).rsplit("@", 1)[1].casefold()
        if not domain.endswith(".invalid"):
            violations.append(
                Violation("email_address", path, "content contains a non-reserved email address")
            )
            break
    for rule, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        if pattern.search(text):
            violations.append(Violation("secret_content:" + rule, path, "content matches a high-confidence secret signature"))
    suffix = Path(path).suffix.casefold()
    if suffix in RAW_DIAGNOSTIC_SUFFIXES:
        if RAW_PID_RE.search(text):
            violations.append(Violation("raw_process_identifier", path, "data-like file contains a raw PID/HWND value"))
        if RAW_DISPLAY_RE.search(text):
            violations.append(Violation("raw_display_identifier", path, "data-like file contains a raw display device value"))
    if suffix not in CODE_SUFFIXES:
        # inline markdown code (`File::method()`) is full of hex+colon
        # shapes that parse as IPv6 but are identifiers, not addresses;
        # the address rules therefore run on prose with inline code
        # removed (secret and path rules above still scan the full text)
        prose = re.sub(r"`[^`\n]*`", " ", text)
        for match in (*IPV4_CANDIDATE_RE.finditer(prose),
                      *IPV6_CANDIDATE_RE.finditer(prose)):
            if ":" in match.group(0) and match.group(0).count(":") < 2:
                continue
            # C++ scope fragments ("Foo::bar" -> "::e") parse as IPv6 but
            # carry too little hex material to be an address; a real IPv6
            # literal always has at least four hex digits or two groups
            candidate = match.group(0)
            hex_chars = sum(1 for c in candidate if c in "0123456789abcdef")
            groups = [g for g in candidate.split(":") if g]
            if candidate.count(":") >= 2 and (hex_chars < 4
                                              or len(groups) < 2):
                continue
            try:
                address = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if address.is_unspecified or address.is_loopback:
                continue
            if address.version == 4 and any(address in network for network in DOCUMENTATION_IPV4_NETWORKS):
                continue
            if address.version == 6 and address in DOCUMENTATION_IPV6_NETWORK:
                continue
            if address.version == 4 and _is_version_resource_quad(prose, match):
                continue
            violations.append(
                Violation("ip_address", path, "documentation/data contains a non-documentation IP address")
            )
            break
    return _deduplicate_violations(violations)


def _deduplicate_violations(violations: Iterable[Violation]) -> list[Violation]:
    result: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    for violation in violations:
        key = (violation.rule, violation.path)
        if key not in seen:
            seen.add(key)
            result.append(violation)
    return result


def _scan_png(path: str, data: bytes) -> list[Violation]:
    if not data.startswith(PNG_SIGNATURE):
        return [Violation("invalid_png", path, "PNG signature is missing")]
    violations: list[Violation] = []
    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            return [Violation("invalid_png", path, "PNG chunk framing is truncated")]
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        payload_end = offset + 8 + length
        chunk_end = payload_end + 4
        if payload_end < offset or chunk_end > len(data):
            return [Violation("invalid_png", path, "PNG chunk length exceeds the file boundary")]
        if len(chunk_type) != 4 or any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type):
            return [Violation("invalid_png", path, "PNG chunk type is not alphabetic ASCII")]
        expected_crc = int.from_bytes(data[payload_end:chunk_end], "big")
        actual_crc = zlib.crc32(data[offset + 4 : payload_end]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return [Violation("invalid_png", path, "PNG chunk CRC does not match")]
        if chunk_index == 0 and (chunk_type != b"IHDR" or length != 13):
            return [Violation("invalid_png", path, "PNG must begin with one 13-byte IHDR chunk")]
        if chunk_type == b"IHDR":
            if saw_ihdr or chunk_index != 0:
                return [Violation("invalid_png", path, "PNG contains a duplicate or misplaced IHDR chunk")]
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            if length != 0 or saw_iend or chunk_end != len(data):
                return [Violation("invalid_png", path, "PNG IEND is malformed or not final")]
            saw_iend = True
        if chunk_type in PNG_METADATA_CHUNKS:
            violations.append(
                Violation(
                    "png_metadata_chunk",
                    path,
                    f"PNG metadata chunk {chunk_type.decode('ascii')} is not permitted",
                )
            )
        elif chunk_type not in PNG_APPROVED_CHUNKS:
            violations.append(
                Violation(
                    "png_unapproved_chunk",
                    path,
                    f"PNG chunk {chunk_type.decode('ascii')} is outside the public allowlist",
                )
            )
        offset = chunk_end
        chunk_index += 1
    if not (saw_ihdr and saw_idat and saw_iend):
        return [Violation("invalid_png", path, "PNG is missing IHDR, IDAT, or IEND")]
    return _deduplicate_violations(violations)


def _scan_ico(path: str, data: bytes) -> list[Violation]:
    if len(data) < 6:
        return [Violation("invalid_ico", path, "ICO header is truncated")]
    reserved = int.from_bytes(data[0:2], "little")
    image_type = int.from_bytes(data[2:4], "little")
    image_count = int.from_bytes(data[4:6], "little")
    if reserved != 0 or image_type != 1 or image_count < 1 or image_count > 256:
        return [Violation("invalid_ico", path, "ICO header fields are invalid")]
    directory_end = 6 + image_count * 16
    if directory_end > len(data):
        return [Violation("invalid_ico", path, "ICO image directory is truncated")]
    violations: list[Violation] = []
    for index in range(image_count):
        entry_offset = 6 + index * 16
        image_size = int.from_bytes(data[entry_offset + 8 : entry_offset + 12], "little")
        image_offset = int.from_bytes(data[entry_offset + 12 : entry_offset + 16], "little")
        image_end = image_offset + image_size
        embedded_path = f"{path}!/image-{index}.png"
        if (
            image_size == 0
            or image_offset < directory_end
            or image_end < image_offset
            or image_end > len(data)
        ):
            violations.append(Violation("invalid_ico", path, "ICO image entry exceeds file boundaries"))
            continue
        image_data = data[image_offset:image_end]
        if image_data.startswith(PNG_SIGNATURE):
            violations.extend(_scan_png(embedded_path, image_data))
    return _deduplicate_violations(violations)


def _scan_archive(
    path: str,
    data: bytes,
    *,
    policy: Mapping[str, Any],
    local_literals: Sequence[str],
    username_hashes: frozenset[str],
    machine_name_hashes: frozenset[str],
) -> list[Violation]:
    violations: list[Violation] = []
    limits = policy["limits"]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        return [Violation("invalid_archive", path, f"archive cannot be parsed: {type(exc).__name__}")]
    with archive:
        if archive.comment:
            violations.append(
                Violation("archive_metadata", path, "archive comment is not permitted in public source")
            )
        members = archive.infolist()
        if len(members) > limits["max_archive_members"]:
            return [Violation("archive_member_limit", path, "archive has too many members")]
        seen: set[str] = set()
        total = 0
        for member in members:
            member_path = member.filename.replace("\\", "/")
            public_member_path = f"{path}!/{member_path}"
            try:
                _validate_archive_member_path(member_path)
            except SnapshotError:
                violations.append(Violation("unsafe_archive_path", public_member_path, "archive member path is unsafe"))
                continue
            folded = member_path.casefold()
            if folded in seen:
                violations.append(Violation("duplicate_archive_path", public_member_path, "duplicate archive member"))
                continue
            seen.add(folded)
            mode = (member.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                violations.append(Violation("archive_symlink", public_member_path, "archive contains a symbolic link"))
                continue
            if member.flag_bits & 0x1:
                violations.append(Violation("encrypted_archive_member", public_member_path, "encrypted members cannot be audited"))
                continue
            if member.comment or member.extra:
                violations.append(
                    Violation(
                        "archive_metadata",
                        public_member_path,
                        "archive member comment/extra metadata is not permitted",
                    )
                )
            if member.is_dir():
                continue
            total += member.file_size
            if member.file_size > limits["max_blob_bytes"] or total > limits["max_archive_uncompressed_bytes"]:
                violations.append(Violation("archive_size_limit", path, "archive exceeds bounded inspection limits"))
                break
            if member.compress_size == 0 and member.file_size > 0:
                violations.append(Violation("archive_ratio_limit", public_member_path, "archive member has an invalid compression ratio"))
                continue
            if member.compress_size and member.file_size / member.compress_size > 200:
                violations.append(Violation("archive_ratio_limit", public_member_path, "archive member compression ratio is excessive"))
                continue
            violations.extend(_path_violations(member_path, username_hashes, machine_name_hashes))
            try:
                member_data = archive.read(member)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                violations.append(Violation("archive_read_error", public_member_path, "archive member failed CRC/decompression"))
                continue
            if Path(member_path).suffix.casefold() in ARCHIVE_SUFFIXES or member_data.startswith(b"PK\x03\x04"):
                violations.append(Violation("nested_archive", public_member_path, "nested archives are not accepted"))
                continue
            violations.extend(
                _scan_payload(
                    public_member_path,
                    member_data,
                    policy=policy,
                    local_literals=local_literals,
                    username_hashes=username_hashes,
                    machine_name_hashes=machine_name_hashes,
                    allow_archive=False,
                )
            )
    return _deduplicate_violations(violations)


def _validate_archive_member_path(path: str) -> None:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        raise SnapshotError("unsafe archive path")
    pure = PurePosixPath(path.rstrip("/"))
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise SnapshotError("unsafe archive path")
    for component in pure.parts:
        if (
            component.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in component)
            or component.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        ):
            raise SnapshotError("unsafe archive path")


def _scan_payload(
    path: str,
    data: bytes,
    *,
    policy: Mapping[str, Any],
    local_literals: Sequence[str],
    username_hashes: frozenset[str],
    machine_name_hashes: frozenset[str],
    allow_archive: bool = True,
) -> list[Violation]:
    violations = _path_violations(path, username_hashes, machine_name_hashes)
    if violations:
        return violations
    if len(data) > policy["limits"]["max_blob_bytes"]:
        return [Violation("blob_size_limit", path, "file exceeds bounded inspection limit")]
    suffix = Path(path.split("!/", 1)[-1]).suffix.casefold()
    if allow_archive and (suffix in ARCHIVE_SUFFIXES or data.startswith(b"PK\x03\x04")):
        return _scan_archive(
            path,
            data,
            policy=policy,
            local_literals=local_literals,
            username_hashes=username_hashes,
            machine_name_hashes=machine_name_hashes,
        )
    try:
        text = _decode_text(path.split("!/", 1)[-1], data)
    except ExportRejected as exc:
        return list(exc.violations)
    if text is None:
        if suffix not in ALLOWED_BINARY_SUFFIXES:
            return [Violation("opaque_binary", path, "binary type is not explicitly allowed for public export")]
        if suffix == ".png" or data.startswith(PNG_SIGNATURE):
            violations.extend(_scan_png(path, data))
        elif suffix == ".ico":
            violations.extend(_scan_ico(path, data))
        # Direct byte checks still catch uncompressed high-confidence literals.
        lower = data.lower()
        if b"-----begin private key-----" in lower or b"-----begin rsa private key-----" in lower:
            violations.append(Violation("secret_content:private_key", path, "binary contains a private-key marker"))
        for literal in local_literals:
            encoded = literal.encode("utf-8", "ignore").lower()
            if encoded and encoded in lower:
                violations.append(Violation("local_absolute_path", path, "binary contains this export host's absolute path"))
                break
        return _deduplicate_violations(violations)
    return _scan_text(
        path,
        text,
        local_literals=local_literals,
        username_hashes=username_hashes,
        machine_name_hashes=machine_name_hashes,
    )


def _inventory_digest(records: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(record["mode"].encode("ascii"))
        digest.update(b"\x00")
        digest.update(str(record["bytes"]).encode("ascii"))
        digest.update(b"\x00")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SnapshotError(f"audit report already exists: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical_json(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def default_audit_path(output: Path) -> Path:
    return output.parent / f"{output.name}{AUDIT_SUFFIX}"


def _write_blob(target_root: Path, entry: TreeEntry, data: bytes) -> None:
    destination = target_root.joinpath(*PurePosixPath(entry.path).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(data)
    if entry.mode == "100755" and os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _initialise_single_commit(
    candidate: Path,
    *,
    executable_paths: Sequence[str],
) -> tuple[str, str]:
    _git_bytes(candidate, "init", "--initial-branch=main")
    _git_bytes(candidate, "config", "core.autocrlf", "false")
    _git_bytes(candidate, "config", "core.safecrlf", "true")
    _git_bytes(candidate, "config", "core.filemode", "true")
    hooks = candidate / ".git" / "disabled-hooks"
    hooks.mkdir(parents=True, exist_ok=False)
    # Git resolves this path from the worktree for non-bare hook execution.
    # Keeping it relative prevents the temporary staging directory from being
    # persisted in the portable candidate's local .git/config.
    _git_bytes(candidate, "config", "core.hooksPath", ".git/disabled-hooks")
    _git_bytes(candidate, "add", "--all", "--")
    for path in executable_paths:
        _git_bytes(candidate, "update-index", "--chmod=+x", "--", path)
    commit_env = {
        "GIT_AUTHOR_NAME": "RetirementPet Public Export",
        "GIT_AUTHOR_EMAIL": "public-export@retirementpet.invalid",
        "GIT_COMMITTER_NAME": "RetirementPet Public Export",
        "GIT_COMMITTER_EMAIL": "public-export@retirementpet.invalid",
        "GIT_AUTHOR_DATE": PUBLIC_ROOT_COMMIT_DATE,
        "GIT_COMMITTER_DATE": PUBLIC_ROOT_COMMIT_DATE,
    }
    _git_bytes(
        candidate,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--no-gpg-sign",
        "-m",
        "Initial public source snapshot",
        extra_env=commit_env,
    )
    public_commit = _git_text(candidate, "rev-parse", "HEAD")
    public_tree = _git_text(candidate, "rev-parse", "HEAD^{tree}")
    if _git_text(candidate, "rev-list", "--count", "HEAD") != "1":
        raise SnapshotError("candidate repository is not a single root commit")
    if _git_bytes(candidate, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise SnapshotError("candidate repository is not clean after commit")
    return public_commit, public_tree


def _ensure_output_boundaries(source_root: Path, output: Path, audit_report: Path) -> None:
    source = source_root.resolve()
    output_resolved = output.resolve(strict=False)
    audit_resolved = audit_report.resolve(strict=False)
    if output_resolved == source or output_resolved.is_relative_to(source):
        raise SnapshotError("public snapshot output must be outside the source repository")
    if source.is_relative_to(output_resolved):
        raise SnapshotError("public snapshot output cannot contain the source repository")
    if audit_resolved == source or audit_resolved.is_relative_to(source):
        raise SnapshotError("audit report must be outside the source repository")
    if output.exists():
        raise SnapshotError(f"public snapshot output already exists: {output}")
    if audit_report.exists():
        raise SnapshotError(f"public snapshot audit report already exists: {audit_report}")
    if audit_resolved == output_resolved or audit_resolved.is_relative_to(output_resolved):
        raise SnapshotError("audit report must be outside the candidate repository")


def _rejection_payload(
    *,
    source_commit: str | None,
    source_tree: str | None,
    config_path: str,
    config_sha256: str | None,
    violations: Sequence[Violation],
    excluded: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "REJECTED",
        "exporter_version": EXPORTER_VERSION,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "policy": {"path": config_path, "sha256": config_sha256},
        "violations": [item.as_dict() for item in violations],
        "excluded": list(excluded),
    }


def _error_payload(
    *,
    source_commit: str | None,
    source_tree: str | None,
    config_path: str,
    config_sha256: str | None,
    error: BaseException,
    excluded: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "ERROR",
        "exporter_version": EXPORTER_VERSION,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "policy": {"path": config_path, "sha256": config_sha256},
        "error_type": type(error).__name__,
        "excluded": list(excluded),
    }


def _remove_owned_candidate(output: Path, expected_commit: str | None) -> None:
    """Remove only a complete candidate created by this invocation."""

    if expected_commit is None or not (output / ".git").is_dir():
        raise SnapshotError("cannot safely identify candidate for rollback")
    try:
        actual_commit = _git_text(output, "rev-parse", "HEAD^{commit}")
    except SnapshotError as exc:
        raise SnapshotError("cannot verify candidate identity for rollback") from exc
    if actual_commit != expected_commit:
        raise SnapshotError("candidate identity changed before rollback")
    shutil.rmtree(output)


def verify_public_tree(
    source_root: Path,
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> PublicTreeResult:
    """Fail closed unless the checked-out HEAD is already safe to publish.

    Unlike :func:`export_snapshot`, this mode does not silently exclude raw
    private-source material.  It is intended for the public repository's PR
    workflow, where every tracked path (apart from Git's own database, which
    is never part of the tree) must itself be publishable.
    """

    source_root = source_root.resolve()
    top_level = Path(_git_text(source_root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != source_root:
        raise ExportRejected(
            "source root must be the Git top-level directory",
            [Violation("source_not_git_root", "<source>", "source root differs from git toplevel")],
        )
    source_commit = _git_text(source_root, "rev-parse", "HEAD^{commit}")
    source_tree = _git_text(source_root, "rev-parse", "HEAD^{tree}")

    policy, _raw_policy = _load_policy(source_root, config_path, source_commit)
    username_hashes = _runtime_username_hashes(policy)
    machine_name_hashes = _runtime_machine_name_hashes()
    local_literals = _sensitive_local_literals(source_root)
    entries = _parse_tree(
        _git_bytes(source_root, "ls-tree", "-r", "-z", "--full-tree", source_commit),
        allow_generated_manifest=True,
    )
    violations: list[Violation] = []
    if len(entries) > policy["limits"]["max_files"]:
        violations.append(Violation("file_count_limit", "<git-tree>", "too many tracked paths"))

    present_paths = {entry.path for entry in entries}
    for required in policy["required_paths"]:
        if required not in present_paths:
            violations.append(Violation("required_path_missing", required, "required public source path is absent"))

    accepted_paths: set[str] = set()
    scanned_total = 0
    for entry in entries:
        reason = _exclusion_reason(entry.path, policy)
        if reason is not None:
            violations.append(
                Violation(
                    "public_tree_excluded_path",
                    entry.path,
                    f"tracked public path matches private-export rule {reason}",
                )
            )
            continue
        if entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            violations.append(
                Violation("special_git_entry", entry.path, f"mode/type {entry.mode}/{entry.object_type} is not publishable")
            )
            continue
        data = _git_bytes(source_root, "cat-file", "blob", entry.object_id)
        scanned_total += len(data)
        if scanned_total > policy["limits"]["max_total_bytes"]:
            violations.append(Violation("total_size_limit", "<git-tree>", "public source exceeds total byte limit"))
            break
        payload_issues = _scan_payload(
            entry.path,
            data,
            policy=policy,
            local_literals=local_literals,
            username_hashes=username_hashes,
            machine_name_hashes=machine_name_hashes,
        )
        if payload_issues:
            violations.extend(payload_issues)
            continue
        accepted_paths.add(entry.path)

    for required in policy["required_paths"]:
        if required in present_paths and required not in accepted_paths:
            violations.append(
                Violation(
                    "required_path_not_public",
                    required,
                    "required path did not pass public-tree content checks",
                )
            )
    violations = _deduplicate_violations(violations)
    if violations:
        raise ExportRejected("checked-out public tree violated publication policy", violations)
    return PublicTreeResult(source_commit, source_tree, len(entries))


def export_snapshot(
    source_root: Path,
    output: Path,
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
    audit_report: Path | None = None,
) -> ExportResult:
    """Export ``source_root`` HEAD to a new, one-commit public candidate."""

    source_root = source_root.resolve()
    output = output.resolve(strict=False)
    audit_report = (audit_report or default_audit_path(output)).resolve(strict=False)
    source_commit: str | None = None
    source_tree: str | None = None
    config_sha256: str | None = None
    excluded: list[dict[str, str]] = []
    staging: Path | None = None
    output_created = False
    public_commit: str | None = None
    _ensure_output_boundaries(source_root, output, audit_report)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        top_level = Path(_git_text(source_root, "rev-parse", "--show-toplevel")).resolve()
        if top_level != source_root:
            raise ExportRejected(
                "source root must be the Git top-level directory",
                [Violation("source_not_git_root", "<source>", "source root differs from git toplevel")],
            )
        source_commit = _git_text(source_root, "rev-parse", "HEAD^{commit}")
        source_tree = _git_text(source_root, "rev-parse", "HEAD^{tree}")
        dirty = _git_bytes(source_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if dirty:
            raise ExportRejected(
                "source repository is not clean",
                [Violation("source_worktree_dirty", "<source>", "commit or remove all tracked/untracked changes first")],
            )

        policy, raw_policy = _load_policy(source_root, config_path, source_commit)
        config_sha256 = _sha256(raw_policy)
        username_hashes = _runtime_username_hashes(policy)
        machine_name_hashes = _runtime_machine_name_hashes()
        local_literals = _sensitive_local_literals(source_root)
        entries = _parse_tree(
            _git_bytes(source_root, "ls-tree", "-r", "-z", "--full-tree", source_commit)
        )
        if len(entries) > policy["limits"]["max_files"]:
            raise ExportRejected(
                "source tree exceeds bounded file count",
                [Violation("file_count_limit", "<git-tree>", "too many tracked paths")],
            )

        included: list[tuple[TreeEntry, bytes]] = []
        records: list[dict[str, Any]] = []
        violations: list[Violation] = []
        included_total = 0
        present_paths = {entry.path for entry in entries}
        for required in policy["required_paths"]:
            if required not in present_paths:
                violations.append(Violation("required_path_missing", required, "required public source path is absent"))

        for entry in entries:
            reason = _exclusion_reason(entry.path, policy)
            if reason is not None:
                excluded.append({"path": entry.path, "reason": reason})
                continue
            if entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
                violations.append(
                    Violation("special_git_entry", entry.path, f"mode/type {entry.mode}/{entry.object_type} is not exportable")
                )
                continue
            path_issues = _path_violations(entry.path, username_hashes, machine_name_hashes)
            if path_issues:
                violations.extend(path_issues)
                continue
            data = _git_bytes(source_root, "cat-file", "blob", entry.object_id)
            included_total += len(data)
            if included_total > policy["limits"]["max_total_bytes"]:
                violations.append(Violation("total_size_limit", "<git-tree>", "included source exceeds total byte limit"))
                break
            payload_issues = _scan_payload(
                entry.path,
                data,
                policy=policy,
                local_literals=local_literals,
                username_hashes=username_hashes,
                machine_name_hashes=machine_name_hashes,
            )
            if payload_issues:
                violations.extend(payload_issues)
                continue
            included.append((entry, data))
            records.append(
                {"bytes": len(data), "mode": entry.mode, "path": entry.path, "sha256": _sha256(data)}
            )

        included_paths = {entry.path for entry, _data in included}
        for required in policy["required_paths"]:
            if required not in included_paths and required in present_paths:
                violations.append(
                    Violation(
                        "required_path_not_included",
                        required,
                        "required public source path did not pass inclusion checks",
                    )
                )

        violations = _deduplicate_violations(violations)
        if violations:
            raise ExportRejected("public snapshot policy rejected the source tree", violations)

        excluded_counts: dict[str, int] = {}
        for item in excluded:
            excluded_counts[item["reason"]] = excluded_counts.get(item["reason"], 0) + 1
        manifest = {
            "schema_version": 1,
            "exporter": {"name": "export_public_snapshot.py", "version": EXPORTER_VERSION},
            "policy": {"path": config_path, "sha256": config_sha256},
            "inventory": {
                "included_bytes": included_total,
                "included_files": len(records),
                "excluded_by_rule": dict(sorted(excluded_counts.items())),
                "excluded_files": len(excluded),
                "sha256": _inventory_digest(records),
                "manifest_self_excluded": True,
            },
            "files": records,
        }
        manifest_bytes = _canonical_json(manifest)
        manifest_issues = _scan_payload(
            MANIFEST_NAME,
            manifest_bytes,
            policy=policy,
            local_literals=local_literals,
            username_hashes=username_hashes,
            machine_name_hashes=machine_name_hashes,
        )
        if manifest_issues:
            raise ExportRejected("generated manifest violated export policy", manifest_issues)

        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
        for entry, data in included:
            _write_blob(staging, entry, data)
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        executable_paths = [entry.path for entry, _data in included if entry.mode == "100755"]
        public_commit, public_tree = _initialise_single_commit(
            staging,
            executable_paths=executable_paths,
        )

        committed_paths = set(
            _git_text(staging, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        )
        expected_paths = {entry.path for entry, _data in included} | {MANIFEST_NAME}
        if committed_paths != expected_paths:
            raise SnapshotError("candidate commit inventory differs from validated export inventory")
        os.replace(staging, output)
        staging = None
        output_created = True
        accepted_audit = {
            "schema_version": 1,
            "status": "ACCEPTED",
            "exporter_version": EXPORTER_VERSION,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "public_commit": public_commit,
            "public_tree": public_tree,
            "manifest_sha256": _sha256(manifest_bytes),
            "policy": {"path": config_path, "sha256": config_sha256},
            "included_files": len(records),
            "excluded_files": len(excluded),
            "excluded": excluded,
            "checks": [
                "source_head_only",
                "source_worktree_clean",
                "no_special_git_entries",
                "bounded_recursive_archive_scan",
                "forbidden_path_and_content_scan",
                "single_root_commit",
                "candidate_worktree_clean",
                "candidate_inventory_exact",
            ],
        }
        _atomic_json(audit_report, accepted_audit)
        return ExportResult(
            source_commit,
            source_tree,
            public_commit,
            public_tree,
            _sha256(manifest_bytes),
            len(records),
            len(excluded),
            audit_report,
            output,
        )
    except ExportRejected as exc:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        _atomic_json(
            audit_report,
            _rejection_payload(
                source_commit=source_commit,
                source_tree=source_tree,
                config_path=config_path,
                config_sha256=config_sha256,
                violations=exc.violations,
                excluded=excluded,
            ),
        )
        raise
    except BaseException as exc:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        if output_created and output.exists():
            _remove_owned_candidate(output, public_commit)
        if not isinstance(exc, (KeyboardInterrupt, SystemExit)) and not audit_report.exists():
            _atomic_json(
                audit_report,
                _error_payload(
                    source_commit=source_commit,
                    source_tree=source_tree,
                    config_path=config_path,
                    config_sha256=config_sha256,
                    error=exc,
                    excluded=excluded,
                ),
            )
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="clean Git repository to export (default: project root)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="new directory for the single-commit candidate")
    mode.add_argument(
        "--verify-public-tree",
        action="store_true",
        help="scan the checked-out public HEAD and reject every private-export exclusion",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="tracked, repository-relative export policy path",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        help=f"sidecar audit report (default: OUTPUT{AUDIT_SUFFIX})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify_public_tree:
        if args.audit_report is not None:
            print(
                json.dumps(
                    {"status": "ERROR", "error": "--audit-report is only valid with --output"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 3
        try:
            verified = verify_public_tree(args.source_root, config_path=args.config)
        except ExportRejected as exc:
            safe_rule_counts: dict[str, int] = {}
            for item in exc.violations:
                safe_rule_counts[item.rule] = safe_rule_counts.get(item.rule, 0) + 1
            print(
                json.dumps(
                    {
                        "status": "REJECTED",
                        "violation_count": len(exc.violations),
                        "violations": [
                            {"rule": rule, "count": safe_rule_counts[rule]}
                            for rule in sorted(safe_rule_counts)
                        ],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        except (OSError, SnapshotError) as exc:
            print(
                json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 3
        print(
            json.dumps(
                {
                    "status": "ACCEPTED",
                    "mode": "public-tree-verification",
                    "source_commit": verified.source_commit,
                    "source_tree": verified.source_tree,
                    "scanned_files": verified.scanned_files,
                },
                ensure_ascii=False,
            )
        )
        return 0

    assert args.output is not None
    output = args.output.resolve(strict=False)
    audit = (args.audit_report or default_audit_path(output)).resolve(strict=False)
    try:
        result = export_snapshot(
            args.source_root,
            output,
            config_path=args.config,
            audit_report=audit,
        )
    except ExportRejected as exc:
        print(
            json.dumps(
                {
                    "status": "REJECTED",
                    "violations": len(exc.violations),
                    "audit_report": str(audit),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, SnapshotError) as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error": str(exc), "audit_report": str(audit)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3
    print(
        json.dumps(
            {
                "status": "ACCEPTED",
                "source_commit": result.source_commit,
                "public_commit": result.public_commit,
                "included_files": result.included_files,
                "excluded_files": result.excluded_files,
                "manifest_sha256": result.manifest_sha256,
                "output": str(result.output),
                "audit_report": str(result.audit_report),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
