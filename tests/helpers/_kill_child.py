"""Child/verifier process for the parent-driven Windows crash matrix.

The ``run`` mode reaches one stable fault-point, publishes a durable JSON
ACK, and parks. It never kills itself: only the parent harness calls
``TerminateProcess``. The ``verify`` mode opens the library in a genuinely
fresh process and emits a compact consistency snapshot.

Usage::

    python _kill_child.py run <library_root> <marker> <point> <token> \
        <reference_pack> <official_pack>
    python _kill_child.py verify <library_root>
"""

from __future__ import annotations

import json
import os
import sys
import time
import ctypes
import ctypes.wintypes as wt
from pathlib import Path
from typing import NoReturn

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent / "src"))


SWITCH_BEFORE_SELECTION_COMMIT = "switch.before_selection_commit"
SWITCH_AFTER_SELECTION_COMMIT = \
    "switch.after_selection_commit_before_return"
INSTALL_AFTER_PUBLISH = "install.after_publish_before_catalog_commit"

_RUN_POINTS = {
    SWITCH_BEFORE_SELECTION_COMMIT,
    SWITCH_AFTER_SELECTION_COMMIT,
    INSTALL_AFTER_PUBLISH,
}


def _current_process_creation_time() -> int:
    """Return the stable Windows process identity paired with ``pid``."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wt.BOOL
    creation = wt.FILETIME()
    exit_time = wt.FILETIME()
    kernel_time = wt.FILETIME()
    user_time = wt.FILETIME()
    if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(), ctypes.byref(creation),
            ctypes.byref(exit_time), ctypes.byref(kernel_time),
            ctypes.byref(user_time)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


def _publish_durable_ack(marker: Path, point: str, token: str) -> None:
    """Make the marker visible only after its complete JSON has been fsynced."""
    payload = json.dumps(
        {
            "creation_time_100ns": _current_process_creation_time(),
            "point": point,
            "pid": os.getpid(),
            "token": token,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    with open(temporary, "xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, marker)


def _park_for_parent_kill() -> NoReturn:
    while True:
        time.sleep(1.0)


class _Surface:
    def __init__(self):
        self.renderer = None

    def swap_renderer(self, renderer) -> None:
        self.renderer = renderer

    def paint_first_frame(self, renderer) -> bool:
        image = renderer.first_frame_image()
        return image is not None and not image.isNull()


def _revision(library, package_id: str):
    return next(
        record.revision_key for record in library.list_revisions()
        if record.revision_key.pack.package_id == package_id
    )


def _commit_character(
        library, switcher, surface: _Surface,
        package_id: str, character_id: str) -> None:
    request = switcher.request(
        _revision(library, package_id), character_id)
    candidate = switcher.prepare(request)
    if candidate is None:
        raise RuntimeError(f"prepare failed for {package_id}.{character_id}")
    if not switcher.swap_and_commit(request, surface):
        raise RuntimeError(f"commit failed for {package_id}.{character_id}")


def _run_switch_boundary(
        library_root: Path, marker: Path, point: str, token: str) -> int:
    from PySide6.QtWidgets import QApplication

    qt_application = QApplication.instance() or QApplication([])

    from retirement_pet.lifecycle import PackLibrary
    from retirement_pet.lru_cache import LruByteCache
    from retirement_pet.switcher import ActiveSelectionStore, RuntimeSwitcher

    library = PackLibrary(library_root)
    store = ActiveSelectionStore(library)
    switcher = RuntimeSwitcher(
        library, store, asset_cache=LruByteCache(48 * 1024 * 1024))
    surface = _Surface()

    # Establish an OLD durable truth before arming either side of the demo
    # selection commit boundary.
    _commit_character(
        library, switcher, surface, "retirement-cat-official", "cat")

    original_commit = store.commit_if_newer
    if point == SWITCH_BEFORE_SELECTION_COMMIT:
        def pause_before_commit(selection, slot="active"):
            _publish_durable_ack(marker, point, token)
            _park_for_parent_kill()
    elif point == SWITCH_AFTER_SELECTION_COMMIT:
        def pause_after_commit(selection, slot="active"):
            won = original_commit(selection, slot)
            if not won:
                raise RuntimeError("candidate selection lost CAS before ACK")
            _publish_durable_ack(marker, point, token)
            _park_for_parent_kill()
    else:  # defensive; dispatch already validates the point
        raise ValueError(f"unsupported switch point: {point}")

    # Instance replacement is a test-only seam around the actual durable
    # ActiveSelectionStore transaction. Production code remains untouched.
    store.commit_if_newer = (
        pause_before_commit
        if point == SWITCH_BEFORE_SELECTION_COMMIT
        else pause_after_commit
    )
    _commit_character(library, switcher, surface, "minimal-static", "demo")
    del qt_application
    raise AssertionError("parent-kill boundary unexpectedly returned")


def _run_install_boundary(
        library_root: Path, marker: Path, point: str, token: str,
        reference_pack: Path) -> int:
    from retirement_pet.lifecycle import PackLibrary

    library = PackLibrary(library_root)

    def pause_after_publish(intent, _revision_key):
        target = Path(intent["target"])
        if not target.is_file():
            raise RuntimeError("publish target missing before catalog boundary")
        _publish_durable_ack(marker, point, token)
        _park_for_parent_kill()

    # install() has durably written PUBLISH_INTENT and moved the media to its
    # immutable target before this catalog-commit seam is entered.
    library._commit_install = pause_after_publish
    library.install(reference_pack)
    raise AssertionError("parent-kill boundary unexpectedly returned")


def _read_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _selection_payload(selection) -> dict | None:
    if selection is None:
        return None
    return {
        "publisher_id": selection.publisher_id,
        "package_id": selection.package_id,
        "package_version": selection.package_version,
        "content_digest": selection.content_digest,
        "character_fqid": selection.character_fqid,
        "variant_id": selection.variant_id,
        "config_revision_id": selection.config_revision_id,
        "generation": selection.generation,
        "commit_sequence": selection.commit_sequence,
    }


def _verify_fresh(library_root: Path) -> int:
    from PySide6.QtWidgets import QApplication

    qt_application = QApplication.instance() or QApplication([])

    from retirement_pet.lifecycle import PackLibrary
    from retirement_pet.lru_cache import LruByteCache
    from retirement_pet.switcher import ActiveSelectionStore, RuntimeSwitcher

    library = PackLibrary(library_root)
    store = ActiveSelectionStore(library)
    switcher = None
    try:
        records = sorted(
            library.list_revisions(), key=lambda item: str(item.revision_key))
        catalog = [{
            "publisher_id": item.revision_key.pack.publisher_id,
            "package_id": item.revision_key.pack.package_id,
            "package_version": item.revision_key.package_version,
            "content_digest": item.revision_key.content_digest,
            "builtin": item.builtin,
            "file_exists": item.pack_path.is_file(),
        } for item in records]

        active = store.get("active")
        active_record_exists = None
        active_renderable = None
        if active is not None:
            record = library.get_revision(active.revision_key())
            active_record_exists = bool(
                record is not None and record.pack_path.is_file())
            if active_record_exists:
                switcher = RuntimeSwitcher(
                    library, store,
                    asset_cache=LruByteCache(48 * 1024 * 1024),
                )
                request = switcher.request(
                    active.revision_key(), active.character_fqid,
                    variant_id=active.variant_id,
                    config_revision_id=active.config_revision_id,
                )
                active_renderable = switcher.prepare(request) is not None

        events = _read_events(library.journal_dir / "events.jsonl")
        install_events = [
            event for event in events
            if event.get("event") == "INSTALL_COMMITTED"
        ]
        snapshot = {
            "active": _selection_payload(active),
            "active_record_file_exists": active_record_exists,
            "active_renderable": active_renderable,
            "catalog": catalog,
            "intent_files": sorted(
                path.name for path in
                library.journal_dir.glob("intent-*.json")),
            "staging_entries": sorted(
                path.name for path in library.staging_dir.iterdir()),
            "install_event_names": [
                event["event"] for event in install_events],
            "install_event_transaction_ids": [
                event.get("transaction_id") for event in install_events],
        }
        print(
            "VERIFY_JSON="
            + json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        return 0
    finally:
        if switcher is not None:
            switcher.shutdown()
        store.close()
        library.close()
        del qt_application


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("mode required: run or verify")
    mode = args[0]
    if mode == "verify":
        if len(args) != 2:
            raise SystemExit("verify requires <library_root>")
        return _verify_fresh(Path(args[1]))
    if mode != "run" or len(args) != 7:
        raise SystemExit(
            "run requires <library_root> <marker> <point> <token> "
            "<reference_pack> <official_pack>"
        )

    library_root = Path(args[1])
    marker = Path(args[2])
    point = args[3]
    token = args[4]
    reference_pack = Path(args[5])
    official_pack = Path(args[6])
    if point not in _RUN_POINTS:
        raise SystemExit(f"unsupported fault point: {point}")

    # This is not a kill barrier. It gives the parent an authenticated PID
    # for failure-path cleanup even if setup fails before the real boundary.
    _publish_durable_ack(
        marker.with_name(marker.name + ".started"),
        "harness.started", token,
    )

    if point == INSTALL_AFTER_PUBLISH:
        return _run_install_boundary(
            library_root, marker, point, token, reference_pack)

    # The parent prepared both revisions in this scenario; asserting the
    # paths here catches a malformed harness invocation before the ACK.
    if not reference_pack.is_file() or not official_pack.is_file():
        raise SystemExit("switch fixture media missing")
    return _run_switch_boundary(library_root, marker, point, token)


if __name__ == "__main__":
    sys.exit(main())
