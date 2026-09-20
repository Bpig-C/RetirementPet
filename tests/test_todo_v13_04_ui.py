"""V13-04: Markdown preview safety and priority visibility in the tree.

隔离约定：独立临时数据目录与合成任务；断网/不落盘的预览行为直接对
_MarkdownPreview 组件取证。不读写日用 %APPDATA% 数据。
"""

from __future__ import annotations

import pytest

_MARKDOWN = (
    "# 标题 heading\n\n"
    "**粗体** *斜体* 中英文 mixed 混排 😀emoji\n\n"
    "- 项目一\n- 项目二\n"
    "- [ ] 待办甲\n- [x] 待办乙\n\n"
    "> 引用块 quote\n\n"
    "```python\nprint('code')\n```\n\n"
    "公式 $x^2$ 原样显示\n"
)


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-v13m-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _todo_page(app):
    app._open_control_panel("todo")
    return app._panel._built["todo"]


def _select_task(page, task_id):
    page.tree.setCurrentItem(page._items_by_id[task_id])


def _note_task(app, markdown=_MARKDOWN):
    from retirement_pet.todo import Horizon

    task = app.todo.add_task("带备注任务", Horizon.SHORT)
    app.todo.set_note(task.id, markdown)
    return task


# -- Markdown preview core --------------------------------------------------------


def test_preview_renders_markdown_and_returns_to_edit(app, qt_application):
    task = _note_task(app)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    assert page.note_editor.toPlainText() == _MARKDOWN

    page._note_mode_preview.click()
    qt_application.processEvents()
    assert page.note_preview.isVisible()
    assert not page.note_editor.isVisible()
    text = page.note_preview.toPlainText()
    for expected in ("标题", "粗体", "项目一", "待办甲", "引用块",
                     "print", "😀emoji"):
        assert expected in text, expected
    # raw markdown syntax is not echoed for the rendered parts
    assert "**粗体**" not in text

    page._note_mode_edit.click()
    qt_application.processEvents()
    assert page.note_editor.isVisible()
    assert not page.note_preview.isVisible()
    assert page.note_editor.toPlainText() == _MARKDOWN  # draft intact


def test_preview_mirrors_unsaved_draft_without_writing_store(
        app, qt_application):
    task = _note_task(app, "原始备注")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page.note_editor.setPlainText("# 未保存草稿标题")
    qt_application.processEvents()

    page._note_mode_preview.click()
    qt_application.processEvents()
    assert "未保存草稿标题" in page.note_preview.toPlainText()
    # previewing never persists anything
    assert app.todo.note_for(task.id) == "原始备注"


def test_preview_strips_malicious_html_and_scripts(app, qt_application):
    malicious = (
        "[正常链接](https://example.com)\n\n"
        "<script>alert(1)</script>\n\n<b>bold</b> "
        '<img src="http://evil.example/x.png" onerror="steal()">'
    )
    task = _note_task(app, malicious)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()

    html = page.note_preview.toHtml()
    plain = page.note_preview.toPlainText()
    assert "<script" not in html and "onerror" not in html
    assert "alert(1)" not in plain  # raw HTML is dropped, not executed
    assert "<img" not in html  # inline img tags are not rendered
    assert "steal" not in plain
    # the paragraph before the hostile HTML keeps its link text
    assert "正常链接" in plain


def test_preview_never_loads_any_resource():
    """The resource hook is the no-network guarantee for every URL kind."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument

    from retirement_pet.ui.panel.todo_page import _MarkdownPreview

    preview = _MarkdownPreview()
    for url in ("http://evil.example/x.png",
                "https://evil.example/y.js",
                "file:///example/secret.ini",
                "data:text/html,<b>x</b>",
                "qrc:/steal"):
        variant = preview.loadResource(
            QTextDocument.ImageResource, QUrl(url))
        assert variant in (None, "", 0, b"")  # nothing fetchable


def test_remote_image_markdown_stays_unfetched(app, qt_application):
    task = _note_task(app, "![头像](http://evil.example/pic.png)")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    # the markdown image is rendered as an image element but the resource
    # hook answers nothing: no bytes are ever fetched
    from PySide6.QtCore import QUrl as _QU
    assert page.note_preview.loadResource(
        2, _QU("http://evil.example/pic.png")) in (None, "", 0, b"")


def test_task_list_checkboxes_are_display_only(app, qt_application):
    task = _note_task(app)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    assert page.note_preview.isReadOnly()
    # the todo store is untouched by any preview interaction
    assert app.todo.all_tasks()[0].status.value == "open"


def test_math_formula_shown_literally(app, qt_application):
    task = _note_task(app, "公式 $x^2 + y^2 = z^2$ 保持原样")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    assert "$x^2 + y^2 = z^2$" in page.note_preview.toPlainText()


def test_long_unbroken_text_renders(app, qt_application):
    long_text = "无断词" + "长" * 600 + "文本"
    task = _note_task(app, long_text)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    assert "长" * 600 in page.note_preview.toPlainText()


def test_preview_survives_task_switch_and_restore(app, qt_application):
    from retirement_pet.todo import Horizon

    task = _note_task(app, "# 第一")
    other = app.todo.add_task("无备注", Horizon.SHORT)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    assert "第一" in page.note_preview.toPlainText()

    _select_task(page, other.id)  # switch away and back in preview mode
    qt_application.processEvents()
    assert "第一" not in page.note_preview.toPlainText()  # no stale content
    _select_task(page, task.id)
    qt_application.processEvents()
    assert "第一" in page.note_preview.toPlainText()


# -- controlled link opening -------------------------------------------------------


def _link_setup(qt_application, app, url):
    task = _note_task(app, f"[点我]({url})")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()
    return page


def test_http_link_opens_only_after_confirmation(app, qt_application,
                                                 monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page = _link_setup(qt_application, app, "https://example.com/docs")
    opened = []
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))
    asked = []

    def ask(*_args, **_kwargs):
        asked.append(True)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(ask))
    page.note_preview._on_anchor("https://example.com/docs")
    assert asked and not opened  # refused: nothing opened

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: QMessageBox.Yes))
    page.note_preview._on_anchor("https://example.com/docs")
    assert opened == ["https://example.com/docs"]


def test_non_http_links_are_refused(app, qt_application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page = _link_setup(qt_application, app, "[x](file:///example/secret)")
    opened = []
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: QMessageBox.Yes))
    for url in ("file:///example/secret", "data:text/html,x",
                "javascript:alert(1)", "ftp://example.com/f"):
        page.note_preview._on_anchor(url)
    assert opened == []
    assert "只允许" in page.status.text()


# -- priority visibility in the main tree -------------------------------------------


def test_priority_tags_visible_in_status_column(app, qt_application):
    from retirement_pet.todo import Horizon, Level

    high_high = app.todo.add_task("重要且紧急", Horizon.SHORT)
    app.todo.set_importance(high_high.id, Level.HIGH)
    app.todo.set_urgency(high_high.id, Level.HIGH)
    high_low = app.todo.add_task("重要不紧急", Horizon.SHORT)
    app.todo.set_importance(high_low.id, Level.HIGH)
    app.todo.set_urgency(high_low.id, Level.LOW)
    plain = app.todo.add_task("普通任务", Horizon.SHORT)

    page = _todo_page(app)
    qt_application.processEvents()
    assert page._items_by_id[high_high.id].text(2) == "进行中·要·急"
    assert page._items_by_id[high_low.id].text(2) == "进行中·要"
    assert page._items_by_id[plain.id].text(2) == "进行中"
    # tooltip carries the full classification (screen-reader friendly)
    assert "重要：高" in page._items_by_id[high_high.id].toolTip(2)
    assert "紧急：低" in page._items_by_id[high_low.id].toolTip(2)
    assert "紧急：未设" in page._items_by_id[plain.id].toolTip(2) or \
        page._items_by_id[plain.id].toolTip(2) == ""


def test_priority_tags_follow_completion_state(app, qt_application):
    from retirement_pet.todo import Horizon, Level

    task = app.todo.add_task("完成后仍可见", Horizon.SHORT)
    app.todo.set_importance(task.id, Level.HIGH)
    app.todo.complete(task.id)
    page = _todo_page(app)
    page.view_box.setCurrentIndex(1)  # 已完成
    page.refresh()
    assert page._items_by_id[task.id].text(2) == "已完成·要"


# -- 720x480 reachability -------------------------------------------------------


def test_720x480_all_controls_reachable(app, qt_application):
    from retirement_pet.todo import Horizon, Level

    task = app.todo.add_task("小屏可达", Horizon.SHORT)
    app.todo.set_importance(task.id, Level.HIGH)
    app.todo.set_urgency(task.id, Level.HIGH)
    app.todo.set_note(task.id, _MARKDOWN)
    page = _todo_page(app)
    page.resize(720, 480)
    page.show()
    qt_application.processEvents()
    _select_task(page, task.id)
    qt_application.processEvents()

    for name, button in page.buttons.items():
        assert button.isVisibleTo(page), name
        assert not button.visibleRegion().isEmpty(), name
    # note panel controls reachable in both modes
    for control in (page._note_mode_edit, page._note_mode_preview,
                    page._note_save_button, page.note_editor):
        assert control.isVisibleTo(page)
    page._note_mode_preview.click()
    qt_application.processEvents()
    assert page.note_preview.isVisibleTo(page)
    assert not page.note_preview.visibleRegion().isEmpty()
    page._note_mode_edit.click()
    qt_application.processEvents()
    page.hide()


# -- acceptance E: image stripping and 720 layout --------------------------------


def test_preview_strips_all_image_schemes_including_file_and_data(
        app, qt_application):
    """Acceptance E-#1: file:// and data: images must never load - the
    sanitizer strips every markdown image BEFORE parsing (pixel-checked)."""
    from pathlib import Path
    import struct
    import zlib

    def tiny_png(rgb):
        def chunk(tag, data):
            c = tag + data
            return (struct.pack(">I", len(data)) + c
                    + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
        ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
        raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    green = tmp_path_file = Path(app._data_dir) / "green.png"
    green.write_bytes(tiny_png((0, 200, 0)))

    import base64
    data_uri = "data:image/png;base64," + base64.b64encode(
        tiny_png((0, 200, 0))).decode()
    markdown = (
        f"![绿块](file:///{green.as_posix()})\n\n"
        f"![数据](<{data_uri}>)\n\n"
        f"![裸路径]({green.as_posix()})\n\n"
        f"![带标题](http://evil.example/x.png \"title\")\n\n"
        "![逃逸](http://evil.example/y.png)"
    )
    task = _note_task(app, markdown)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()

    plain = page.note_preview.toPlainText()
    assert "<img" not in page.note_preview.toHtml()
    assert "绿块" in plain  # alt text survives as text
    assert "逃逸" in plain
    # the rendered viewport contains no green pixel from any image
    grab = page.note_preview.grab().toImage()
    greens = 0
    for x in range(0, grab.width(), 3):
        for y in range(0, grab.height(), 3):
            color = grab.pixelColor(x, y)
            if color.green() > 150 and color.red() < 80 \
                    and color.blue() < 80:
                greens += 1
    assert greens == 0, f"{greens} green pixels: an image was rendered"


def test_720x480_status_column_visible_without_horizontal_scroll(
        app, qt_application):
    """Acceptance E-#2: the priority tags column must stay inside the
    viewport at 720x480 with the note pane open."""
    from retirement_pet.todo import Horizon, Level

    task = app.todo.add_task("小屏优先级", Horizon.SHORT)
    app.todo.set_importance(task.id, Level.HIGH)
    app.todo.set_urgency(task.id, Level.HIGH)
    app.todo.set_note(task.id, "# 备注内容")
    page = _todo_page(app)
    page.resize(720, 480)
    page.show()
    qt_application.processEvents()
    _select_task(page, task.id)
    qt_application.processEvents()

    viewport_width = page.tree.viewport().width()
    columns_total = sum(page.tree.columnWidth(i) for i in range(4))
    assert columns_total <= viewport_width, (
        f"columns {columns_total} > viewport {viewport_width}")
    header_x = page.tree.columnViewportPosition(2)
    assert header_x is not None and 0 <= header_x \
        and header_x + page.tree.columnWidth(2) <= viewport_width
    # no horizontal scrollbar needed
    assert page.tree.horizontalScrollBar().maximum() == 0
    page.hide()


# -- CR13-01: every image syntax must fail closed at the document layer ----


def _tiny_png(rgb, size=24):
    import struct
    import zlib

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _count_green(page) -> int:
    grab = page.note_preview.grab().toImage()
    return sum(
        1 for x in range(0, grab.width(), 2)
        for y in range(0, grab.height(), 2)
        if grab.pixelColor(x, y).green() > 150
        and grab.pixelColor(x, y).red() < 80
        and grab.pixelColor(x, y).blue() < 80)


def test_cr13_01_reference_style_images_never_load(app, qt_application):
    """The controller's original counter-example: reference-style images
    used to bypass the inline-only strip and read a local file."""
    from pathlib import Path

    png = Path(app._data_dir) / "secret-reference.png"
    png.write_bytes(_tiny_png((0, 200, 0)))
    markdown = (f"![secret][local]\n\n[local]: <{png.as_uri()}>\n\n"
                f"![folded][]\n\n[folded]: {png.as_uri()}\n\n"
                f"![shortcut]\n\n[shortcut]: {png.as_uri()}\n")
    task = _note_task(app, markdown)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()

    plain = page.note_preview.toPlainText()
    assert "secret" in plain  # alt text survives as text
    assert png.as_uri() not in plain  # definition URLs are dropped
    assert _count_green(page) == 0, "a reference image was rendered"
    assert "<img" not in page.note_preview.toHtml()


def test_cr13_01_document_loader_is_the_fail_closed_root():
    """The resource refusal lives at the DOCUMENT layer: any load request
    that reaches the document (whatever produced it) gets nothing."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument

    from retirement_pet.ui.panel.todo_page import (
        SecurePreviewDocument,
        _MarkdownPreview,
    )

    for document in (SecurePreviewDocument(), _MarkdownPreview().document()):
        for url in ("file:///C:/any/secret.png",
                    "file:///etc/passwd",
                    "data:image/png;base64,aGVsbG8=",
                    "qrc:/nothing",
                    "image.png",  # relative
                    "http://evil.example/x.png"):
            result = document.loadResource(
                QTextDocument.ImageResource, QUrl(url))
            assert result in (None, "", 0, b""), url


def test_cr13_01_relative_and_escaped_images_stripped(app, qt_application):
    from pathlib import Path

    png = Path(app._data_dir) / "plain-green.png"
    png.write_bytes(_tiny_png((0, 200, 0)))
    markdown = (f"![relative]({png.name})\n\n"
                f"![windows](screens\{png.name})\n\n"
                "the literal \![not an image](x.png) stays\n")
    task = _note_task(app, markdown)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page._note_mode_preview.click()
    qt_application.processEvents()

    assert _count_green(page) == 0
    plain = page.note_preview.toPlainText()
    # the escaped bang renders as literal text and the brackets become a
    # plain LINK (no image loading); the important part is zero pixels
    assert "not an image" in plain and "![not an image]" not in plain
