"""Control panel pages: lazy, event-driven, one visible at a time.

Pages NEVER write SQL or mutate the pet window directly - they call app
methods (the composition root owns the wiring).
"""

from __future__ import annotations

import hashlib
import logging
import math
import multiprocessing
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from retirement_pet.layout_policy import (
    ALLOWED,
    LayoutId,
    POLICIES,
)
from retirement_pet.text_profile import SAFE_VARIABLES

logger = logging.getLogger(__name__)


LOCAL_IMPORT_TIMEOUT_SECONDS = 60.0


class _LocalImportWorker(QObject):
    """Own one timeout-bounded spawn process without blocking Qt shutdown."""

    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
            self, pack_path: Path, parent=None, *,
            _target=None,
            _timeout_seconds: float = LOCAL_IMPORT_TIMEOUT_SECONDS,
            _context=None):
        super().__init__(parent)
        self._pack_path = Path(pack_path)
        timeout = float(_timeout_seconds)
        self._timeout_seconds = (
            min(LOCAL_IMPORT_TIMEOUT_SECONDS, max(0.01, timeout))
            if math.isfinite(timeout) else LOCAL_IMPORT_TIMEOUT_SECONDS
        )
        self._started_at = 0.0
        self._started = False
        self._finished = False
        self._transfer_envelope = None
        self._pending_result = None
        self._transfer_buffer = bytearray()
        self._transfer_hasher = hashlib.sha256()
        context = _context or multiprocessing.get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        if _target is None:
            from retirement_pet.petpack.local_import import (
                isolated_import_process_entry,
            )
            _target = isolated_import_process_entry
        self._receive_connection = receive_connection
        self._send_connection = send_connection
        self._process = context.Process(
            target=_target,
            args=(str(self._pack_path), send_connection),
            name="RetirementPet-local-import",
            daemon=True,
        )
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll)

    def start(self) -> None:
        if self._started or self._finished:
            return
        try:
            self._process.start()
            self._started = True
            self._started_at = time.monotonic()
        except Exception:  # noqa: BLE001 - process-launch boundary
            logger.exception("isolated local character-pack inspector failed to start")
            self._finish(error="无法启动安全检查进程")
            return
        finally:
            # The child owns its duplicate; the GUI must not keep the writer
            # alive or EOF/crash detection becomes ambiguous.
            try:
                self._send_connection.close()
            except OSError:
                pass
        self._poll_timer.start()

    def _poll(self) -> None:
        if self._finished:
            return
        if time.monotonic() - self._started_at >= self._timeout_seconds:
            self._finish(error="角色包检查超时，已安全终止")
            return
        try:
            self._drain_available_messages()
            if self._finished:
                return
        except Exception:  # noqa: BLE001 - child protocol boundary
            logger.exception(
                "isolated local character-pack protocol failed "
                "(transferred=%d expected=%s pending=%s)",
                len(self._transfer_buffer),
                getattr(self._transfer_envelope, "payload_size", None),
                self._pending_result is not None,
            )
            self._finish(error="角色包检查进程异常退出")
            return
        if self._pending_result is not None:
            if self._process.is_alive():
                return
            try:
                has_extra_frame = self._receive_connection.poll()
                if has_extra_frame:
                    try:
                        self._receive_connection.recv_bytes(maxlength=1)
                    except (EOFError, BrokenPipeError):
                        has_extra_frame = False
                    except OSError:
                        # A real frame larger than one byte violated the
                        # protocol.  Windows EOF is BrokenPipeError above.
                        has_extra_frame = True
            except (BrokenPipeError, OSError, ValueError):
                has_extra_frame = False
            if self._process.exitcode == 0 and not has_extra_frame:
                self._finish(result=self._pending_result)
            else:
                self._finish(error="角色包检查进程异常退出")
            return
        if self._started and not self._process.is_alive():
            try:
                queued = self._receive_connection.poll()
            except (OSError, ValueError):
                queued = False
            if queued:
                return
            # A normally sent message is observable before EOF.  No message
            # plus a dead child means crash/forced exit and must fail closed.
            self._finish(error="角色包检查进程异常退出")

    def _drain_available_messages(self) -> None:
        if self._pending_result is not None:
            return
        from retirement_pet.petpack.local_import import (
            ISOLATED_IMPORT_CHUNK_BYTES,
            MAX_ISOLATED_IMPORT_METADATA_BYTES,
            parse_isolated_import_metadata,
            preview_from_isolated_transfer,
        )

        if self._transfer_envelope is None:
            if not self._receive_connection.poll():
                return
            frame = self._receive_connection.recv_bytes(
                maxlength=MAX_ISOLATED_IMPORT_METADATA_BYTES)
            parsed = parse_isolated_import_metadata(frame)
            if isinstance(parsed, str):
                self._finish(error=parsed)
                return
            self._transfer_envelope = parsed

        # Bound each event-loop turn to 4 MiB so a maximum-size accepted pack
        # cannot monopolize the GUI while it crosses the process boundary.
        for _ in range(4):
            if not self._receive_connection.poll():
                return
            chunk = self._receive_connection.recv_bytes(
                maxlength=ISOLATED_IMPORT_CHUNK_BYTES)
            remaining = (
                self._transfer_envelope.payload_size
                - len(self._transfer_buffer)
            )
            if not chunk or len(chunk) > remaining:
                raise ValueError("invalid isolated-import chunk")
            self._transfer_buffer.extend(chunk)
            self._transfer_hasher.update(chunk)
            if len(self._transfer_buffer) == \
                    self._transfer_envelope.payload_size:
                if self._transfer_hasher.hexdigest() != \
                        self._transfer_envelope.payload_sha256:
                    raise ValueError("isolated-import transfer digest mismatch")
                self._pending_result = preview_from_isolated_transfer(
                    self._transfer_envelope, bytes(self._transfer_buffer))
                self._transfer_buffer.clear()
                return

    def _stop_process(self) -> None:
        if not self._started:
            return
        process = self._process
        try:
            process.join(timeout=0.1)
        except (AssertionError, OSError, ValueError):
            pass
        if process.is_alive():
            try:
                process.terminate()
            except (AttributeError, OSError):
                pass
            try:
                process.join(timeout=0.3)
            except (AssertionError, OSError, ValueError):
                pass
        if process.is_alive():
            try:
                process.kill()
            except (AttributeError, OSError):
                pass
            try:
                process.join(timeout=0.3)
            except (AssertionError, OSError, ValueError):
                pass
        if not process.is_alive():
            try:
                process.close()
            except (AttributeError, OSError, ValueError):
                pass

    def _finish(self, *, result=None, error: str | None = None) -> None:
        if self._finished:
            return
        # Mark first because succeeded may open a nested confirmation modal;
        # dispose/quit can re-enter cancel() while that modal is running.
        self._finished = True
        self._poll_timer.stop()
        self._stop_process()
        self._transfer_buffer.clear()
        try:
            self._receive_connection.close()
        except OSError:
            pass
        if result is not None:
            self.succeeded.emit(result)
        elif error is not None:
            self.failed.emit(error)
        self.finished.emit()

    def cancel(self) -> None:
        """Return after a hard upper bound even when the child never returns."""
        self._finish()

    def isFinished(self) -> bool:
        return self._finished

    def has_live_process(self) -> bool:
        if not self._started:
            return False
        try:
            return self._process.is_alive()
        except ValueError:  # multiprocessing.Process.close() already ran
            return False


def _confirm_local_pack(parent, preview) -> bool:
    """Display non-spoofable LOCAL_IMPORTED trust and rights disclosure."""
    warning_text = "、".join(preview.warning_codes) or "无协议警告"
    rights_text = "、".join(preview.rights_bases) or "未声明"
    capability_text = "、".join(preview.action_semantics) or "仅待机回退"
    details = (
        "本地内容\n"
        f"角色包：{preview.package_name}\n"
        f"声明发布者：{preview.publisher_id or '未声明'}（身份未验证）\n"
        f"权利依据：{rights_text}（由包作者或用户自行声明，未验证）\n"
        f"角色数：{len(preview.character_ids)}；"
        f"文件大小：{preview.archive_size / (1024 * 1024):.1f} MiB\n"
        f"动作语义：{capability_text}\n"
        f"需要确认的警告：{warning_text}\n\n"
        "RetirementPet 未验证发布者身份或权利状态；导入不会上传、分享或自动激活角色。"
    )
    box = QMessageBox(parent)
    box.setWindowTitle("确认导入本地角色包")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText("确认安装这个本地角色包？")
    box.setInformativeText(details)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    yes_button = box.button(QMessageBox.StandardButton.Yes)
    if yes_button is not None:
        yes_button.setText("确认导入")
    return box.exec() == QMessageBox.StandardButton.Yes


def _page() -> QWidget:
    page = QWidget()
    page.setLayout(QVBoxLayout())
    return page


def _add(page: QWidget, *widgets) -> None:
    for widget in widgets:
        page.layout().addWidget(widget)


# -- overview ----------------------------------------------------------------------


def build_overview_page(app) -> QWidget:
    page = _page()
    runtime = app.switcher.current_runtime
    active = app._selection_store.get("active")

    info = QGroupBox("当前状态")
    form = QFormLayout(info)
    if active is not None:
        form.addRow("活动角色", QLabel(active.character_fqid))
        record = app.library.get_revision(active.revision_key())
        source = ("官方内置" if record and record.builtin else "本地导入")
        form.addRow("来源", QLabel(source))
    else:
        form.addRow("活动角色", QLabel("引擎安全猫（Bootstrap）"))
    contexts = ", ".join(sorted(c.value for c in app.contexts.active())) or "无"
    form.addRow("当前 Context", QLabel(contexts))
    if app.countdown_module.snapshot is not None:
        snap = app.countdown_module.snapshot
        form.addRow("距退休", QLabel(f"{snap.days} 天（{snap.stage_text}）"))
    caps = app.capabilities
    supported = ", ".join(sorted(caps.semantics)) or "仅 idle"
    form.addRow("角色能力", QLabel(supported))
    _add(page, info)

    if runtime is not None and runtime.missing_semantics:
        missing = QGroupBox("缺少的动作语义（已回退到本角色待机）")
        missing.setLayout(QVBoxLayout())
        missing.layout().addWidget(QLabel(
            "，".join(runtime.missing_semantics)))
        _add(page, missing)
    page.layout().addStretch(1)
    return page


# -- characters ----------------------------------------------------------------------


def build_characters_page(app) -> QWidget:
    page = _page()
    page.layout().addWidget(QLabel(
        "按 系列 → 角色 浏览并切换；同一时间只有一个活动角色。"
        "本地导入只接受通过安全预检的 .petpack。"))

    onboarding_group = None
    onboarding_official_btn = None
    onboarding_preview_btn = None
    onboarding_status = None
    if app._character_onboarding_pending():
        onboarding_group = QGroupBox("首次角色选择")
        onboarding_group.setObjectName("character_onboarding")
        onboarding_layout = QVBoxLayout(onboarding_group)
        onboarding_intro = QLabel(
            "你现在看到的是官方简笔退休猫。可以继续使用它，或启用随程序提供的"
            "半写实退休猫预览。这里只更换角色；退休倒计时、待办与全局设置都会保留。")
        onboarding_intro.setWordWrap(True)
        onboarding_intro.setTextFormat(Qt.TextFormat.PlainText)
        onboarding_layout.addWidget(onboarding_intro)
        onboarding_disclosure = QLabel(
            "半写实猫按“本地内容”处理：发布者身份与权利声明未验证。点击启用按钮"
            "表示确认导入经过隔离预检且身份摘要固定的预览包，并立即切换到该角色。")
        onboarding_disclosure.setWordWrap(True)
        onboarding_disclosure.setTextFormat(Qt.TextFormat.PlainText)
        onboarding_layout.addWidget(onboarding_disclosure)
        onboarding_buttons = QHBoxLayout()
        onboarding_official_btn = QPushButton("继续使用官方退休猫")
        onboarding_official_btn.setObjectName("character_onboarding_keep_official")
        onboarding_preview_btn = QPushButton(
            "确认本地内容并启用半写实猫")
        onboarding_preview_btn.setObjectName(
            "character_onboarding_enable_preview")
        onboarding_buttons.addWidget(onboarding_official_btn)
        onboarding_buttons.addWidget(onboarding_preview_btn)
        onboarding_layout.addLayout(onboarding_buttons)
        onboarding_status = QLabel("请选择一种角色；关闭面板会留到下次手动启动再询问。")
        onboarding_status.setObjectName("character_onboarding_status")
        onboarding_status.setWordWrap(True)
        onboarding_status.setTextFormat(Qt.TextFormat.PlainText)
        onboarding_layout.addWidget(onboarding_status)
        page.layout().addWidget(onboarding_group)

    list_widget = QListWidget()
    list_widget.setObjectName("character_list")
    entries = []
    _add(page, list_widget)

    switch_btn = QPushButton("切换到所选角色")
    switch_btn.setObjectName("character_switch")
    import_btn = QPushButton("导入本地角色包…")
    import_btn.setObjectName("character_import")
    status = QLabel("")
    status.setObjectName("character_status")
    status.setWordWrap(True)
    status.setTextFormat(Qt.TextFormat.PlainText)
    import_state = {"worker": None, "mode": None, "disposed": False}

    def import_lifecycle_active() -> bool:
        qt_app = QApplication.instance()
        return (
            not import_state["disposed"]
            and not bool(getattr(app, "_shutdown_complete", False))
            and qt_app is not None
            and not QApplication.closingDown()
        )

    def is_active(entry, active) -> bool:
        return active is not None \
            and entry.revision_key == active.revision_key() \
            and entry.character_id == active.character_fqid.rsplit(".", 1)[-1]

    def update_button(_row=None) -> None:
        row = list_widget.currentRow()
        active = app._selection_store.get("active")
        switch_btn.setEnabled(
            0 <= row < len(entries) and not is_active(entries[row], active))

    def refresh() -> None:
        if onboarding_group is not None \
                and not app._character_onboarding_pending():
            onboarding_group.hide()
        active = app._selection_store.get("active")
        entries.clear()
        list_widget.clear()
        current_row = -1
        for (publisher, series), group in sorted(app.catalog.grouped().items()):
            for entry in group:
                entries.append(entry)
                label = f"{entry.display_name}（{series} · {publisher}）"
                if is_active(entry, active):
                    label += "  ← 当前"
                    current_row = len(entries) - 1
                if entry.builtin:
                    label += "  [官方]"
                else:
                    label += "  [本地内容 · 发布者/权利未验证]"
                QListWidgetItem(label, list_widget)
        if current_row >= 0:
            list_widget.setCurrentRow(current_row)
        preserve_result = status.text().startswith((
            "切换成功", "切换失败", "导入成功", "导入失败", "角色包已存在",
            "已启用", "已继续", "半写实预览包", "预览包已导入",
        ))
        if not preserve_result:
            if not entries:
                status.setText("没有可用角色；桌宠继续使用安全回退角色。")
            elif len(entries) == 1:
                status.setText(
                    "当前只有一个角色；可以导入本地 .petpack，导入后仍需手动切换。")
            else:
                status.setText("请选择一个非当前角色。")
        update_button()

    def on_switch():
        row = list_widget.currentRow()
        if 0 <= row < len(entries):
            selected = entries[row]
            if app._switch_character(selected):
                status.setText(f"切换成功：{selected.display_name}")
            else:
                status.setText("切换失败，已保留原角色。")
            refresh()

    def import_failed(detail: str) -> None:
        if not import_lifecycle_active():
            return
        status.setText(f"导入失败：{detail}")
        if import_state["mode"] == "onboarding" \
                and onboarding_status is not None:
            onboarding_status.setText(
                f"半写实猫检查失败：{detail}。当前角色未改变，可以重试。")

    def import_inspected(preview) -> None:
        if not import_lifecycle_active():
            return
        if import_state["mode"] == "onboarding":
            if onboarding_status is not None:
                onboarding_status.setText("安全检查通过，正在导入并切换角色…")
            ok, message = app._activate_bundled_character_preview(preview)
            if not import_lifecycle_active():
                return
            status.setText(message)
            if onboarding_status is not None:
                onboarding_status.setText(message)
            if ok:
                refresh()
            return
        confirmed = _confirm_local_pack(page, preview)
        # QMessageBox.exec() owns a nested event loop.  Quit or panel disposal
        # may therefore have completed before it returns to this callback.
        if not import_lifecycle_active():
            return
        if not confirmed:
            status.setText("已取消导入；角色库和当前角色均未改变。")
            return
        status.setText("正在核对隔离预检绑定并写入不可变角色库…")
        ok, message = app._import_preflighted_character_pack(
            preview,
            warnings_acknowledged=preview.warning_codes,
        )
        if not import_lifecycle_active():
            return
        status.setText(message)
        if ok:
            refresh()

    def import_finished() -> None:
        worker = import_state["worker"]
        import_state["worker"] = None
        import_state["mode"] = None
        if import_lifecycle_active():
            import_btn.setEnabled(True)
            if onboarding_official_btn is not None:
                onboarding_official_btn.setEnabled(True)
            if onboarding_preview_btn is not None:
                onboarding_preview_btn.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def start_import(pack_path: Path, mode: str) -> None:
        if not import_lifecycle_active() or import_state["worker"] is not None:
            return
        import_state["mode"] = mode
        import_btn.setEnabled(False)
        if onboarding_official_btn is not None:
            onboarding_official_btn.setEnabled(False)
        if onboarding_preview_btn is not None:
            onboarding_preview_btn.setEnabled(False)
        worker = _LocalImportWorker(Path(pack_path), QApplication.instance())
        import_state["worker"] = worker
        worker.succeeded.connect(import_inspected)
        worker.failed.connect(import_failed)
        worker.finished.connect(import_finished)
        worker.start()

    def on_import():
        from retirement_pet.resource_path import asset_path

        if not import_lifecycle_active() or import_state["worker"] is not None:
            return
        initial_dir = asset_path("petpack", "examples")
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            page,
            "导入本地角色包",
            str(initial_dir) if initial_dir.is_dir() else "",
            "PetPack 角色包 (*.petpack)",
        )
        # QFileDialog.exec() is also a nested event loop.  Never construct a
        # process (or mutate UI state) after disposal/shutdown won that race.
        if not import_lifecycle_active() or not file_name:
            return
        if import_state["worker"] is not None:
            return
        status.setText("正在后台复制并检查角色包…")
        # The worker must outlive a nested confirmation modal closing/deleting
        # this page before _finish() can emit its trailing finished signal.
        start_import(Path(file_name), "manual")

    def on_keep_official() -> None:
        if not import_lifecycle_active() or import_state["worker"] is not None:
            return
        persisted = app._complete_character_onboarding()
        message = "已继续使用官方退休猫；以后仍可在角色页更换。"
        if not persisted:
            message = "已继续使用官方退休猫；选择未能保存，下次手动启动会再次询问。"
        status.setText(message)
        if onboarding_status is not None:
            onboarding_status.setText(message)
        refresh()

    def on_enable_preview() -> None:
        if not import_lifecycle_active() or import_state["worker"] is not None:
            return
        pack_path = app._bundled_character_preview_path()
        if not pack_path.is_file():
            message = "未找到随程序提供的半写实预览包；当前角色未改变。"
            status.setText(message)
            if onboarding_status is not None:
                onboarding_status.setText(message)
            return
        status.setText("正在后台复制并检查固定身份的半写实预览包…")
        if onboarding_status is not None:
            onboarding_status.setText("正在安全检查半写实预览包…")
        start_import(pack_path, "onboarding")

    def dispose() -> None:
        if import_state["disposed"]:
            return
        import_state["disposed"] = True
        worker = import_state["worker"]
        if worker is not None:
            try:
                worker.succeeded.disconnect(import_inspected)
                worker.failed.disconnect(import_failed)
            except (RuntimeError, TypeError):
                pass
            # Hard-bounded process termination; no unbounded QThread.wait()
            # and no unsafe QThread.terminate() during QApplication teardown.
            worker.cancel()

    switch_btn.clicked.connect(on_switch)
    import_btn.clicked.connect(on_import)
    if onboarding_official_btn is not None:
        onboarding_official_btn.clicked.connect(on_keep_official)
    if onboarding_preview_btn is not None:
        onboarding_preview_btn.clicked.connect(on_enable_preview)
    list_widget.currentRowChanged.connect(update_button)
    hint = QLabel("切换保留退休目标、任务数据与全局设置。")
    hint.setWordWrap(True)
    _add(page, import_btn, switch_btn, status, hint)
    page.activate = refresh
    page.dispose = dispose
    page._import_worker_state = import_state
    refresh()
    return page


# -- actions & decisions ----------------------------------------------------------------


def build_actions_page(app) -> QWidget:
    page = _page()
    info = QGroupBox("动作与决策")
    form = QFormLayout(info)
    perf = app.bridge.last_performance
    if perf is not None:
        form.addRow("当前表演", QLabel(f"{perf.semantic}（{perf.source}）"))
    else:
        form.addRow("当前表演", QLabel("待机"))
    discipline = QGroupBox("纪律矩阵（引擎冻结，角色包不可修改）")
    discipline.setLayout(QFormLayout())
    discipline.layout().addRow(QLabel(
        "会议：随机动作禁止；仅静音且 meeting_safe 的请求\n"
        "勿扰：仅 dnd_safe 动作；装饰隐藏\n"
        "工作：允许低频随机；不清除工作事实"))
    random_check = QCheckBox("启用")
    random_check.setObjectName("random_actions_enabled")
    random_check.setChecked(bool(
        app.settings.get("random_actions_enabled", True)))
    random_status = QLabel("")
    random_status.setObjectName("random_actions_status")

    def set_random_enabled(enabled: bool) -> None:
        if app._apply_settings({"random_actions_enabled": enabled}):
            random_status.setText("已保存")
            return
        random_check.blockSignals(True)
        random_check.setChecked(not enabled)
        random_check.blockSignals(False)
        random_status.setText("保存失败，设置未改变")

    random_check.toggled.connect(set_random_enabled)
    form.addRow("随机小动作", random_check)
    form.addRow("", random_status)
    page.layout().addWidget(info)
    page.layout().addWidget(discipline)
    page.layout().addStretch(1)
    return page


# -- text ---------------------------------------------------------------------------------


def build_text_page(app) -> QWidget:
    page = _page()
    page.layout().addWidget(QLabel(
        "文案使用语义键与安全变量；模板为纯文本替换，未知变量保持字面，"
        "不允许表达式或标记。当前 Alpha 仅供预览，不会保存或改变桌宠文案。"))

    preview_box = QGroupBox("模板预览（安全变量白名单）")
    form = QFormLayout(preview_box)
    template_edit = QLineEdit("还有 {days} 天，{character_name} 陪着你")
    result = QLabel("")

    def refresh(_text=None):
        from retirement_pet.text_profile import render
        try:
            result.setText(render(template_edit.text(), {
                "days": "12345", "character_name": "退休猫",
                "hours": "9", "minutes": "30",
                "series_name": "退休猫", "stage_name": "青年",
            }))
        except Exception as exc:  # noqa: BLE001 - surface template errors
            result.setText(f"模板错误：{exc}")

    template_edit.textChanged.connect(refresh)
    form.addRow("模板", template_edit)
    form.addRow("预览", result)
    form.addRow("可用变量", QLabel("，".join(sorted(SAFE_VARIABLES))))
    refresh()
    _add(page, preview_box)
    page.layout().addStretch(1)
    return page


# -- display -----------------------------------------------------------------------------


def build_display_page(app) -> QWidget:
    page = _page()
    box = QGroupBox("显示（预定义布局 × 可见性预设）")
    form = QFormLayout(box)

    layout_combo = QComboBox()
    layout_combo.setObjectName("layout_preview")
    for layout_id in LayoutId:
        spec = ALLOWED[layout_id]
        layout_combo.addItem(f"{spec.layout_id.value}（倒计时:{spec.countdown}）",
                             spec.layout_id.value)
    policy_combo = QComboBox()
    policy_combo.setObjectName("visibility_preview")
    for policy in POLICIES:
        policy_combo.addItem(policy.value, policy.value)

    layout_combo.setEnabled(False)
    policy_combo.setEnabled(False)
    layout_combo.setToolTip("当前 Alpha 尚未把布局预设应用到桌宠")
    policy_combo.setToolTip("当前 Alpha 尚未把可见性预设应用到桌宠")

    form.addRow("布局", layout_combo)
    form.addRow("可见性", policy_combo)
    preview_note = QLabel("预定义布局尚在预览阶段；当前版本不会保存或应用这两项。")
    preview_note.setObjectName("layout_preview_note")
    preview_note.setWordWrap(True)
    form.addRow("", preview_note)

    top_check = QCheckBox("宠物窗口始终置顶")
    top_check.setChecked(bool(app.settings.get("always_on_top", True)))
    top_check.toggled.connect(
        lambda on: app._set_always_on_top(on))
    form.addRow("", top_check)

    click_through = QCheckBox("鼠标穿透")
    click_through.setChecked(bool(app.settings.get("click_through", False)))
    click_through.toggled.connect(
        lambda on: app._set_click_through(on))
    form.addRow("", click_through)
    _add(page, box)
    page.layout().addStretch(1)
    return page


# -- sound & schedule (v1 settings semantics) -----------------------------------------------


def build_sound_schedule_page(app) -> QWidget:
    page = _page()
    box = QGroupBox("退休目标、声音与生活节奏")
    form = QFormLayout(box)

    target_edit = QLineEdit(str(app.settings.get("target_datetime", "")))
    target_edit.setObjectName("schedule_target")
    volume_spin = QDoubleSpinBox()
    volume_spin.setRange(0.0, 1.0)
    volume_spin.setSingleStep(0.05)
    volume_spin.setValue(float(app.settings.get("volume", 0.35)))
    sound_check = QCheckBox("启用声音")
    sound_check.setChecked(bool(app.settings.get("sound_enabled", True)))
    meal_edit = QLineEdit(", ".join(app.settings.get("meal_times", [])))
    meal_edit.setObjectName("schedule_meals")
    exercise_edit = QLineEdit(", ".join(app.settings.get("exercise_times", [])))
    exercise_edit.setObjectName("schedule_exercise")
    apply_btn = QPushButton("应用")
    apply_btn.setObjectName("schedule_apply")
    status = QLabel("")
    status.setObjectName("schedule_status")

    def on_apply():
        def parse_times(text: str, label: str) -> list[str]:
            result = []
            for raw in text.replace("，", ",").split(","):
                item = raw.strip()
                if not item:
                    continue
                parts = item.split(":")
                if len(parts) != 2:
                    raise ValueError(label)
                try:
                    hour, minute = int(parts[0]), int(parts[1])
                except ValueError as exc:
                    raise ValueError(label) from exc
                if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                    raise ValueError(label)
                result.append(f"{hour:02d}:{minute:02d}")
            return result

        try:
            meal_times = parse_times(meal_edit.text(), "三餐")
            exercise_times = parse_times(exercise_edit.text(), "健身")
        except ValueError as exc:
            status.setText(f"{exc.args[0]}时间格式无效，请使用 HH:MM")
            return
        values = {
            "target_datetime": target_edit.text().strip(),
            "volume": volume_spin.value(),
            "sound_enabled": sound_check.isChecked(),
            "meal_times": meal_times,
            "exercise_times": exercise_times,
        }
        from retirement_pet.countdown import parse_target
        try:
            parse_target(values["target_datetime"])
        except ValueError:
            status.setText("退休目标时间格式无效，示例 2060-07-07T21:32:00")
            return
        if app._apply_settings(values):
            status.setText("已应用并保存")
        else:
            status.setText("保存失败，设置未改变")

    apply_btn.clicked.connect(on_apply)
    form.addRow("退休目标时间", target_edit)
    form.addRow("音量", volume_spin)
    form.addRow("", sound_check)
    form.addRow("三餐时间（HH:MM, …）", meal_edit)
    form.addRow("健身时间", exercise_edit)
    form.addRow("", apply_btn)
    form.addRow("", status)
    _add(page, box)
    page.layout().addStretch(1)
    return page


# -- about & diagnostics ------------------------------------------------------------------


def build_about_page(app) -> QWidget:
    page = _page()
    box = QGroupBox("关于与诊断")
    form = QFormLayout(box)
    from retirement_pet import __version__

    form.addRow("版本", QLabel(__version__))
    form.addRow("数据目录", QLabel(str(app._data_dir)))
    form.addRow("角色库", QLabel(str(app.library.root)))
    revisions = app.library.list_revisions()
    form.addRow("已安装 Revision", QLabel(str(len(revisions))))
    events = app.library.journal_dir / "events.jsonl"
    if events.is_file():
        count = sum(1 for _ in events.open(encoding="utf-8"))
        form.addRow("生命周期事件", QLabel(str(count)))
    perf_path = app._data_dir / "logs" / "perf_markers.jsonl"
    if perf_path.is_file():
        count = sum(1 for _ in perf_path.open(encoding="utf-8"))
        form.addRow("性能 marker", QLabel(str(count)))
    _add(page, box)

    logs_btn = QPushButton("打开日志目录")
    logs_btn.clicked.connect(
        lambda: __import__("subprocess").Popen(
            ["explorer", str(app._data_dir / "logs")]))
    _add(page, logs_btn)
    page.layout().addStretch(1)
    return page
