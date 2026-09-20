"""SingleInstanceGuard: primary acquires, secondary is rejected + notifies."""

from __future__ import annotations

import pytest
from PySide6.QtNetwork import QLocalServer

from retirement_pet.single_instance import (
    QUIET_COMMAND,
    SHOW_COMMAND,
    SingleInstanceGuard,
    server_name,
)


def test_server_name_is_user_scoped():
    name = server_name()
    assert name.startswith("RetirementPet-single-")
    assert len(name) > len("RetirementPet-single-")  # contains a user part


def test_primary_acquires(qt_application):
    guard = SingleInstanceGuard(name="pytest-primary-test")
    try:
        assert guard.acquire()
        assert guard.is_primary
    finally:
        guard.release()


def test_second_instance_rejected_and_show_sent(qt_application):
    shown = []
    primary = SingleInstanceGuard(name="pytest-secondary-test")
    primary.on_show_requested = lambda: shown.append(True)
    assert primary.acquire()
    try:
        secondary = SingleInstanceGuard(name="pytest-secondary-test")
        assert not secondary.acquire()
        assert not secondary.is_primary
        # The show command should arrive asynchronously.
        import time

        deadline = time.time() + 2
        while not shown and time.time() < deadline:
            qt_application.processEvents()
            time.sleep(0.02)
        assert shown
    finally:
        primary.release()


def test_quiet_autostart_duplicate_does_not_show_primary(qt_application):
    shown = []
    primary = SingleInstanceGuard(name="pytest-quiet-secondary-test")
    primary.on_show_requested = lambda: shown.append(True)
    assert primary.acquire()
    try:
        secondary = SingleInstanceGuard(name="pytest-quiet-secondary-test")
        assert not secondary.acquire(command=QUIET_COMMAND)
        assert not secondary.is_primary

        # Deliver the quiet command; the server acknowledges it without
        # invoking the focus-stealing show callback.
        import time

        deadline = time.time() + 0.5
        while time.time() < deadline:
            qt_application.processEvents()
            time.sleep(0.01)
        assert shown == []
    finally:
        primary.release()


def test_acquire_rejects_unknown_secondary_command_before_connecting():
    guard = SingleInstanceGuard(name="pytest-invalid-secondary-command")
    with pytest.raises(ValueError, match="unsupported"):
        guard.acquire(command="arbitrary")


def test_release_allows_new_primary(qt_application):
    first = SingleInstanceGuard(name="pytest-release-test")
    assert first.acquire()
    first.release()

    second = SingleInstanceGuard(name="pytest-release-test")
    try:
        assert second.acquire()
    finally:
        second.release()


def test_stale_server_cleared(qt_application):
    """A crashed owner leaves a stale socket; the next start still works."""
    zombie = SingleInstanceGuard(name="pytest-stale-test")
    assert zombie.acquire()
    # Simulate crash: server object abandoned without release().
    zombie._server.close()
    zombie._server.deleteLater()
    zombie._server = None

    fresh = SingleInstanceGuard(name="pytest-stale-test")
    try:
        assert fresh.acquire()  # removeServer cleared the stale entry
    finally:
        fresh.release()


def test_cr13_02_server_socket_options_are_user_scoped(qt_application):
    """CR13-02: the named-pipe endpoint is created with a same-user DACL
    (UserAccessOption); username-based naming alone is not authorisation."""
    guard = SingleInstanceGuard(name="pytest-user-scope-test")
    try:
        assert guard.acquire()
        server = guard._server
        assert server is not None and server.isListening()
        options = server.socketOptions()
        assert options & QLocalServer.UserAccessOption, hex(int(options))
    finally:
        guard.release()


def test_cr13_02_second_instance_connects_under_same_user(qt_application):
    """The user-scoped DACL must not break the normal same-user flows."""
    shown = []
    primary = SingleInstanceGuard(name="pytest-scope-flow-test")
    primary.on_show_requested = lambda: shown.append(True)
    assert primary.acquire()
    try:
        secondary = SingleInstanceGuard(name="pytest-scope-flow-test")
        assert not secondary.acquire()  # same user connects and notifies
        import time

        deadline = time.time() + 2
        while not shown and time.time() < deadline:
            qt_application.processEvents()
            time.sleep(0.02)
        assert shown
    finally:
        primary.release()
