"""Settings dialog: edits user-facing settings, applies via callback."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(
            self, settings: dict,
            on_apply: Callable[[dict], bool | None], parent=None):
        super().__init__(parent)
        self._on_apply = on_apply
        self.setWindowTitle("退休宠物 · 设置")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.target_edit = QLineEdit(str(settings.get("target_datetime", "")))
        self.target_edit.setToolTip("格式：2060-07-07T21:32:00")
        form.addRow(QLabel("退休目标时间"), self.target_edit)

        self.meal_edit = QLineEdit(", ".join(settings.get("meal_times", [])))
        self.meal_edit.setToolTip("逗号分隔的 HH:MM，例如 08:00, 12:00, 18:30")
        form.addRow(QLabel("三餐时间"), self.meal_edit)

        self.exercise_edit = QLineEdit(", ".join(settings.get("exercise_times", [])))
        form.addRow(QLabel("健身时间"), self.exercise_edit)

        self.meeting_edit = QLineEdit(self._format_meetings(settings.get("meeting_windows", [])))
        self.meeting_edit.setToolTip("格式：09:30-10:30，多个用逗号分隔")
        form.addRow(QLabel("会议时段"), self.meeting_edit)

        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0.0, 1.0)
        self.volume_spin.setSingleStep(0.05)
        self.volume_spin.setValue(float(settings.get("volume", 0.35)))
        form.addRow(QLabel("音量"), self.volume_spin)

        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(30, 3600)
        self.idle_spin.setSuffix(" 秒")
        self.idle_spin.setValue(int(settings.get("work_idle_threshold_s", 120)))
        form.addRow(QLabel("多久没动静算休息"), self.idle_spin)

        self.reminder_spin = QSpinBox()
        self.reminder_spin.setRange(10, 240)
        self.reminder_spin.setSuffix(" 分钟")
        self.reminder_spin.setValue(int(settings.get("rest_reminder_minutes", 50)))
        form.addRow(QLabel("连续工作提醒间隔"), self.reminder_spin)

        self.random_check = QCheckBox("随机小动作（伸懒腰、舔爪等）")
        self.random_check.setChecked(bool(settings.get("random_actions_enabled", True)))
        form.addRow(self.random_check)

        self.sound_check = QCheckBox("音效")
        self.sound_check.setChecked(bool(settings.get("sound_enabled", True)))
        form.addRow(self.sound_check)

        self.wheel_check = QCheckBox("滚轮调节音量")
        self.wheel_check.setChecked(bool(settings.get("wheel_controls_volume", True)))
        form.addRow(self.wheel_check)

        self.top_check = QCheckBox("窗口始终置顶")
        self.top_check.setChecked(bool(settings.get("always_on_top", True)))
        form.addRow(self.top_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _format_meetings(windows) -> str:
        parts = []
        for window in windows or []:
            if isinstance(window, dict):
                parts.append(f"{window.get('start', '')}-{window.get('end', '')}")
        return ", ".join(parts)

    def _parse_meetings(self, text: str) -> list[dict]:
        windows = []
        for chunk in text.replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk or "-" not in chunk:
                continue
            start, _, end = chunk.partition("-")
            s, e = start.strip(), end.strip()
            if len(s) == 5 and len(e) == 5 and s[2] == ":" and e[2] == ":":
                windows.append({"start": s, "end": e})
        return windows

    def _collect(self) -> dict:
        def parse_times(text: str) -> list[str]:
            result = []
            for chunk in text.replace("，", ",").split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                hh, _, mm = chunk.partition(":")
                try:
                    result.append(f"{int(hh):02d}:{int(mm):02d}")
                except ValueError:
                    continue
            return result

        return {
            "target_datetime": self.target_edit.text().strip(),
            "meal_times": parse_times(self.meal_edit.text()),
            "exercise_times": parse_times(self.exercise_edit.text()),
            "meeting_windows": self._parse_meetings(self.meeting_edit.text()),
            "volume": self.volume_spin.value(),
            "work_idle_threshold_s": self.idle_spin.value(),
            "rest_reminder_minutes": self.reminder_spin.value(),
            "random_actions_enabled": self.random_check.isChecked(),
            "sound_enabled": self.sound_check.isChecked(),
            "wheel_controls_volume": self.wheel_check.isChecked(),
            "always_on_top": self.top_check.isChecked(),
        }

    def _on_ok(self) -> None:
        values = self._collect()
        # Validate target before accepting.
        from retirement_pet.countdown import parse_target

        try:
            parse_target(values["target_datetime"])
        except ValueError:
            self.target_edit.setStyleSheet("border: 1px solid red;")
            return
        if self._on_apply(values) is not False:
            self.accept()
