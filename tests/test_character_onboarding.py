"""One-time character choice: durability, trust binding and switch safety."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PREVIEW_PACK = (
    ROOT / "assets" / "petpack" / "examples" /
    "realistic-retirement-cat-0.1.1.petpack"
)


def _receipt_snapshot(app) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in app.library.receipts_dir.glob("*.json")
    }


@lru_cache(maxsize=1)
def _verified_preview():
    """Run the real isolated preflight once; its result is immutable."""
    from retirement_pet.petpack.local_import import preflight_local_pack

    return preflight_local_pack(PREVIEW_PACK)


@pytest.fixture()
def verified_preview(qt_application):
    return _verified_preview()


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
        instance_name=f"pytest-character-onboarding-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def test_character_choice_is_pending_by_default(app, qt_application):
    from retirement_pet.app import (
        CHARACTER_ONBOARDING_CAMPAIGN,
        CHARACTER_ONBOARDING_SETTING,
    )

    assert app.settings.get(CHARACTER_ONBOARDING_SETTING) != \
        CHARACTER_ONBOARDING_CAMPAIGN
    assert app._character_onboarding_pending()


def test_explicit_official_choice_writes_only_marker_and_preserves_active(
        app, qt_application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from retirement_pet.app import (
        CHARACTER_ONBOARDING_CAMPAIGN,
        CHARACTER_ONBOARDING_SETTING,
    )

    active_before = app._selection_store.get("active")
    lkg_before = app._selection_store.get("last_known_good")
    receipts_before = _receipt_snapshot(app)
    save_observations = []
    real_save = app.settings.save

    def observed_save():
        # At the sole durable write, no ACTIVE/LKG generation or install
        # receipt has changed; only the explicit-choice marker is staged.
        save_observations.append((
            app.settings.get(CHARACTER_ONBOARDING_SETTING),
            app._selection_store.get("active"),
            app._selection_store.get("last_known_good"),
            _receipt_snapshot(app),
        ))
        return real_save()

    monkeypatch.setattr(app.settings, "save", observed_save)
    app._open_control_panel("characters")
    button = app._panel._built["characters"].findChild(
        QPushButton, "character_onboarding_keep_official")
    assert button is not None

    QTest.mouseClick(button, Qt.LeftButton)
    qt_application.processEvents()

    assert save_observations == [(
        CHARACTER_ONBOARDING_CAMPAIGN,
        active_before,
        lkg_before,
        receipts_before,
    )]
    assert app._selection_store.get("active") == active_before
    assert app._selection_store.get("last_known_good") == lkg_before
    assert _receipt_snapshot(app) == receipts_before
    saved = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert saved[CHARACTER_ONBOARDING_SETTING] == CHARACTER_ONBOARDING_CAMPAIGN
    assert not app._character_onboarding_pending()


def test_settings_save_failure_rolls_back_character_choice_marker(
        app, qt_application, monkeypatch):
    from retirement_pet.app import CHARACTER_ONBOARDING_SETTING

    marker_before = app.settings.get(CHARACTER_ONBOARDING_SETTING)
    active_before = app._selection_store.get("active")
    receipts_before = _receipt_snapshot(app)
    monkeypatch.setattr(app.settings, "save", lambda: False)

    assert not app._complete_character_onboarding()

    assert app.settings.get(CHARACTER_ONBOARDING_SETTING) == marker_before
    assert app._selection_store.get("active") == active_before
    assert _receipt_snapshot(app) == receipts_before


def test_manual_primary_offers_choice_without_requesting_activation(
        app, qt_application, monkeypatch):
    opened = []

    def record_open(page, *, activate=True):
        opened.append((page, activate))

    app._headless = False
    monkeypatch.setattr(app, "_open_control_panel", record_open)

    app._maybe_open_character_onboarding()

    assert opened == [("characters", False)]
    assert app._character_onboarding_report_ready
    assert app._character_onboarding_report_state == "offered"
    assert app._character_onboarding_panel_shown


def test_autostart_stays_quiet_but_user_second_launch_opens_choice(
        qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet", "--startup"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=False,
        instance_name=f"pytest-character-onboarding-startup-{tmp_path.name}",
    )
    opened = []

    def record_open(page, *, activate=True):
        opened.append((page, activate))

    monkeypatch.setattr(pet, "_open_control_panel", record_open)
    try:
        pet._maybe_open_character_onboarding()
        assert not pet.window.isVisible()
        assert opened == []
        assert pet._character_onboarding_report_ready
        assert pet._character_onboarding_report_state == \
            "suppressed_autostart"

        pet._show_from_second_instance()
        assert pet.window.isVisible()
        assert opened == [("characters", True)]
        assert pet._character_onboarding_report_state == "offered"
    finally:
        pet.shutdown()


@pytest.mark.parametrize("mismatch", ("archive_digest", "identity"))
def test_bundled_preview_mismatch_is_rejected_before_install(
        app, qt_application, verified_preview, monkeypatch, mismatch):
    from retirement_pet.petpack.identity import PackKey, RevisionKey

    if mismatch == "archive_digest":
        candidate = replace(verified_preview, archive_sha256="0" * 64)
    else:
        candidate = replace(
            verified_preview,
            revision_key=RevisionKey(
                PackKey("community.retirementpet", "lookalike-cat"),
                verified_preview.revision_key.package_version,
                verified_preview.revision_key.content_digest,
            ),
        )
    install_calls = []
    monkeypatch.setattr(
        app,
        "_import_preflighted_character_pack",
        lambda *_args, **_kwargs: install_calls.append(1),
    )
    active_before = app._selection_store.get("active")
    revisions_before = app.library.list_revisions()
    receipts_before = _receipt_snapshot(app)

    ok, message = app._activate_bundled_character_preview(candidate)

    assert not ok
    assert "身份校验失败" in message
    assert install_calls == []
    assert app._selection_store.get("active") == active_before
    assert app.library.list_revisions() == revisions_before
    assert _receipt_snapshot(app) == receipts_before


def test_real_preflight_installs_switches_then_completes_marker(
        app, qt_application, verified_preview):
    from retirement_pet.app import (
        CHARACTER_ONBOARDING_CAMPAIGN,
        CHARACTER_ONBOARDING_SETTING,
    )

    active_before = app._selection_store.get("active")
    receipts_before = _receipt_snapshot(app)

    ok, message = app._activate_bundled_character_preview(verified_preview)

    assert ok, message
    active = app._selection_store.get("active")
    assert active.revision_key() == verified_preview.revision_key
    assert active.character_fqid.endswith(".realistic-cat")
    assert active.generation > active_before.generation
    assert app.library.get_revision(verified_preview.revision_key) is not None
    assert len(_receipt_snapshot(app)) == len(receipts_before) + 1
    assert app.settings.get(CHARACTER_ONBOARDING_SETTING) == \
        CHARACTER_ONBOARDING_CAMPAIGN
    saved = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert saved[CHARACTER_ONBOARDING_SETTING] == CHARACTER_ONBOARDING_CAMPAIGN
    assert not app._character_onboarding_pending()


def test_cas_loser_restores_authority_and_keeps_marker_pending(
        app, qt_application, verified_preview, monkeypatch):
    from retirement_pet.app import CHARACTER_ONBOARDING_SETTING

    active_before = app._selection_store.get("active")
    lkg_before = app._selection_store.get("last_known_good")
    marker_before = app.settings.get(CHARACTER_ONBOARDING_SETTING)
    receipts_before = _receipt_snapshot(app)
    real_commit_if_newer = app.switcher._store.commit_if_newer

    def competing_commit(proposed, slot="active"):
        # Exercise RuntimeSwitcher's real CAS-loser reconciliation: the target
        # preview loses to a newer persisted official selection.  False means
        # "this target was not activated", not "all state stayed byte-equal".
        winner = replace(
            active_before,
            generation=proposed.generation + 5,
            commit_sequence=proposed.commit_sequence + 5,
        )
        app.switcher._store.commit(winner, slot)
        return False

    monkeypatch.setattr(
        app.switcher._store, "commit_if_newer", competing_commit)

    ok, message = app._activate_bundled_character_preview(verified_preview)

    assert not ok
    assert "目标角色未启用" in message
    authoritative = app._selection_store.get("active")
    assert authoritative.revision_key() == active_before.revision_key()
    assert authoritative.character_fqid == active_before.character_fqid
    assert authoritative.generation > active_before.generation
    assert not app.switcher.in_safe_mode
    assert app.switcher.current_runtime is app.window._renderer
    assert app.switcher.current_runtime.character_fqid() == \
        authoritative.character_fqid
    assert app._selection_store.get("last_known_good") == lkg_before
    assert app.settings.get(CHARACTER_ONBOARDING_SETTING) == marker_before
    assert app._character_onboarding_pending()
    record = app.library.get_revision(verified_preview.revision_key)
    assert record is not None
    assert record.pack_path.is_file()
    assert any(
        entry.revision_key == verified_preview.revision_key
        and entry.character_id == "realistic-cat"
        for entry in app.catalog.entries()
    )
    assert len(_receipt_snapshot(app)) == len(receipts_before) + 1

    # A retry reuses the immutable READY revision/receipt and only performs
    # the missing activation; it must not invent a second install fact.
    receipt_after_install = _receipt_snapshot(app)
    monkeypatch.setattr(
        app.switcher._store, "commit_if_newer", real_commit_if_newer)
    ok, message = app._activate_bundled_character_preview(verified_preview)

    assert ok, message
    assert _receipt_snapshot(app) == receipt_after_install
    assert app._selection_store.get("active").revision_key() == \
        verified_preview.revision_key
    assert not app._character_onboarding_pending()
