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
from PySide6.QtGui import QStandardItem, QStandardItemModel
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
    QScrollArea,
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


def _wordwrap_height_policy(label: QLabel) -> None:
    """QLabel drops the heightForWidth flag by default, so layouts under
    a scroll shell under-reserve wrapped text and clip its last lines
    (V12-07 L07-02)."""
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)


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
    # V13-06: explainable activity + focus state (aggregate facts only,
    # no key content, no window titles, no task titles)
    activity = app.activity_status()
    state_text = {"active": "近期有输入", "idle": "空闲",
                  "unknown": "未知"}[activity["state"]]
    link_text = "开" if activity["link_enabled"] else "关"
    form.addRow("活动联动", QLabel(f"{state_text}（联动已{link_text}）"))
    if activity["focused"]:
        working = "正在处理专注任务" if activity["working_on_focused_task"]             else "有专注任务（暂无输入）"
        form.addRow("专注", QLabel(working))
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
    # L07-02: the panel is usable down to 720x480 - the whole content
    # scrolls instead of clipping details or crushing the onboarding
    # buttons to slivers.
    content = QWidget()
    content.setLayout(QVBoxLayout())
    content.layout().setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setObjectName("characters_scroll")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    page.layout().addWidget(scroll)
    _characters_add = lambda widget: content.layout().addWidget(widget)
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
        _characters_add(onboarding_group)

    # V12-07: series -> entries browsing; every revision keeps its own
    # package ID / version / digest line so same-named multi-version
    # entries are distinguishable at a glance.
    series_filter = QComboBox()
    series_filter.setObjectName("character_series_filter")
    entries = []

    list_widget = QListWidget()
    list_widget.setObjectName("character_list")
    list_widget.setMinimumHeight(120)
    _characters_add(series_filter)
    _characters_add(list_widget)

    switch_btn = QPushButton("切换到所选角色")
    switch_btn.setObjectName("character_switch")
    rollback_btn = QPushButton("回到上一健康版本")
    rollback_btn.setObjectName("character_rollback")
    details_btn = QPushButton("查看详情")
    details_btn.setObjectName("character_details")
    uninstall_btn = QPushButton("删除所选本地版本")
    uninstall_btn.setObjectName("character_uninstall")
    import_btn = QPushButton("导入本地角色包…")
    import_btn.setObjectName("character_import")
    status = QLabel("")
    status.setObjectName("character_status")
    status.setWordWrap(True)
    status.setTextFormat(Qt.TextFormat.PlainText)
    _wordwrap_height_policy(status)
    import_state = {"worker": None, "mode": None, "disposed": False}

    details_group = QGroupBox("版本详情")
    details_group.setObjectName("character_details_box")
    details_group.hide()
    details_text = QLabel("")
    details_text.setObjectName("character_details_text")
    details_text.setWordWrap(True)
    details_text.setTextFormat(Qt.TextFormat.PlainText)
    _wordwrap_height_policy(details_text)
    details_thumb = QLabel("")
    details_thumb.setObjectName("character_details_thumb")
    details_layout = QHBoxLayout(details_group)
    details_layout.addWidget(details_thumb)
    details_layout.addWidget(details_text, 1)
    _characters_add(details_group)
    # bounded decode cache (V12-07): at most 8 small thumbnails are held,
    # nothing is ever decoded at full character size
    thumbnail_cache = {"map": {}, "max": 8, "max_pixels": 1_048_576}

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
        update_uninstall_button()

    def _protection_reason(entry) -> str | None:
        """Why the selected revision must not be uninstalled right now."""
        active = app._selection_store.get("active")
        lkg = app._selection_store.get("last_known_good")
        if entry.builtin:
            return "内置安全包不通过普通卸载移除"
        pending = {str(key) for key in app.library.pending_delete_keys()}
        if str(entry.revision_key) in pending:
            return "该版本已标记待删除，重启后继续"
        if active is not None and entry.revision_key == active.revision_key():
            return "当前正在使用的版本不能删除；先切换到其它版本"
        if lkg is not None and entry.revision_key == lkg.revision_key():
            return "上一健康版本是恢复依据，不能直接删除；先切换到其它版本"
        return None

    def update_uninstall_button() -> None:
        row = list_widget.currentRow()
        if not 0 <= row < len(entries):
            uninstall_btn.setEnabled(False)
            uninstall_btn.setToolTip("")
            return
        reason = _protection_reason(entries[row])
        uninstall_btn.setEnabled(reason is None)
        uninstall_btn.setToolTip(reason or "")

    def refresh_series_filter() -> None:
        current = series_filter.currentData()
        series_filter.blockSignals(True)
        series_filter.clear()
        series_filter.addItem("全部系列", None)
        for (publisher, series) in sorted(app.catalog.grouped()):
            series_filter.addItem(f"{series}（{publisher}）",
                                  (publisher, series))
        if current is not None:
            index = series_filter.findData(current)
            if index >= 0:
                series_filter.setCurrentIndex(index)
        series_filter.blockSignals(False)

    def refresh() -> None:
        if onboarding_group is not None                 and not app._character_onboarding_pending():
            onboarding_group.hide()
        active = app._selection_store.get("active")
        last_known_good = app._selection_store.get("last_known_good")
        refresh_series_filter()
        chosen_series = series_filter.currentData()
        pending = {str(key) for key in app.library.pending_delete_keys()}
        entries.clear()
        list_widget.clear()
        current_row = -1
        for (publisher, series), group in sorted(app.catalog.grouped().items()):
            if chosen_series is not None and (publisher, series) != chosen_series:
                continue
            for entry in group:
                entries.append(entry)
                label = (f"{entry.display_name} · {entry.package_id}"
                         f" {entry.package_version}"
                         f" · {entry.content_digest[:12]}"
                         f"（{series} · {publisher}）")
                if is_active(entry, active):
                    label += "  ← 当前"
                    current_row = len(entries) - 1
                elif (last_known_good is not None
                        and entry.revision_key
                        == last_known_good.revision_key()):
                    label += "  ← 上一健康"
                if entry.builtin:
                    label += "  [官方]"
                else:
                    label += "  [本地内容 · 发布者/权利未验证]"
                if str(entry.revision_key) in pending:
                    label += "  [待删除]"
                QListWidgetItem(label, list_widget)
        if current_row >= 0:
            list_widget.setCurrentRow(current_row)
        preserve_result = status.text().startswith((
            "切换成功", "切换失败", "导入成功", "导入失败", "角色包已存在",
            "已启用", "已继续", "半写实预览包", "预览包已导入",
            "已回退到上一健康版本", "回退失败", "没有可回退",
            "上一健康版本的包已不在", "已删除本地版本", "文件被占用",
            "暂不能删除该版本",
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

    def _load_thumbnail(entry):
        """Bounded thumbnail decode for the details view (L07-03).

        Returns (pixmap, note).  Bounds, in order: the compressed member
        is capped at 8 MiB on read; PNG pixel dimensions are parsed from
        the IHDR BEFORE any decode and refused above 1 megapixel (about
        4 MiB of RGBA intermediate - the format has no native scaled
        decode, so the bound must exist pre-decode); the decode itself
        asks the reader for a <=96px result; the cache holds at most 8
        entries keyed by revision AND character, so two characters of
        one pack never share a thumbnail.
        """
        import json
        import zipfile

        from PySide6.QtCore import QBuffer, QSize
        from PySide6.QtGui import QImageReader, QPixmap

        cache_key = f"{entry.revision_key}:{entry.character_id}"
        cached = thumbnail_cache["map"].get(cache_key)
        if cached is not None:
            return cached, ""
        note = ""
        pixmap = None
        try:
            from retirement_pet.petpack.archive import MANIFEST_NAME

            record = app.library.get_revision(entry.revision_key)
            if record is None or not Path(record.pack_path).is_file():
                return None, "无缩略图"
            with zipfile.ZipFile(record.pack_path) as archive:
                manifest = json.loads(archive.read(MANIFEST_NAME))
                thumb_id = next(
                    (c.get("thumbnail_asset")
                     for c in manifest.get("characters", [])
                     if c.get("id") == entry.character_id), None)
                asset = next((a for a in manifest.get("assets", [])
                              if a.get("id") == thumb_id), None)
                if asset is None:
                    return None, "无缩略图"
                member = archive.getinfo(str(asset.get("path", "")))
                if member.file_size > 8 * 1024 * 1024:
                    return None, "缩略图文件超限，未读取"
                data = archive.read(member)

            png_signature = bytes(
                [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
            ihdr_tag = bytes([0x49, 0x48, 0x44, 0x52])
            if (data[:8] == png_signature and len(data) >= 24
                    and data[12:16] == ihdr_tag):
                width = int.from_bytes(data[16:20], "big")
                height = int.from_bytes(data[20:24], "big")
            if width * height > thumbnail_cache["max_pixels"]:
                return None, (f"缩略图 {width}x{height} 超出 "
                              f"{thumbnail_cache['max_pixels']} 像素"
                              "解码上限，未解码")

            # keep the buffer alive for the reader's lifetime: a
            # temporary QBuffer(QByteArray(...)) can be collected while
            # Qt still reads from it (native access violation)
            buffer = QBuffer()
            buffer.setData(data)
            buffer.open(QBuffer.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            reader.setAutoTransform(False)
            if width and height:
                if width >= height:
                    target = QSize(
                        96, max(1, height * 96 // width))
                else:
                    target = QSize(
                        max(1, width * 96 // height), 96)
                reader.setScaledSize(target)
            image = reader.read()
            buffer.close()
            if image.isNull():
                return None, "缩略图无法解码"
            pixmap = QPixmap.fromImage(image)
        except Exception:  # noqa: BLE001 - a missing thumb never blocks
            logger.exception("thumbnail load failed")
            return None, "缩略图读取失败"
        cache = thumbnail_cache["map"]
        if len(cache) >= thumbnail_cache["max"]:
            cache.pop(next(iter(cache)))
        cache[cache_key] = pixmap
        return pixmap, ""

    def on_details() -> None:
        row = list_widget.currentRow()
        if not 0 <= row < len(entries):
            return
        entry = entries[row]
        record = app.library.get_revision(entry.revision_key)
        try:
            size = Path(record.pack_path).stat().st_size if record else 0
        except OSError:
            size = 0
        source = ("官方内置发布" if entry.builtin
                  else f"本地导入（{entry.trust_channel}；"
                       "发布者身份与权利声明未验证）")
        details_text.setText(chr(10).join([
            f"角色：{entry.display_name}（{entry.character_id}）",
            f"package ID：{entry.package_id}",
            f"版本：{entry.package_version}",
            "内容摘要："
            + chr(10).join(entry.content_digest[i:i + 16]
                           for i in range(0, len(entry.content_digest), 16)),
            f"来源：{source}",
            f"安装时间：{record.installed_at if record else '未知'}",
            f"包含角色：{record.character_count if record else '?'} 个",
            f"包体：{size / 1024:.0f} KiB（归档预算 200 MiB、"
            "解压总预算 500 MiB，导入校验时强制）",
            "运行预算：解码缓存 48 MiB，全部角色共享 LRU 上限",
        ]))
        thumb, note = _load_thumbnail(entry)
        if thumb is not None:
            details_thumb.setPixmap(thumb)
        else:
            details_thumb.setText(note or "无缩略图")
        details_group.show()

    def on_rollback() -> None:
        active = app._selection_store.get("active")
        last_known_good = app._selection_store.get("last_known_good")
        if last_known_good is None or (active is not None
                                       and last_known_good == active):
            status.setText("没有可回退的上一健康版本。")
            return
        character_id = last_known_good.character_fqid.rsplit(".", 1)[-1]
        entry = next((e for e in app.catalog.entries()
                      if e.revision_key == last_known_good.revision_key()
                      and e.character_id == character_id), None)
        if entry is None:
            status.setText("上一健康版本的包已不在角色库，无法回退。")
            return
        if app._switch_character(entry):
            status.setText(f"已回退到上一健康版本：{entry.display_name}")
        else:
            status.setText("回退失败，已保留当前角色。")
        refresh()

    def on_uninstall() -> None:
        row = list_widget.currentRow()
        if not 0 <= row < len(entries):
            return
        entry = entries[row]
        active = app._selection_store.get("active")
        last_known_good = app._selection_store.get("last_known_good")
        protected = set()
        if active is not None:
            protected.add(active.revision_key())
        if last_known_good is not None:
            protected.add(last_known_good.revision_key())

        def active_guard(rk) -> bool:
            return rk in protected

        try:
            outcome = app.library.request_uninstall(
                entry.revision_key, active_guard=active_guard)
        except Exception:  # noqa: BLE001 - honest refusal, no partial state
            status.setText("暂不能删除该版本（内置、使用中或恢复依据）。")
            return
        if outcome == "pending_delete":
            status.setText("文件被占用，已标记待删除；重启应用后自动继续。")
        else:
            status.setText("已删除本地版本（移入库内回收区；凭证与权利"
                           "事实保留）。")
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
    rollback_btn.clicked.connect(on_rollback)
    details_btn.clicked.connect(on_details)
    uninstall_btn.clicked.connect(on_uninstall)
    import_btn.clicked.connect(on_import)
    series_filter.currentIndexChanged.connect(lambda _index: refresh())
    if onboarding_official_btn is not None:
        onboarding_official_btn.clicked.connect(on_keep_official)
    if onboarding_preview_btn is not None:
        onboarding_preview_btn.clicked.connect(on_enable_preview)
    list_widget.currentRowChanged.connect(update_button)
    hint = QLabel("切换保留退休目标、任务数据与全局设置。")
    hint.setWordWrap(True)
    _wordwrap_height_policy(hint)
    # two rows: a single five-button row would force a ~526px minimum
    # width onto the scrolled content and clip it at the 720x480 minimum
    actions_rows = QVBoxLayout()
    for row_buttons in ((switch_btn, rollback_btn, details_btn),
                        (uninstall_btn, import_btn)):
        row = QHBoxLayout()
        for button in row_buttons:
            row.addWidget(button)
        row.addStretch(1)
        actions_rows.addLayout(row)
    row_wrapper = QWidget()
    row_wrapper.setLayout(actions_rows)
    _characters_add(row_wrapper)
    _characters_add(status)
    _characters_add(hint)
    character_sub = {"unsub": None}

    def activate() -> None:
        refresh()
        if character_sub["unsub"] is None:
            character_sub["unsub"] = app.on_character_changed(refresh)

    def dispose() -> None:
        unsub = character_sub["unsub"]
        if unsub is not None:
            unsub()
            character_sub["unsub"] = None
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

    page.activate = activate
    page.dispose = dispose
    page._import_worker_state = import_state
    page._character_thumbnail_cache = thumbnail_cache
    refresh()
    return page


# -- actions & decisions ----------------------------------------------------------------


def build_actions_page(app) -> QWidget:
    from retirement_pet.models import ActionId

    page = _page()
    info = QGroupBox("动作与决策")
    form = QFormLayout(info)

    current_label = QLabel("")
    current_label.setObjectName("actions_current")

    # ActionRuntime carries no provenance; the change event's reason is
    # where the request came from.  Remember the latest one so a plain
    # re-activate (no event in flight) can still label the source.
    last_reason = {"value": None}

    def _source_text(reason: str | None) -> str:
        if not reason:
            return "引擎"
        if reason == "panel":
            return "面板触发"
        if reason.startswith("resolve:"):
            return "上下文驱动"
        if reason == "random":
            return "自动调度"
        if reason == "user":
            return "互动触发"
        if reason == "audio":
            return "音频触发"
        return reason

    def refresh_current() -> None:
        # the ACTUAL main performance wins (CR-C05): a manual or
        # resolver-driven runtime is what the pet is really doing;
        # bridge.last_performance only covers the context-derived intent
        runtime = app.controller.current
        if runtime is not None:
            current_label.setText(
                f"{runtime.spec.action_id.value}"
                f"（{_source_text(last_reason['value'])}）")
            return
        perf = app.bridge.last_performance
        if perf is not None:
            fallback = ("（素材缺失，已回退）"
                        if perf.missing_semantics else "")
            short = perf.semantic.rsplit(".", 1)[-1]
            if app._action_mode(short) == "disabled":
                fallback += "（该动作已禁用，未执行）"
            current_label.setText(
                f"{perf.semantic}（{perf.source}）{fallback}")
        else:
            current_label.setText("待机")

    refresh_current()
    form.addRow("当前表演", current_label)

    discipline = QGroupBox("纪律矩阵（引擎冻结，角色包不可修改）")
    discipline.setLayout(QFormLayout())
    discipline.layout().addRow(QLabel(
        "会议：随机动作禁止；仅静音且会议安全的请求\n"
        "勿扰：仅勿扰安全动作（休息、音乐）；装饰隐藏\n"
        "工作：允许低频随机；不清除工作事实"))
    random_check = QCheckBox("启用随机小动作")
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
    form.addRow("", random_check)
    form.addRow("", random_status)

    # -- per-action manual trigger / mode / loop (V12-06) -------------------

    def triggerable_semantics() -> list[str]:
        # ActionId values (engine registry names), not manifest semantic keys
        from retirement_pet.models import RANDOM_ACTION_IDS

        names = ["work", "rest", "eat", "exercise", "meeting", "music"]
        names += [action_id.value for action_id in RANDOM_ACTION_IDS]
        return sorted({name for name in names
                       if app.controller.has_action(ActionId(name))
                       and name != "idle"})

    controls = QGroupBox("动作控制（手动触发、自动/禁用、循环）")
    controls_form = QFormLayout(controls)
    actions_status = QLabel("")
    actions_status.setObjectName("actions_status")
    actions_status.setWordWrap(True)
    actions_status.setTextFormat(Qt.TextFormat.PlainText)
    mode_labels = {"auto": "自动", "manual": "仅手动", "disabled": "禁用"}

    def semantic_label(semantic: str) -> str:
        return f"{semantic}（{mode_labels[app._action_mode(semantic)]}）"

    # CR-C05: row labels must follow the EFFECTIVE mode, not the value at
    # construction time, so keep references and refresh them on activate
    mode_row_labels: dict[str, QLabel] = {}
    loop_checks: dict[str, QCheckBox] = {}

    for semantic in triggerable_semantics():
        row = QHBoxLayout()

        mode_combo = QComboBox()
        mode_combo.setObjectName(f"action_mode_{semantic}")
        for mode_value, mode_text in mode_labels.items():
            mode_combo.addItem(mode_text, mode_value)
        mode_combo.setCurrentIndex(
            max(0, mode_combo.findData(app._action_mode(semantic))))

        def make_mode_handler(semantic=semantic, combo_ref=mode_combo):
            def handler(index) -> None:
                mode = str(combo_ref.itemData(index))
                if not app._set_action_mode(semantic, mode):
                    actions_status.setText("保存失败，设置未改变")
                    # restore the control silently: re-triggering the
                    # signal would retry (and re-fail) the write
                    combo_ref.blockSignals(True)
                    combo_ref.setCurrentIndex(max(
                        0, combo_ref.findData(app._action_mode(semantic))))
                    combo_ref.blockSignals(False)
                    return
                actions_status.setText(f"{semantic} 已设为{mode_labels[mode]}")
                label_ref = mode_row_labels.get(semantic)
                if label_ref is not None:
                    label_ref.setText(semantic_label(semantic))
            return handler

        mode_combo.currentIndexChanged.connect(make_mode_handler())
        row.addWidget(mode_combo)

        try:
            spec = app.controller.spec_of(ActionId(semantic))
        except ValueError:
            spec = None
        if spec is not None and spec.max_duration_ms is None:
            # CR-C06/C06-R2: the loop switch is only offered where it has
            # real support - the ACTIVE character's sequence material,
            # whose declared per-frame durations make one full pass real.
            # Availability is (re)derived from the current material in
            # refresh_loop_switches(); parts-based visuals get no fake
            # single-pass control.
            loop_check = QCheckBox("循环")
            loop_check.setObjectName(f"action_loop_{semantic}")
            loop_check.setChecked(app._action_loop(semantic))
            loop_checks[semantic] = loop_check

            def make_loop_handler(semantic=semantic, check_ref=loop_check):
                def handler(on: bool) -> None:
                    if not app._set_action_loop(semantic, on):
                        actions_status.setText("保存失败，设置未改变")
                        check_ref.blockSignals(True)
                        check_ref.setChecked(app._action_loop(semantic))
                        check_ref.blockSignals(False)
                return handler

            loop_check.toggled.connect(make_loop_handler())
            row.addWidget(loop_check)

        trigger_btn = QPushButton("手动触发")
        trigger_btn.setObjectName(f"action_trigger_{semantic}")

        def make_trigger_handler(semantic=semantic):
            def handler() -> None:
                accepted, message = app._request_manual_action(semantic)
                actions_status.setText(message)
                if accepted:
                    refresh_current()
            return handler

        trigger_btn.clicked.connect(make_trigger_handler())
        row.addWidget(trigger_btn)

        wrapper = QWidget()
        wrapper.setLayout(row)
        row_label = QLabel(semantic_label(semantic))
        mode_row_labels[semantic] = row_label
        controls_form.addRow(row_label, wrapper)

    controls_form.addRow("", actions_status)

    # CR-C02: the panel is usable down to 720x480; the stacked group boxes
    # would compress the per-action rows to zero height there, so the whole
    # content scrolls instead and every control keeps its effective size
    content = QWidget()
    content.setLayout(QVBoxLayout())
    content.layout().setContentsMargins(0, 0, 0, 0)
    content.layout().addWidget(info)
    content.layout().addWidget(discipline)
    content.layout().addWidget(controls)
    content.layout().addStretch(1)
    scroll = QScrollArea()
    scroll.setObjectName("actions_scroll")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    page.layout().addWidget(scroll)

    # CR-C05/C05-R1: while the page is active it follows the real
    # performance stream AND context changes - a vetoed context switch
    # fires no action event, so the context store must be observed too.
    # C06-R2: character switches change which material the loop switches
    # refer to, so they refresh as well.  dispose removes every
    # subscription; a disposed page is never driven by later changes.
    unsubscribes: list = []

    def on_action_change(event) -> None:
        last_reason["value"] = (getattr(event, "source", None)
                                or getattr(event, "reason", None))
        refresh_current()

    def refresh_mode_rows() -> None:
        for semantic, label in mode_row_labels.items():
            label.setText(semantic_label(semantic))

    def refresh_loop_switches() -> None:
        for semantic, check in loop_checks.items():
            material = app._action_single_pass(semantic)
            has_sequence = material is not None
            # blockSignals: refreshing the control from the stored
            # preference must never WRITE through the toggled handler
            check.blockSignals(True)
            check.setEnabled(has_sequence)
            check.setChecked(app._action_loop(semantic) and has_sequence)
            check.blockSignals(False)
            if has_sequence:
                frames, total_ms = material
                check.setToolTip(
                    f"当前素材共 {frames} 帧、完整一遍约 "
                    f"{total_ms / 1000:.1f} 秒。关闭后仅手动触发的表演"
                    f"完整播放素材一遍；上下文/自动表演不受此开关影响，"
                    f"循环到事实变化为止")
            else:
                check.setToolTip(
                    "当前角色该动作没有序列素材，无法完整单次播放，"
                    "因此不提供循环开关")

    def refresh_dynamic() -> None:
        refresh_current()
        refresh_mode_rows()
        refresh_loop_switches()

    def refresh() -> None:
        refresh_dynamic()
        if not unsubscribes:
            unsubscribes.append(app.controller.on_change(on_action_change))
            unsubscribes.append(app.contexts.on_change(lambda: refresh_current()))
            unsubscribes.append(app.on_character_changed(refresh_dynamic))

    def dispose() -> None:
        while unsubscribes:
            unsubscribes.pop()()

    page.activate = refresh
    page.dispose = dispose
    return page


# -- text ---------------------------------------------------------------------------------


def build_text_page(app) -> QWidget:
    import re

    from retirement_pet.config_system import UiConfigError
    from retirement_pet.text_profile import TextSafetyError, render

    page = _page()
    intro = QLabel(
        "文案模板为纯文本替换，未知变量原样保留；不允许表达式、标记或"
        "控制字符。作用范围：全局对所有角色生效；当前角色覆盖只影响所选"
        "角色，且优先于全局。")
    # CR-C02: a long unwrapped line squeezes the navigation column
    intro.setWordWrap(True)
    intro.setTextFormat(Qt.TextFormat.PlainText)
    page.layout().addWidget(intro)

    box = QGroupBox("模板（选择、编辑、预览、应用、恢复默认）")
    form = QFormLayout(box)

    key_combo = QComboBox()
    key_combo.setObjectName("text_key_combo")
    key_combo.addItem("倒计时标题（countdown_text）", "countdown_text")
    key_combo.addItem("点击问候（greeting_text）", "greeting_text")

    template_edit = QLineEdit()
    template_edit.setObjectName("text_template_edit")
    result = QLabel("")
    result.setWordWrap(True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    unknown_label = QLabel("")
    unknown_label.setWordWrap(True)
    unknown_label.setTextFormat(Qt.TextFormat.PlainText)
    scope_global = QCheckBox("全局")
    scope_global.setObjectName("text_scope_global")
    scope_character = QCheckBox("当前角色覆盖")
    scope_character.setObjectName("text_scope_character")
    apply_btn = QPushButton("应用")
    apply_btn.setObjectName("text_apply")
    reset_btn = QPushButton("恢复默认")
    reset_btn.setObjectName("text_reset")
    status = QLabel("")
    status.setObjectName("text_status")
    status.setWordWrap(True)
    status.setTextFormat(Qt.TextFormat.PlainText)

    _TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def current_scope() -> str | None:
        return app._active_character_fqid() if scope_character.isChecked() \
            else None

    def load_current() -> None:
        key = str(key_combo.currentData())
        scope = current_scope()
        value = app.ui_config.effective(key, scope)
        template_edit.setText(str(value))
        source = app.ui_config.source_of(key, scope)
        status.setText(f"当前生效来源：{source.value}")
        refresh_preview()

    def refresh_preview() -> None:
        variables = app._text_template_variables()
        template = template_edit.text()
        unknown = sorted({name for name in _TOKEN.findall(template)
                          if name not in SAFE_VARIABLES})
        unknown_label.setText(
            "未知变量（将原样显示）：" + "、".join(unknown)
            if unknown else "")
        try:
            result.setText(render(template, variables))
        except TextSafetyError as exc:
            result.setText(f"模板不可用：{exc}")

    def on_apply() -> None:
        key = str(key_combo.currentData())
        scope = current_scope()
        try:
            saved = app.ui_config.set_user(key, template_edit.text(), scope)
        except UiConfigError as exc:
            status.setText(f"未保存：{exc}")
            return
        if not saved:
            status.setText("保存失败，设置未改变")
            return
        app._sync_countdown_heading()
        where = f"角色 {scope}" if scope else "全局"
        status.setText(f"已应用（{where}）")

    def on_reset() -> None:
        key = str(key_combo.currentData())
        scope = current_scope()
        if not app.ui_config.reset(key, scope):
            status.setText("该范围没有覆盖，无需恢复")
            return
        app._sync_countdown_heading()
        load_current()
        status.setText("已恢复默认")

    key_combo.currentIndexChanged.connect(lambda _i: load_current())
    template_edit.textChanged.connect(refresh_preview)
    scope_global.toggled.connect(lambda _on: load_current())
    scope_character.toggled.connect(lambda _on: load_current())
    apply_btn.clicked.connect(on_apply)
    reset_btn.clicked.connect(on_reset)

    form.addRow("文案项", key_combo)
    form.addRow("模板", template_edit)
    form.addRow("预览", result)
    form.addRow("", unknown_label)
    form.addRow("作用范围", scope_global)
    form.addRow("", scope_character)
    form.addRow("", apply_btn)
    form.addRow("", reset_btn)
    form.addRow("", status)
    form.addRow("可用变量", QLabel("，".join(sorted(SAFE_VARIABLES))))
    scope_global.setChecked(True)
    load_current()
    _add(page, box)
    page.layout().addStretch(1)
    return page


# -- display -----------------------------------------------------------------------------


def build_display_page(app) -> QWidget:
    from retirement_pet.config_system import UiConfigError
    from retirement_pet.layout_policy import combination_allowed

    page = _page()
    box = QGroupBox("显示（预定义布局 × 可见性预设）")
    form = QFormLayout(box)

    layout_combo = QComboBox()
    layout_combo.setObjectName("display_layout_combo")
    for layout_id in LayoutId:
        spec = ALLOWED[layout_id]
        layout_combo.addItem(f"{spec.layout_id.value}（倒计时:{spec.countdown}）",
                             spec.layout_id.value)
    policy_combo = QComboBox()
    policy_combo.setObjectName("display_policy_combo")
    policy_model = QStandardItemModel(policy_combo)

    def policy_items_enabled() -> None:
        # the engine allow-matrix decides which policies combine with the
        # selected layout; forbidden ones stay visible but unselectable
        layout = layout_combo.currentData()
        for index in range(policy_model.rowCount()):
            item = policy_model.item(index)
            item.setEnabled(combination_allowed(
                layout, item.data(Qt.ItemDataRole.UserRole)))

    for policy in POLICIES:
        item = QStandardItem(policy.value)
        item.setData(policy.value, Qt.ItemDataRole.UserRole)
        policy_model.appendRow(item)
    policy_combo.setModel(policy_model)

    def select_current() -> None:
        layout = str(app.ui_config.effective("layout_id"))
        policy = str(app.ui_config.effective("visibility_policy"))
        layout_index = layout_combo.findData(layout)
        policy_index = policy_combo.findData(policy)
        if layout_index >= 0:
            layout_combo.setCurrentIndex(layout_index)
        if policy_index >= 0:
            policy_combo.setCurrentIndex(policy_index)
        policy_items_enabled()

    layout_combo.currentIndexChanged.connect(lambda _i: policy_items_enabled())

    status = QLabel("")
    status.setObjectName("display_status")
    status.setWordWrap(True)
    status.setTextFormat(Qt.TextFormat.PlainText)
    apply_btn = QPushButton("应用布局与可见性")
    apply_btn.setObjectName("display_apply")
    reset_btn = QPushButton("恢复默认（standard + normal）")
    reset_btn.setObjectName("display_reset")

    def on_apply() -> None:
        layout = str(layout_combo.currentData())
        policy = str(policy_combo.currentData())
        # one user operation, one transaction: the CANDIDATE pair is
        # validated as a whole and published in a single save (CR-C03)
        try:
            saved = app.ui_config.set_display(layout, policy)
        except UiConfigError as exc:
            status.setText(f"未保存：{exc}")
            select_current()
            return
        if not saved:
            status.setText("保存失败，设置未改变")
            select_current()
            return
        app._apply_layout_config()
        status.setText(f"已应用并保存：{layout} + {policy}")

    def on_reset() -> None:
        result = app.ui_config.reset_display()
        select_current()
        if result is None:
            status.setText("已是默认布局与可见性，无需恢复")
            return
        if result is False:
            status.setText("恢复失败，设置未改变")
            return
        app._apply_layout_config()
        status.setText("已恢复默认布局与可见性")

    apply_btn.clicked.connect(on_apply)
    reset_btn.clicked.connect(on_reset)

    form.addRow("布局", layout_combo)
    form.addRow("可见性", policy_combo)
    form.addRow("", apply_btn)
    form.addRow("", reset_btn)
    form.addRow("", status)
    select_current()

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
    activity_check = QCheckBox("根据键盘鼠标活动自动切换工作/休息（只看系统空闲时间，不记录按键内容与窗口）")
    activity_check.setObjectName("schedule_activity_link")
    activity_check.setChecked(bool(app.settings.get("activity_link_enabled", True)))
    media_check = QCheckBox("联动系统媒体（Windows 媒体会话，默认关闭）")
    media_check.setObjectName("schedule_media_bridge")
    media_check.setChecked(bool(app.settings.get("media_bridge_enabled", False)))
    media_status = QLabel("")
    media_status.setObjectName("schedule_media_status")

    def refresh_media_status():
        from retirement_pet.media_bridge import (
            STATUS_PAUSED,
            STATUS_PLAYING,
        )

        bridge = app.media_bridge
        snapshot = bridge.snapshot()
        if not snapshot.enabled:
            # honest state: distinguish "user turned it off" from "start
            # failed with the setting still on" (review RR13-05)
            if app.settings.get("media_bridge_enabled", False)                     and snapshot.reason:
                media_status.setText(
                    f"系统媒体联动：开启失败（{snapshot.reason}）")
            else:
                media_status.setText("系统媒体联动：未开启")
            return
        if not snapshot.bridge_available:
            media_status.setText(f"系统媒体联动：不可用（{snapshot.reason}）")
            return
        if snapshot.local_playback_active:
            media_status.setText("当前控制：本地播放（系统媒体挂起）")
        elif snapshot.active is not None:
            state = "播放中" if snapshot.active.status == STATUS_PLAYING                 else "已暂停" if snapshot.active.status == STATUS_PAUSED else "空闲"
            media_status.setText(
                f"当前控制：系统媒体 · {snapshot.active.app_name}（{state}，"
                f"共 {len(snapshot.sessions)} 个会话）")
        else:
            media_status.setText(
                f"系统媒体联动：已开启，暂无会话（共 "
                f"{len(snapshot.sessions)} 个）")

    refresh_media_status()
    app.media_bridge.changed.connect(refresh_media_status)
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
            "media_bridge_enabled": media_check.isChecked(),
            "activity_link_enabled": activity_check.isChecked(),
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
    form.addRow("", activity_check)
    form.addRow("", media_check)
    form.addRow("", media_status)
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
