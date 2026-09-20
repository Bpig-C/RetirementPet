"""Real SMTC session child (V13-05 native integration).

Plays a locally generated silent WAV through a headless ``MediaPlayer`` so
the process owns a REAL Windows system media session.  The parent test
observes appear/disappear and drives play/pause through the bridge.

Usage::

    python _media_session_child.py <wav_path> <ready_marker> <stop_file>

The child plays until ``<stop_file>`` exists or 60 s elapse, then exits
(which removes the session).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    wav, ready_marker, stop_file = sys.argv[1], sys.argv[2], sys.argv[3]
    import asyncio
    import hashlib
    import json

    from winrt.windows.foundation import Uri
    from winrt.windows.media.core import MediaSource
    from winrt.windows.media.playback import MediaPlayer

    async def run() -> None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Mgr,
        )

        def session_ids(manager) -> set[str]:
            found = set()
            try:
                for candidate in manager.get_sessions():
                    aumid = candidate.source_app_user_model_id or ""
                    found.add(hashlib.sha256(
                        aumid.encode("utf-8")).hexdigest()[:16])
            except Exception:  # noqa: BLE001 - best effort identity scan
                pass
            return found

        manager = await Mgr.request_async()
        before = session_ids(manager)
        player = MediaPlayer()
        player.source = MediaSource.create_from_uri(
            Uri(Path(wav).resolve().as_uri()))
        player.is_muted = True
        player.is_looping_enabled = True
        player.play()
        # SMTC registration is asynchronous: our own session shows up in
        # the global list a moment after play() - identify it as the NEW
        # id that was not present before we started playing
        session_id = ""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            new_ids = session_ids(manager) - before
            if new_ids:
                session_id = sorted(new_ids)[0]
                break
            await asyncio.sleep(0.2)
        Path(ready_marker).write_text(
            json.dumps({"session_id": session_id}), encoding="utf-8")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if os.path.exists(stop_file):
                break
            await asyncio.sleep(0.2)
        player.pause()
        player.close()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
