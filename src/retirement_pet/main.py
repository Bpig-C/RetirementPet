"""Entry point for RetirementPet (source runs and the PyInstaller build)."""

from __future__ import annotations

import sys


def _write_local_import_canary(argv: list[str]) -> int:
    """Run the frozen spawn path without opening a pet window or library."""
    import json
    import os
    import tempfile
    from pathlib import Path

    from retirement_pet.petpack.local_import import (
        ISOLATED_IMPORT_PROTOCOL_VERSION,
        LocalImportError,
        preflight_local_pack,
    )

    def one_value(flag: str, *, required: bool = True) -> str | None:
        if argv.count(flag) != 1:
            if not required and flag not in argv:
                return None
            raise ValueError(flag)
        index = argv.index(flag)
        if index + 1 >= len(argv):
            raise ValueError(flag)
        return argv[index + 1]

    try:
        output = Path(one_value("--local-import-preflight-out"))
        pack = Path(one_value("--local-import-pack"))
        timeout_raw = one_value(
            "--local-import-preflight-timeout-ms", required=False)
        timeout_ms = 60_000 if timeout_raw is None else int(timeout_raw)
        if not 1 <= timeout_ms <= 60_000:
            raise ValueError("timeout")
    except (TypeError, ValueError):
        return 2

    result = {
        "schema": 1,
        "operation": "local_import_preflight",
        "ipc_protocol": ISOLATED_IMPORT_PROTOCOL_VERSION,
        "result": "ERROR",
        "archive_sha256": None,
        "content_digest": None,
        "character_ids": [],
        "first_frames_verified": False,
        "child_exit_code": None,
        "child_reaped": False,
    }
    exit_code = 2
    try:
        preview = preflight_local_pack(
            pack, timeout_seconds=timeout_ms / 1000.0)
        result.update({
            "result": "PASS",
            "archive_sha256": preview.archive_sha256,
            "content_digest": preview.revision_key.content_digest,
            "character_ids": list(preview.character_ids),
            "first_frames_verified": preview.first_frames_verified,
            "child_exit_code": 0,
            "child_reaped": True,
        })
        exit_code = 0
    except LocalImportError as exc:
        result["child_reaped"] = bool(exc.child_reaped)
        result["child_exit_code"] = exc.child_exit_code
        if "超时" in str(exc):
            result["result"] = "TIMEOUT"
            exit_code = 1
        else:
            result["result"] = "REJECT"
            exit_code = 1
    except Exception:  # noqa: BLE001 - canary must have stable output/exit
        result["result"] = "ERROR"
        exit_code = 2

    temporary_name = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            result, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", delete=False,
                dir=output.parent, prefix=f".{output.name}.", suffix=".tmp") \
                as handle:
            temporary_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except OSError:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        return 2
    return exit_code


def main() -> int:
    # Required by Windows ``spawn`` and the PyInstaller bootloader.  The local
    # PetPack inspector runs hostile ZIP/PNG work in a disposable child; in a
    # frozen EXE this dispatch must happen before normal argument handling or
    # the child would recursively start the desktop application.
    import multiprocessing

    multiprocessing.freeze_support()

    if "--local-import-preflight-out" in sys.argv:
        return _write_local_import_canary(sys.argv[1:])

    if "--build-attestation-v2-out" in sys.argv:
        from pathlib import Path

        from retirement_pet.build_info import (
            BuildInfoError,
            build_attestation,
            write_build_attestation,
        )

        idx = sys.argv.index("--build-attestation-v2-out")
        if idx + 1 >= len(sys.argv):
            return 2
        try:
            write_build_attestation(
                Path(sys.argv[idx + 1]), build_attestation())
            return 0
        except (BuildInfoError, OSError):
            return 2

    if "--startup-command-out" in sys.argv:
        from pathlib import Path

        from retirement_pet.startup import build_startup_command

        idx = sys.argv.index("--startup-command-out")
        if idx + 1 >= len(sys.argv):
            return 2
        try:
            output = Path(sys.argv[idx + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(build_startup_command(), encoding="utf-8")
            return 0
        except OSError:
            return 2

    if "--build-info" in sys.argv or "--build-info-out" in sys.argv:
        import json
        from pathlib import Path

        from retirement_pet.build_info import (
            BuildInfoError,
            load_build_info,
            write_build_info,
        )

        try:
            info = load_build_info()
            if "--build-info-out" in sys.argv:
                idx = sys.argv.index("--build-info-out")
                if idx + 1 >= len(sys.argv):
                    return 2
                write_build_info(Path(sys.argv[idx + 1]), info)
            else:
                print(json.dumps(info, ensure_ascii=False, sort_keys=True))
            return 0
        except BuildInfoError:
            # Avoid printing resource paths or backend details from a broken
            # artifact.  Release harnesses use only the stable exit code.
            return 2

    # Structured IPC client mode: send one command to the running primary
    # instance and exit (harness / diagnostic path, not a second window).
    if "--ipc-send" in sys.argv:
        from PySide6.QtCore import QCoreApplication

        from retirement_pet.single_instance import send_command

        QCoreApplication(sys.argv[:1])
        idx = sys.argv.index("--ipc-send")
        command = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "show"
        return 0 if send_command(command) else 1

    # Agent CLI (V13-02): query/operate the RUNNING instance through the
    # local agent protocol; never a second window or a second store.
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        from retirement_pet.agent_cli import run_cli

        return run_cli(sys.argv[2:])

    from retirement_pet.app import PetApplication

    app = PetApplication(sys.argv)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
