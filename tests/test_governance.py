"""M7 content governance: rights per channel, warnings snapshot, privacy."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

import pytest

from retirement_pet.petpack.diagnostics import (
    RGT_E001_INVALID_RIGHTS,
    RGT_E002_MISSING_REF,
    RGT_E003_OFFICIAL_UNKNOWN_RIGHTS,
)
from retirement_pet.petpack.validator import validate_petpack

ROOT = Path(__file__).resolve().parent.parent
REF_PACK = ROOT / "tests" / "fixtures" / "petpack" / "minimal-static.petpack"


def _manifest_with_rights(basis: str) -> dict:
    """Rebuild the reference manifest with an adjustable rights basis."""
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import make_manifest, png_bytes

    manifest = make_manifest()
    manifest["rights_declarations"] = [{
        "id": "rights.main", "basis": basis,
        "claimant_ref": "community.example",
        "license": {"spdx": "MIT" if basis == "open_license" else None,
                    "legal_file_ref": None,
                    "custom_name": None if basis == "open_license"
                    else "custom"},
        "scope_claimed": ["personal_use"], "attribution": "test",
        "notes": None}]
    for asset in manifest["assets"]:
        asset["rights_ref"] = "rights.main"
    return manifest


def _build(manifest: dict) -> bytes:
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import build_pack, png_bytes

    return build_pack(manifest, {
        "assets/thumb.png": png_bytes(4, 4),
        "assets/idle_0.png": png_bytes(8, 8),
    })


# -- per-channel rights rules -------------------------------------------------------


def test_original_basis_accepts_on_both_channels():
    report = validate_petpack(REF_PACK.read_bytes())
    assert report.accepted


def test_unknown_basis_on_local_channel_is_warning_not_reject():
    pack = _build(_manifest_with_rights("unknown"))
    report = validate_petpack(pack)  # LOCAL_IMPORTED by default
    assert report.accepted
    assert report.result == "ACCEPT_WITH_DEGRADATION"
    assert any(d.code == "PPK-RGT-W001" for d in report.diagnostics)


def test_unknown_basis_on_official_channel_rejects():
    pack = _build(_manifest_with_rights("unknown"))
    report = validate_petpack(pack, trust_channel="BUILTIN_OFFICIAL")
    assert not report.accepted
    assert any(d.code == RGT_E003_OFFICIAL_UNKNOWN_RIGHTS
               for d in report.diagnostics)


def test_licensed_without_license_evidence_rejects():
    manifest = _manifest_with_rights("licensed")
    manifest["rights_declarations"][0]["license"] = {
        "spdx": None, "legal_file_ref": None, "custom_name": None}
    report = validate_petpack(_build(manifest))
    assert not report.accepted
    assert any(d.code == RGT_E001_INVALID_RIGHTS for d in report.diagnostics)


def test_open_license_with_spdx_accepts():
    report = validate_petpack(_build(_manifest_with_rights("open_license")))
    assert report.accepted, [d.code for d in report.diagnostics]


def test_unknown_cannot_claim_official_distribution_scope():
    manifest = _manifest_with_rights("unknown")
    manifest["rights_declarations"][0]["scope_claimed"] = [
        "official_distribution"]
    report = validate_petpack(_build(manifest))
    assert not report.accepted
    assert any(d.code == RGT_E001_INVALID_RIGHTS for d in report.diagnostics)


def test_missing_source_ref_rejects():
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import make_manifest, build_pack, png_bytes

    manifest = make_manifest()
    manifest["assets"][0].pop("source_ref")
    report = validate_petpack(build_pack(manifest, {
        "assets/thumb.png": png_bytes(4, 4),
        "assets/idle_0.png": png_bytes(8, 8)}))
    assert not report.accepted
    assert any(d.code == RGT_E002_MISSING_REF for d in report.diagnostics)


# -- receipt records the acknowledged warning snapshot ---------------------------------


def test_receipt_captures_warning_snapshot(tmp_path):
    from retirement_pet.lifecycle import LifecycleError, PackLibrary

    library = PackLibrary(tmp_path / "library")
    pack = _build(_manifest_with_rights("unknown"))
    path = tmp_path / "unknown-rights.petpack"
    path.write_bytes(pack)

    report = validate_petpack(pack)
    warning_codes = [d.code for d in report.diagnostics
                     if d.severity.value == "WARNING"]
    assert warning_codes == ["PPK-RGT-W001"]

    with pytest.raises(LifecycleError):
        library.install(path)
    assert library.list_revisions() == []

    record = library.install(
        path, warnings_acknowledged=tuple(warning_codes))
    receipts = list(library.receipts_dir.glob("*.json"))
    assert len(receipts) == 1
    receipt_path = receipts[0]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["content_digest"] == record.revision_key.content_digest
    # The engine writes the acknowledged snapshot atomically; callers do not
    # patch an already-published receipt after the fact.
    saved = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert saved["warnings_acknowledged"] == ["PPK-RGT-W001"]
    library.close()


# -- privacy: network-zero audit --------------------------------------------------------


_OUTBOUND_MODULES = frozenset({
    "boto3",
    "botocore",
    "curl_cffi",
    "requests",
    "urllib.request",
    "urllib3",
    "http.client",
    "httpcore",
    "ftplib",
    "grpc",
    "imaplib",
    "nntplib",
    "paramiko",
    "poplib",
    "pycurl",
    "PySide6.QtWebSockets",
    "smtplib",
    "ssl",
    "telnetlib",
    "websocket",
    "websockets",
    "xmlrpc.client",
    "aiohttp",
    "httpx",
    "socket",
})
_QT_NETWORK_MODULE = "PySide6.QtNetwork"
_ALLOWED_QT_LOCAL_NAMES = frozenset({"QLocalServer", "QLocalSocket"})
_ASYNCIO_NETWORK_NAMES = frozenset({"open_connection", "start_server"})
_ASYNCIO_LOOP_FACTORIES = frozenset({
    "get_event_loop", "get_running_loop", "new_event_loop",
})
_ASYNCIO_LOOP_NETWORK_NAMES = frozenset({
    "create_connection", "create_datagram_endpoint", "create_server",
    "sock_connect", "sock_sendall",
})
_ASYNCIO_SUBPROCESS_NAMES = frozenset({
    "create_subprocess_exec", "create_subprocess_shell",
})
_SUBPROCESS_CALL_NAMES = frozenset({
    "Popen", "call", "check_call", "check_output", "run",
})
_NETWORK_EXECUTABLES = frozenset({"curl", "curl.exe", "wget", "wget.exe"})


def _is_outbound_module(module: str) -> bool:
    return any(module == blocked or module.startswith(blocked + ".")
               for blocked in _OUTBOUND_MODULES)


def _is_named_attribute(
        expression: ast.expr, owners: set[str], attribute: str) -> bool:
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == attribute
        and isinstance(expression.value, ast.Name)
        and expression.value.id in owners
    )


def _is_getattr_of(
        expression: ast.expr, owners: set[str], attribute: str) -> bool:
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "getattr"
        and len(expression.args) >= 2
        and isinstance(expression.args[0], ast.Name)
        and expression.args[0].id in owners
        and isinstance(expression.args[1], ast.Constant)
        and expression.args[1].value == attribute
    )


def _literal_command(expression: ast.expr) -> tuple[bool, set[str]]:
    """Return ``(executable_known, normalized_visible_words)``."""
    values: list[str] = []
    executable_known = False
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        values.append(expression.value)
        executable_known = bool(expression.value.strip())
    elif isinstance(expression, (ast.List, ast.Tuple)):
        if expression.elts:
            first = expression.elts[0]
            executable_known = (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and bool(first.value.strip())
            )
        values.extend(
            item.value for item in expression.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    tokens: set[str] = set()
    for value in values:
        for word in value.strip().split():
            normalized = word.strip("\"'()")
            normalized = normalized.replace("/", "\\").rsplit("\\", 1)[-1]
            if normalized:
                tokens.add(normalized.lower())
    return executable_known, tokens


def _outbound_imports(source: str) -> list[str]:
    """Return obvious imports that can add outbound networking capability.

    AST inspection avoids the former regex's literal ``0x08``/``\\b`` typo,
    and covers comma imports plus ``from urllib import request`` forms.  Qt's
    network module is allowed only for the two local IPC classes used by the
    single-instance guard.  This is a preventive source gate, not runtime
    proof: release acceptance separately observes DNS/socket activity.
    """
    offenders: list[str] = []
    tree = ast.parse(source)
    importlib_bindings = {"importlib"}
    builtins_bindings = {"builtins"}
    dynamic_import_bindings = {"__import__"}
    asyncio_bindings = {"asyncio"}
    asyncio_stream_bindings: set[str] = set()
    asyncio_network_bindings: set[str] = set()
    asyncio_loop_bindings: set[str] = set()
    asyncio_subprocess_bindings: set[str] = set()
    subprocess_bindings = {"subprocess"}
    subprocess_call_bindings: set[str] = set()
    os_bindings = {"os"}
    os_system_bindings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == "importlib":
                    importlib_bindings.add(bound)
                elif alias.name == "builtins":
                    builtins_bindings.add(bound)
                elif alias.name == "asyncio":
                    asyncio_bindings.add(bound)
                elif alias.name == "asyncio.streams":
                    if alias.asname:
                        asyncio_stream_bindings.add(bound)
                    else:
                        asyncio_bindings.add(bound)
                elif alias.name == "subprocess":
                    subprocess_bindings.add(bound)
                elif alias.name == "os":
                    os_bindings.add(bound)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if module == "importlib" and alias.name == "import_module":
                    dynamic_import_bindings.add(bound)
                elif module == "builtins" and alias.name == "__import__":
                    dynamic_import_bindings.add(bound)
                elif module == "asyncio" \
                        and alias.name in _ASYNCIO_NETWORK_NAMES:
                    asyncio_network_bindings.add(bound)
                elif module == "asyncio" and alias.name == "streams":
                    asyncio_stream_bindings.add(bound)
                elif module == "asyncio" \
                        and alias.name in _ASYNCIO_SUBPROCESS_NAMES:
                    asyncio_subprocess_bindings.add(bound)
                elif module == "subprocess" \
                        and alias.name in _SUBPROCESS_CALL_NAMES:
                    subprocess_call_bindings.add(bound)
                elif module == "os" and alias.name == "system":
                    os_system_bindings.add(bound)

    def is_dynamic_import_callable(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in dynamic_import_bindings
        ) or _is_named_attribute(
            expression, importlib_bindings, "import_module"
        ) or _is_named_attribute(
            expression, builtins_bindings, "__import__"
        ) or _is_getattr_of(
            expression, importlib_bindings, "import_module"
        ) or _is_getattr_of(
            expression, builtins_bindings, "__import__"
        )

    def dynamic_import_target(expression: ast.expr) -> str | None:
        if not isinstance(expression, ast.Call) \
                or not is_dynamic_import_callable(expression.func) \
                or not expression.args:
            return None
        target = expression.args[0]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            return target.value
        return None

    def is_asyncio_network_callable(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in asyncio_network_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _ASYNCIO_NETWORK_NAMES
            and isinstance(expression.value, ast.Name)
            and expression.value.id in asyncio_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _ASYNCIO_NETWORK_NAMES
            and isinstance(expression.value, ast.Name)
            and expression.value.id in asyncio_stream_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _ASYNCIO_NETWORK_NAMES
            and isinstance(expression.value, ast.Attribute)
            and expression.value.attr == "streams"
            and isinstance(expression.value.value, ast.Name)
            and expression.value.value.id in asyncio_bindings
        )

    def is_subprocess_callable(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in subprocess_call_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _SUBPROCESS_CALL_NAMES
            and isinstance(expression.value, ast.Name)
            and expression.value.id in subprocess_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _SUBPROCESS_CALL_NAMES
            and dynamic_import_target(expression.value) == "subprocess"
        )

    def is_asyncio_subprocess_callable(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in asyncio_subprocess_bindings
        ) or (
            isinstance(expression, ast.Attribute)
            and expression.attr in _ASYNCIO_SUBPROCESS_NAMES
            and isinstance(expression.value, ast.Name)
            and expression.value.id in asyncio_bindings
        )

    def is_os_system_callable(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in os_system_bindings
        ) or _is_named_attribute(expression, os_bindings, "system")

    def command_argument(call: ast.Call) -> ast.expr | None:
        if call.args:
            return call.args[0]
        for keyword in call.keywords:
            if keyword.arg in {"args", "command", "cmd", "program"}:
                return keyword.value
        return None

    def executable_override(call: ast.Call) -> ast.expr | None:
        for keyword in call.keywords:
            if keyword.arg == "executable":
                return keyword.value
        return None

    def is_asyncio_loop(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in asyncio_loop_bindings
        ) or (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Attribute)
            and expression.func.attr in _ASYNCIO_LOOP_FACTORIES
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id in asyncio_bindings
        )

    # Propagate simple callable/loop aliases to a fixed point.  This catches
    # ``load = importlib.import_module`` and ``load = getattr(...)`` without
    # pretending to be a whole-program Python analyser.
    assignments: list[tuple[list[str], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets
                     if isinstance(target, ast.Name)]
            assignments.append((names, node.value))
        elif isinstance(node, ast.AnnAssign) \
                and isinstance(node.target, ast.Name) \
                and node.value is not None:
            assignments.append(([node.target.id], node.value))
    changed = True
    while changed:
        changed = False
        for names, value in assignments:
            if not names:
                continue
            if is_dynamic_import_callable(value):
                for name in names:
                    if name not in dynamic_import_bindings:
                        dynamic_import_bindings.add(name)
                        changed = True
            if is_asyncio_network_callable(value):
                for name in names:
                    if name not in asyncio_network_bindings:
                        asyncio_network_bindings.add(name)
                        changed = True
            if is_asyncio_loop(value):
                for name in names:
                    if name not in asyncio_loop_bindings:
                        asyncio_loop_bindings.add(name)
                        changed = True
            if is_subprocess_callable(value):
                for name in names:
                    if name not in subprocess_call_bindings:
                        subprocess_call_bindings.add(name)
                        changed = True
            if is_asyncio_subprocess_callable(value):
                for name in names:
                    if name not in asyncio_subprocess_bindings:
                        asyncio_subprocess_bindings.add(name)
                        changed = True
            if dynamic_import_target(value) == "subprocess":
                for name in names:
                    if name not in subprocess_bindings:
                        subprocess_bindings.add(name)
                        changed = True
            if is_os_system_callable(value):
                for name in names:
                    if name not in os_system_bindings:
                        os_system_bindings.add(name)
                        changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                if _is_outbound_module(module) \
                        or module == _QT_NETWORK_MODULE:
                    offenders.append(f"line {node.lineno}: import {module}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported = f"{module}.{alias.name}" if module else alias.name
                if _is_outbound_module(module) or _is_outbound_module(imported):
                    offenders.append(
                        f"line {node.lineno}: from {module} import {alias.name}")
                elif module == _QT_NETWORK_MODULE \
                        and alias.name not in _ALLOWED_QT_LOCAL_NAMES:
                    offenders.append(
                        f"line {node.lineno}: from {module} import {alias.name}")
                elif module == "PySide6" and alias.name == "QtNetwork":
                    offenders.append(
                        f"line {node.lineno}: from PySide6 import QtNetwork")
        elif isinstance(node, ast.Call):
            function = node.func
            if is_dynamic_import_callable(function):
                if not node.args or not isinstance(node.args[0], ast.Constant) \
                        or not isinstance(node.args[0].value, str):
                    offenders.append(
                        f"line {node.lineno}: dynamic import has unknown target")
                    continue
                module = node.args[0].value
                if _is_outbound_module(module) or module == _QT_NETWORK_MODULE:
                    offenders.append(
                        f"line {node.lineno}: dynamic import {module}")
                continue
            asyncio_loop_call = (
                isinstance(function, ast.Attribute)
                and function.attr in _ASYNCIO_LOOP_NETWORK_NAMES
                and is_asyncio_loop(function.value)
            )
            if is_asyncio_network_callable(function) or asyncio_loop_call:
                offenders.append(
                    f"line {node.lineno}: asyncio network API {ast.unparse(function)}")
                continue
            asyncio_subprocess_call = is_asyncio_subprocess_callable(function)
            command = command_argument(node)
            if asyncio_subprocess_call:
                command_known, command_tokens = (
                    _literal_command(command)
                    if command is not None else (False, set()))
                override = executable_override(node)
                override_known, override_tokens = (
                    _literal_command(override)
                    if override is not None else (True, set()))
                command_tokens |= override_tokens
                network_commands = command_tokens & _NETWORK_EXECUTABLES
                if network_commands:
                    offenders.append(
                        f"line {node.lineno}: asyncio network executable "
                        f"{sorted(network_commands)[0]}")
                elif not command_known or not override_known:
                    offenders.append(
                        f"line {node.lineno}: asyncio subprocess has "
                        "unknown command")
                continue
            subprocess_call = is_subprocess_callable(function)
            if subprocess_call:
                command_known, command_tokens = (
                    _literal_command(command)
                    if command is not None else (False, set()))
                override = executable_override(node)
                override_known, override_tokens = (
                    _literal_command(override)
                    if override is not None else (True, set()))
                command_tokens |= override_tokens
                network_commands = command_tokens & _NETWORK_EXECUTABLES
                if network_commands:
                    offenders.append(
                        f"line {node.lineno}: network executable "
                        f"{sorted(network_commands)[0]}")
                elif not command_known or not override_known:
                    offenders.append(
                        f"line {node.lineno}: subprocess has unknown command")
                continue
            if is_os_system_callable(function):
                command_known, command_tokens = (
                    _literal_command(command)
                    if command is not None else (False, set()))
                network_commands = command_tokens & _NETWORK_EXECUTABLES
                if network_commands:
                    offenders.append(
                        f"line {node.lineno}: os.system network executable "
                        f"{sorted(network_commands)[0]}")
                elif not command_known:
                    offenders.append(
                        f"line {node.lineno}: os.system has unknown command")
    return offenders


@pytest.mark.parametrize("source", (
    "import requests\n",
    "from socket import socket\n",
    "import os, httpx as client\n",
    "from urllib import request\n",
    "from http import client\n",
    "from PySide6.QtNetwork import QNetworkAccessManager\n",
    "from PySide6 import QtNetwork\n",
    "__import__('socket')\n",
    "import importlib\nimportlib.import_module('requests')\n",
    "import importlib as il\nil.import_module('requests')\n",
    "import importlib\nload = importlib.import_module\nload('httpcore')\n",
    "import importlib\nload: object = importlib.import_module\n"
    "load('httpcore')\n",
    "import importlib\nload = getattr(importlib, 'import_module')\n"
    "load('curl_cffi.requests')\n",
    "import importlib\ngetattr(importlib, 'import_module')('pycurl')\n",
    "from importlib import import_module\nimport_module('socket')\n",
    "from importlib import import_module as load\nload('PySide6.QtNetwork')\n",
    "import importlib\nname = 'httpx'\nimportlib.import_module(name)\n",
    "import builtins\nbuiltins.__import__('requests')\n",
    "name = 'requests'\n__import__(name)\n",
    "import urllib3\n",
    "import xmlrpc.client\n",
    "import imaplib\n",
    "import asyncio\nasyncio.open_connection('example.com', 443)\n",
    "import asyncio as aio\naio.start_server(handler, '0.0.0.0', 80)\n",
    "from asyncio import open_connection as connect\n"
    "connect('example.com', 443)\n",
    "import asyncio\nloop = asyncio.get_running_loop()\n"
    "loop.create_connection(factory, 'example.com', 443)\n",
    "from asyncio import streams\n"
    "streams.open_connection('example.com', 443)\n",
    "import asyncio\nloop = asyncio.get_running_loop()\n"
    "loop.create_datagram_endpoint(factory, remote_addr=('example.com', 53))\n",
    "import asyncio\n"
    "asyncio.create_subprocess_exec('curl', 'https://example.com')\n",
    "from PySide6 import QtWebSockets\n",
    "import subprocess\nsubprocess.run(['curl.exe', 'https://example.com'])\n",
    "import subprocess\nsubprocess.run(args=['curl', 'https://example.com'])\n",
    "__import__('subprocess').Popen(['curl', 'https://example.com'])\n",
    "import importlib\n"
    "importlib.import_module('subprocess').run(['curl', 'https://example.com'])\n",
    "sp = __import__('subprocess')\n"
    "sp.Popen(['curl', 'https://example.com'])\n",
    "import subprocess\nlaunch = subprocess.run\n"
    "launch(['curl', 'https://example.com'])\n",
    "import subprocess\ncmd = ['curl', 'https://example.com']\n"
    "subprocess.run(cmd)\n",
    "import subprocess\nexe = 'curl'\n"
    "subprocess.run([exe, '--version'])\n",
    "import subprocess\n"
    "subprocess.Popen(['explorer.exe', '.'], executable='curl.exe')\n",
    "import subprocess\nexe = choose_program()\n"
    "subprocess.Popen(['explorer.exe', '.'], executable=exe)\n",
    "import subprocess\n"
    "subprocess.run(['cmd', '/c', 'curl', 'https://example.com'])\n",
    "from subprocess import Popen as launch\n"
    "launch('wget https://example.com')\n",
    "import os\nos.system('curl https://example.com')\n",
    "import asyncio\nlaunch = asyncio.create_subprocess_exec\n"
    "command = 'curl'\nlaunch(command, 'https://example.com')\n",
))
def test_network_import_guard_has_live_positive_controls(source):
    """A zero-offender scan is evidence only if known bad inputs trip it."""
    assert _outbound_imports(source), source


@pytest.mark.parametrize("source", (
    "import urllib.parse\n",
    "import asyncio\nasyncio.sleep(0)\n",
    "import asyncio\nasyncio.create_subprocess_exec('explorer.exe', '.')\n",
    "import subprocess\nsubprocess.Popen(['explorer.exe', '.'])\n",
    "__import__('subprocess').Popen(['explorer', '.'])\n",
    "import importlib\n"
    "importlib.import_module('subprocess').run(['explorer.exe', '.'])\n",
    "import subprocess\nsubprocess.run(args=['explorer.exe', '.'])\n",
    "import subprocess\n"
    "subprocess.Popen(['explorer.exe', '.'], executable='explorer.exe')\n",
    "import os\nos.system('explorer.exe .')\n",
    "from PySide6.QtNetwork import QLocalServer, QLocalSocket\n",
    "from retirement_pet import socket_adapter\n",
))
def test_network_import_guard_keeps_local_and_non_network_controls(source):
    assert _outbound_imports(source) == [], source


def test_source_tree_has_no_declared_outbound_network_imports():
    """Prevent obvious outbound imports; runtime evidence proves zero use."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _outbound_imports(text):
            offenders.append(f"{path.relative_to(ROOT)}: {match}")
    assert offenders == [], f"outbound network import found: {offenders}"


def test_pack_media_never_declares_remote_locators():
    """Packs are pure data: no asset path may be a URL/UNC."""
    report = validate_petpack(REF_PACK.read_bytes())
    assert report.accepted
    for asset in report.manifest.get("assets", []):
        path = str(asset.get("path", ""))
        assert "://" not in path and not path.startswith("\\\\")


def test_logs_never_contain_user_home_or_music_paths(tmp_path, caplog):
    """Log sanitization canary: absolute user paths stay out of logs."""
    from retirement_pet.settings import SettingsStore

    with caplog.at_level(logging.DEBUG):
        store = SettingsStore(tmp_path / "settings.json")
        store.set("music_paths",
                  [r"X:\PrivateProfile\Music\secret-song.mp3"])
        store.save()
    dumped = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret-song" not in dumped
    assert "PrivateProfile" not in dumped
