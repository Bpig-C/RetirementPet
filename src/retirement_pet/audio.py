"""Music playback via QMediaPlayer + QAudioOutput (design 8.9).

The player is created lazily on first use so a silent pet session never
initializes audio backends.  Every failure degrades to a non-blocking
notification signal - the main loop must never die because of audio.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal

logger = logging.getLogger(__name__)

_BACKEND_UNAVAILABLE_TEXT = "这台电脑上的音频组件不可用"
_PLAYBACK_FAILED_TEXT = "音乐播放失败，请检查音频文件或系统音频设置"


class AudioManager(QObject):
    #: Non-blocking human-readable problem report (shows as a bubble).
    error_message = Signal(str)
    #: (is_playing, track_name)
    playback_changed = Signal(bool, str)
    playlist_changed = Signal(list)

    def __init__(self, parent: QObject | None = None, volume: float = 0.35,
                 sound_enabled: bool = True):
        super().__init__(parent)
        self._volume = max(0.0, min(1.0, volume))
        self._sound_enabled = bool(sound_enabled)
        self._tracks: list[str] = []
        self._index = -1
        self._player = None
        self._audio_output = None
        self._failed = False  # backend permanently unusable this session

    # -- lazy backend -------------------------------------------------------

    def _ensure_player(self) -> bool:
        if self._failed:
            return False
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(self._volume)
            self._player.setAudioOutput(self._audio_output)
            self._player.errorOccurred.connect(self._on_error)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            return True
        except Exception:  # noqa: BLE001 - missing backend etc.
            # Backend exceptions may embed an absolute source/device path.
            # Keep every LogRecord field fixed: no args and no exc_info.
            logger.warning("audio backend unavailable")
            self._quarantine_backend()
            return False

    def _on_error(self, _err, message: str) -> None:
        # ``message`` is supplied by the platform backend and commonly embeds
        # the full local media path.  It is deliberately neither logged nor
        # shown; the stable UI message is sufficient for this small app.
        del message
        logger.warning("audio playback failed")
        self._settle_failed_playback()
        self.error_message.emit(_PLAYBACK_FAILED_TEXT)
        self.playback_changed.emit(False, "")

    def _settle_failed_playback(self) -> None:
        """Best-effort convergence to stopped state without private data."""
        if self._player is None:
            return
        try:
            self._player.stop()
        except Exception:  # noqa: BLE001 - do not surface backend payloads
            self._quarantine_backend()

    def _quarantine_backend(self) -> None:
        """Detach a backend whose state can no longer be trusted."""
        player = self._player
        audio_output = self._audio_output
        # Clear references before touching Qt again so a re-entrant error
        # signal sees the stable unavailable state.
        self._player = None
        self._audio_output = None
        self._failed = True
        if player is not None:
            try:
                player.stop()
            except Exception:  # noqa: BLE001 - private backend payload
                pass
            try:
                player.setAudioOutput(None)
            except Exception:  # noqa: BLE001
                pass
            try:
                player.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        if audio_output is not None:
            try:
                audio_output.deleteLater()
            except Exception:  # noqa: BLE001
                pass

    def _on_media_status(self, status) -> None:
        if self._player is None:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if status == QMediaPlayer.EndOfMedia:
                self.next()
        except Exception:  # noqa: BLE001
            pass

    # -- playlist -------------------------------------------------------------

    def set_tracks(self, paths: list[str]) -> int:
        """Store only tracks that still exist on disk (design 8.9)."""
        valid = []
        for path in paths:
            if Path(path).is_file():
                valid.append(str(Path(path)))
            else:
                logger.debug("dropping missing music file")
        self._tracks = valid
        self._index = -1  # nothing selected until play()
        self.playlist_changed.emit(list(valid))
        return len(valid)

    def add_track(self, path: str) -> bool:
        if Path(path).is_file():
            self._tracks.append(str(Path(path)))
            self.playlist_changed.emit(list(self._tracks))
            return True
        return False

    @property
    def tracks(self) -> list[str]:
        return list(self._tracks)

    def current_track_name(self) -> str:
        if 0 <= self._index < len(self._tracks):
            return Path(self._tracks[self._index]).stem
        return ""

    # -- transport ---------------------------------------------------------------

    @property
    def sound_enabled(self) -> bool:
        return self._sound_enabled

    def set_sound_enabled(self, enabled: bool) -> None:
        """Master sound switch (settings key ``sound_enabled``).

        Turning it off mid-track stops playback; while off, play()/toggle()
        are silent no-ops instead of errors so menus stay quiet.
        """
        self._sound_enabled = bool(enabled)
        if not enabled and self.is_playing():
            self.stop()

    def play(self) -> bool:
        if not self._sound_enabled:
            return False
        if not self._tracks:
            self.error_message.emit("播放列表是空的，先在设置里添加音乐吧")
            return False
        if not self._ensure_player():
            self.error_message.emit(_BACKEND_UNAVAILABLE_TEXT)
            return False
        if self._index < 0 or self._index >= len(self._tracks):
            self._index = 0
        try:
            self._player.setSource(
                QUrl.fromLocalFile(self._tracks[self._index]))
            self._player.play()
        except Exception:  # noqa: BLE001 - synchronous platform boundary
            logger.warning("audio playback start failed")
            self._settle_failed_playback()
            self.error_message.emit(_PLAYBACK_FAILED_TEXT)
            self.playback_changed.emit(False, "")
            return False
        self.playback_changed.emit(True, self.current_track_name())
        return True

    def pause(self) -> None:
        if self._player is not None:
            try:
                self._player.pause()
            except Exception:  # noqa: BLE001 - platform transport boundary
                logger.warning("audio pause failed")
                self._quarantine_backend()
                self.playback_changed.emit(False, "")
                return
            self.playback_changed.emit(False, self.current_track_name())

    def stop(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001 - platform transport boundary
                logger.warning("audio stop failed")
                self._quarantine_backend()
                self.playback_changed.emit(False, "")
                return
        self.playback_changed.emit(False, self.current_track_name())

    def toggle(self) -> bool:
        if self.is_playing():
            self.pause()
            return False
        return self.play()

    def is_playing(self) -> bool:
        if self._player is None:
            return False
        try:
            return bool(self._player.isPlaying())
        except Exception:  # noqa: BLE001 - platform query boundary
            logger.warning("audio state query failed")
            self._quarantine_backend()
            return False

    def next(self) -> bool:
        if not self._tracks:
            return False
        self._index = (self._index + 1) % len(self._tracks)
        return self.play()

    def previous(self) -> bool:
        if not self._tracks:
            return False
        self._index = (self._index - 1) % len(self._tracks)
        return self.play()

    def seek_next_if_playing(self) -> None:
        if self.is_playing():
            self.next()

    # -- volume ----------------------------------------------------------------

    def set_volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, float(value)))
        if self._audio_output is not None:
            try:
                self._audio_output.setVolume(self._volume)
            except Exception:  # noqa: BLE001 - platform output boundary
                logger.warning("audio volume update failed")
                self._quarantine_backend()

    @property
    def volume(self) -> float:
        return self._volume

    def adjust_volume(self, delta: float) -> float:
        self.set_volume(self._volume + delta)
        return self._volume
