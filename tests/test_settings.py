"""Settings store: defaults merge, unknown preservation, atomic save, recovery."""

from __future__ import annotations

import json

from retirement_pet.settings import SettingsStore, load_defaults


def make_store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


def test_load_missing_file_uses_defaults(tmp_path):
    store = make_store(tmp_path)
    data = store.load()
    assert data["schema_version"] == 1
    assert data["target_datetime"] == "2060-07-07T21:32:00"
    assert data["always_on_top"] is True


def test_missing_fields_filled_from_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"volume": 0.8}), encoding="utf-8")
    store = make_store(tmp_path)
    data = store.load()
    assert data["volume"] == 0.8
    assert data["meal_times"] == ["08:00", "12:00", "18:30"]


def test_unknown_fields_preserved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"future_key": [1, 2, 3]}), encoding="utf-8")
    store = make_store(tmp_path)
    store.load()
    store.set("volume", 0.5)
    store.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["future_key"] == [1, 2, 3]
    assert raw["volume"] == 0.5


def test_broken_file_backed_up_and_defaults_restored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json !!!", encoding="utf-8")
    store = make_store(tmp_path)
    data = store.load()
    assert data["volume"] == load_defaults()["volume"]
    backups = list(tmp_path.glob("settings.json.broken-*"))
    assert len(backups) == 1


def test_atomic_save_leaves_no_temp(tmp_path):
    store = make_store(tmp_path)
    store.load()
    assert store.save()
    assert (tmp_path / "settings.json").is_file()
    leftovers = [p for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_volume_clamped(tmp_path):
    store = make_store(tmp_path)
    store.load()
    store.set("volume", 5.0)
    assert store.get("volume") == 1.0
    store.set("volume", -2)
    assert store.get("volume") == 0.0


def test_invalid_meal_time_falls_back_to_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"meal_times": ["25:99", "12:00"]}), encoding="utf-8")
    store = make_store(tmp_path)
    data = store.load()
    assert data["meal_times"] == load_defaults()["meal_times"]


def test_valid_meal_times_normalized(tmp_path):
    store = make_store(tmp_path)
    store.load()
    store.set("meal_times", ["8:5", "18:30"])
    assert store.get("meal_times") == ["08:05", "18:30"]


def test_roundtrip_preserves_values(tmp_path):
    store = make_store(tmp_path)
    store.load()
    store.set("random_action_min_interval_s", 60)
    store.save()

    store2 = SettingsStore(tmp_path / "settings.json")
    data = store2.load()
    assert data["random_action_min_interval_s"] == 60


def test_state_store_roundtrip_and_reminders(tmp_path):
    from datetime import datetime

    from retirement_pet.state_store import StateStore

    state = StateStore(tmp_path / "state.json")
    state.load()
    now = datetime(2026, 8, 23, 12, 0, 0)
    entry = state.reminder_entry("meal", now)
    entry["reminded"] = True
    state.save()

    state2 = StateStore(tmp_path / "state.json")
    state2.load()
    again = state2.reminder_entry("meal", now)
    assert again["reminded"] is True

    # different day -> fresh record
    other_day = state2.reminder_entry("meal", datetime(2026, 8, 24, 12, 0, 0))
    assert other_day == {}


def test_state_store_prunes_old_days(tmp_path):
    from datetime import datetime

    from retirement_pet.state_store import StateStore

    state = StateStore(tmp_path / "state.json")
    state.load()
    state.reminder_entry("meal", datetime(2026, 8, 1))["reminded"] = True
    state.reminder_entry("meal", datetime(2026, 8, 23))["reminded"] = True
    state.prune_old_reminder_days(datetime(2026, 8, 23), keep_days=7)
    days = state.get("reminders")
    assert "2026-08-01" not in days
    assert "2026-08-23" in days
