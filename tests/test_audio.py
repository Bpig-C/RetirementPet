"""AudioManager degradation: missing backend/files never raise."""

from __future__ import annotations

import logging

import pytest

from retirement_pet.audio import AudioManager


def test_empty_playlist_reports_bubble_not_crash(qt_application):
    mgr = AudioManager()
    errors = []
    mgr.error_message.connect(errors.append)
    assert not mgr.play()
    assert errors and "播放列表" in errors[0]
    assert mgr.is_playing() is False


def test_set_tracks_drops_missing_files(qt_application, tmp_path):
    mgr = AudioManager()
    good1 = tmp_path / "a.mp3"
    good2 = tmp_path / "b.mp3"
    good1.write_bytes(b"x")
    good2.write_bytes(b"x")

    kept = mgr.set_tracks([str(good1), "Z:\\nope\\missing.mp3", str(good2)])
    assert kept == 2
    assert len(mgr.tracks) == 2
    assert mgr.current_track_name() == ""


def test_play_missing_file_degrades(qt_application, tmp_path):
    """Even if a file disappears after validation, play must not crash."""
    mgr = AudioManager()
    track = tmp_path / "gone.mp3"
    track.write_bytes(b"x")
    mgr.set_tracks([str(track)])
    track.unlink()
    # Not playing anything; play() still tries and QMediaPlayer errors land
    # in the error signal, which we simply must survive.
    try:
        mgr.play()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"play() raised: {exc}")


def test_volume_clamped(qt_application):
    mgr = AudioManager(volume=0.35)
    mgr.set_volume(3.0)
    assert mgr.volume == 1.0
    mgr.adjust_volume(-10)
    assert mgr.volume == 0.0


def test_transport_without_backend_is_safe(qt_application):
    mgr = AudioManager()
    mgr.pause()  # no player yet - no-op
    mgr.stop()
    mgr.next()
    mgr.previous()
    assert mgr.is_playing() is False


def _audio_log_dump(caplog) -> str:
    records = [record for record in caplog.records
               if record.name == "retirement_pet.audio"]
    return "\n".join(
        repr(value)
        for record in records
        for value in (
            record.getMessage(), record.args, record.exc_info,
            record.exc_text, record.stack_info, record.__dict__,
        )
    )


def test_missing_music_path_never_enters_complete_log_record(
        qt_application, tmp_path, caplog):
    canary = "AUDIO-MISSING-PATH-CANARY"
    missing = tmp_path / canary / "private-song.mp3"
    mgr = AudioManager()

    with caplog.at_level(logging.DEBUG, logger="retirement_pet.audio"):
        assert mgr.set_tracks([str(missing)]) == 0

    dumped = _audio_log_dump(caplog)
    assert canary not in dumped
    assert str(missing) not in dumped


def test_backend_constructor_error_is_fixed_and_private(
        qt_application, tmp_path, monkeypatch, caplog):
    from PySide6 import QtMultimedia

    canary = "AUDIO-BACKEND-CONSTRUCTOR-CANARY"
    track = tmp_path / "safe.mp3"
    track.write_bytes(b"test")
    mgr = AudioManager()
    mgr.set_tracks([str(track)])
    errors = []
    mgr.error_message.connect(errors.append)

    def explode(*_args, **_kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(QtMultimedia, "QMediaPlayer", explode)
    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        assert mgr.play() is False

    assert errors == ["这台电脑上的音频组件不可用"]
    assert canary not in _audio_log_dump(caplog)


def test_async_backend_error_message_is_not_logged_or_shown(
        qt_application, caplog):
    canary = r"AUDIO-ASYNC-CANARY C:\Private\secret-song.mp3"
    mgr = AudioManager()
    errors = []
    playback = []
    mgr.error_message.connect(errors.append)
    mgr.playback_changed.connect(
        lambda playing, track: playback.append((playing, track)))

    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        mgr._on_error(None, canary)

    assert errors == ["音乐播放失败，请检查音频文件或系统音频设置"]
    assert playback == [(False, "")]
    assert canary not in _audio_log_dump(caplog)
    assert "secret-song" not in _audio_log_dump(caplog)


def test_synchronous_player_start_error_is_contained_and_private(
        qt_application, tmp_path, caplog):
    canary = r"AUDIO-START-CANARY C:\Private\secret-song.mp3"
    track = tmp_path / "safe.mp3"
    track.write_bytes(b"test")
    mgr = AudioManager()
    mgr.set_tracks([str(track)])
    errors = []
    playback = []
    mgr.error_message.connect(errors.append)
    mgr.playback_changed.connect(
        lambda playing, name: playback.append((playing, name)))

    class ExplodingPlayer:
        def setSource(self, _source):  # noqa: N802
            raise RuntimeError(canary)

        def isPlaying(self) -> bool:  # noqa: N802
            return False

    mgr._player = ExplodingPlayer()
    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        assert mgr.play() is False

    assert errors == ["音乐播放失败，请检查音频文件或系统音频设置"]
    assert playback == [(False, "")]
    assert canary not in _audio_log_dump(caplog)


def test_partial_player_start_failure_is_stopped_and_private(
        qt_application, tmp_path, caplog):
    canary = r"AUDIO-PARTIAL-CANARY C:\Private\secret-song.mp3"
    track = tmp_path / "safe.mp3"
    track.write_bytes(b"test")
    mgr = AudioManager()
    mgr.set_tracks([str(track)])

    class PartiallyStartedPlayer:
        def __init__(self):
            self.playing = False
            self.stop_calls = 0

        def setSource(self, _source):  # noqa: N802
            return None

        def play(self):
            self.playing = True
            raise RuntimeError(canary)

        def stop(self):
            self.stop_calls += 1
            self.playing = False

        def isPlaying(self) -> bool:  # noqa: N802
            return self.playing

    player = PartiallyStartedPlayer()
    mgr._player = player
    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        assert mgr.play() is False

    assert player.stop_calls == 1
    assert mgr.is_playing() is False
    assert canary not in _audio_log_dump(caplog)


def test_failed_stop_quarantines_partially_started_backend(
        qt_application, tmp_path, caplog):
    canary = r"AUDIO-STOP-CANARY C:\Private\secret-song.mp3"
    track = tmp_path / "safe.mp3"
    track.write_bytes(b"test")
    mgr = AudioManager()
    mgr.set_tracks([str(track)])

    class BrokenStopPlayer:
        playing = False

        def setSource(self, _source):  # noqa: N802
            return None

        def play(self):
            self.playing = True
            raise RuntimeError(canary)

        def stop(self):
            raise RuntimeError(canary)

        def isPlaying(self):  # noqa: N802
            return self.playing

        def setAudioOutput(self, _output):  # noqa: N802
            return None

        def deleteLater(self):  # noqa: N802
            return None

    mgr._player = BrokenStopPlayer()
    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        assert mgr.play() is False

    assert mgr._player is None
    assert mgr._failed is True
    assert mgr.is_playing() is False
    assert canary not in _audio_log_dump(caplog)


@pytest.mark.parametrize(
    "operation", ("pause", "stop", "is_playing", "toggle"))
def test_public_transport_backend_errors_never_escape_or_leak(
        qt_application, operation, caplog):
    canary = r"AUDIO-TRANSPORT-CANARY C:\Private\secret-song.mp3"
    mgr = AudioManager()

    class ExplodingTransport:
        def pause(self):
            raise RuntimeError(canary)

        def stop(self):
            raise RuntimeError(canary)

        def isPlaying(self):  # noqa: N802
            raise RuntimeError(canary)

        def setAudioOutput(self, _output):  # noqa: N802
            raise RuntimeError(canary)

        def deleteLater(self):  # noqa: N802
            raise RuntimeError(canary)

    mgr._player = ExplodingTransport()
    with caplog.at_level(logging.WARNING, logger="retirement_pet.audio"):
        getattr(mgr, operation)()

    assert mgr._player is None
    assert mgr._failed is True
    assert mgr.is_playing() is False
    assert canary not in _audio_log_dump(caplog)
