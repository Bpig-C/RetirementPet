"""Multi-screen / DPI verification harness (V12-08, schema 3).

Every scenario carries an explicit PASS / FAIL / SKIP / INVALID result
derived from STRUCTURED EVIDENCE - the harness labels results with the
same evidence-driven rules the consumer uses to re-derive them, so a
report without real observations (no displays, no window, no panel
observation, no topology event) can never validate as PASS.  A missing
identity or observation is INVALID; only genuinely absent environment
conditions (second display, mixed DPI, negative coordinates, real
topology events) are SKIP, and a SKIP never becomes a PASS.

    python scripts/verify_displays.py --source
    python scripts/verify_displays.py --exe --artifact-dir DIR \\
        --expected-build-id ID --expected-exe-sha256 SHA

Scenarios (all required, all evidence-checked):

- identity_binding: launched process, app self-report and the NATIVE
  window owner must agree (owner pid == reported pid; owner is the
  launched process or its documented child); EXE mode hashes the
  owner's actual image and binds it to the receipt value.
- display_facts: per-monitor DPI via Shcore.GetDpiForMonitor with
  return-value and range checks; full four-edge work-area sanity; the
  topology is always recomputed from the display records.
- window_containment: the observed rect is fully inside one work area.
- foot_stability: no-input samples are identical; the panel is REALLY
  opened and REALLY closed over IPC (visibility observed, not assumed
  from the IPC return); closed-state rect and foot anchor must return
  within a stated tolerance; every phase must stay contained.
- per_display_landing: the window is driven to EVERY display and its
  containment and DPI are recorded per screen; unvisited targets
  cannot pass.
- topology_recovery: only a really observed topology change (before /
  event / after preserved) with re-contained recovery counts; no event
  is an honest SKIP.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from verify_windows import (  # noqa: E402
    Target,
    find_pet_hwnd,
    hwnd_alive,
    wait_exited,
)

SCHEMA = 3
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
INVALID = "INVALID"
RESULT_VALUES = (PASS, FAIL, SKIP, INVALID)
REQUIRED_SCENARIOS = (
    "identity_binding",
    "display_facts",
    "window_containment",
    "foot_stability",
    "per_display_landing",
    "topology_recovery",
)
PET_WINDOW_REPORT = "window-report.json"
PANEL_WINDOW_TITLE = "退休宠物 · 控制面板"
#: foot tolerance in Qt LOGICAL pixels (device-independent).  Qt
#: mapToGlobal/global coordinates are logical, so the comparison and
#: the tolerance share one unit at every DPI; window_dpi is recorded
#: so an auditor can convert to physical pixels independently.
FOOT_TOLERANCE_DIP = 8
FOOT_TOLERANCE_PX = FOOT_TOLERANCE_DIP  # legacy alias (same unit)
#: a diagnostic is fresh when generated_at is at most this old at read
#: time (the report refreshes every 400ms while the flag is active)
DIAGNOSTIC_MAX_AGE_S = 5.0
#: max |native_physical/dpr - diagnostic_frame| in DIP when binding a
#: diagnostic to the natively observed window (same-tick association)
FRAME_ASSOCIATION_TOLERANCE_DIP = 2.0
#: sane per-monitor DPI range (48 = 50%, 768 = 800%)
DPI_MIN, DPI_MAX = 48, 768
#: PROCESS_QUERY_LIMITED_INFORMATION
_PROCESS_QUERY_LIMITED = 0x1000

user32 = ctypes.WinDLL("user32", use_last_error=True)
# the harness must observe TRUE physical coordinates: without per-
# monitor awareness, Windows virtualizes GetWindowRect/monitor rects
# on scaled displays and every observation mixes units (final audit)
try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:  # noqa: BLE001 - pre-8.1 fallback
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.GetWindowThreadProcessId.argtypes = [wt.HWND,
                                            ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, wt.LPRECT, wt.LPARAM)


def _read_displays() -> list[dict]:
    displays: list[dict] = []

    def callback(hmonitor, _hdc, _rect, _data):
        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return True
        dpi = 0
        dpi_source = "unavailable"
        try:
            shcore = ctypes.WinDLL("shcore")
            dpi_x, dpi_y = wt.UINT(), wt.UINT()
            # 96 == 100%; per-monitor value with a checked return code -
            # a failed call must never masquerade as a measurement
            if shcore.GetDpiForMonitor(
                    wt.HMONITOR(hmonitor), 0, ctypes.byref(dpi_x),
                    ctypes.byref(dpi_y)) == 0:
                dpi = int(dpi_x.value)
                dpi_source = "per_monitor"
        except Exception:  # noqa: BLE001 - pre-8.1 systems
            dpi_source = "unavailable"
        if dpi_source != "per_monitor":
            dpi = int(user32.GetDpiForSystem())
            dpi_source = "system_fallback"
        monitor = [info.rcMonitor.left, info.rcMonitor.top,
                   info.rcMonitor.right - info.rcMonitor.left,
                   info.rcMonitor.bottom - info.rcMonitor.top]
        work = [info.rcWork.left, info.rcWork.top,
                info.rcWork.right - info.rcWork.left,
                info.rcWork.bottom - info.rcWork.top]
        displays.append({
            "device": info.szDevice,
            "primary": bool(info.dwFlags & 1),
            "monitor": monitor,
            "work": work,
            "dpi": dpi,
            "dpi_source": dpi_source,
        })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return displays


def _window_owner_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _process_image(pid: int) -> dict | None:
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)):
            return None
        path = Path(buf.value)
        return {"path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _process_parent_pid(pid: int) -> int | None:
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD),
                    ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeap", ctypes.POINTER(wt.ULONG)),
                    ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == wt.HANDLE(-1).value or not snapshot:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snapshot)


def _panel_visible(pid: int) -> bool:
    found = []

    @MONITORENUMPROC
    def _unused(_hmonitor, _hdc, _rect, _data):  # pragma: no cover
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def on_window(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            owner = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == PANEL_WINDOW_TITLE:
                    found.append(True)
        return True

    user32.EnumWindows(WNDENUMPROC(on_window), 0)
    return bool(found)


def _move_window(hwnd: int, rect: list[int]) -> bool:
    left, top, width, height = rect
    # SWP_NOZORDER | SWP_NOACTIVATE
    return bool(user32.SetWindowPos(
        wt.HWND(hwnd), wt.HWND(0), left, top, width, height, 0x0004 | 0x0010))


class Observers:
    """Win32 observation surface; injected fakes replace it in tests."""

    def __init__(self):
        pass

    def enumerate_displays(self) -> list[dict]:
        return _read_displays()

    def window_alive(self, hwnd: int) -> bool:
        return hwnd_alive(hwnd)

    def read_observation(self, report_file):
        """Read the app's live diagnostic report (window report), which
        carries the real BodyLayout foot anchor (R08-03)."""
        try:
            data = json.loads(Path(report_file).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def window_dpi(self, hwnd: int) -> int | None:
        """Real per-WINDOW DPI observation (user32!GetDpiForWindow).

        This is what the window actually renders at after a move - not
        a value copied from the display record (R08-02).
        """
        dpi = user32.GetDpiForWindow(wt.HWND(hwnd))
        return int(dpi) if dpi else None

    def window_rect(self, hwnd: int) -> list[int] | None:
        rect = wt.RECT()
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return None
        return [rect.left, rect.top, rect.right - rect.left,
                rect.bottom - rect.top]

    def window_owner_pid(self, hwnd: int) -> int:
        return _window_owner_pid(hwnd)

    def process_image(self, pid: int) -> dict | None:
        return _process_image(pid)

    def process_parent_pid(self, pid: int) -> int | None:
        return _process_parent_pid(pid)

    def panel_visible(self, pid: int) -> bool:
        return _panel_visible(pid)

    def move_window(self, hwnd: int, rect: list[int]) -> bool:
        return _move_window(hwnd, rect)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# -- evidence-driven derivation (shared by harness and validator) ------------


def topology_of(displays: list[dict]) -> dict:
    # the display records travel INSIDE the topology dict so a report's
    # before/after snapshots are auditable (fingerprint recomputation)
    return {
        "display_count": len(displays),
        "multi_screen": len(displays) >= 2,
        "mixed_dpi": len({d.get("dpi") for d in displays}) > 1,
        "negative_coordinates": any(
            d["monitor"][0] < 0 or d["monitor"][1] < 0 for d in displays),
        "fingerprint": json.dumps(
            [[d.get("device"), d.get("monitor"), d.get("work"), d.get("dpi")]
             for d in displays], ensure_ascii=False),
        "displays": displays,
    }


def _valid_display(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    monitor, work = entry.get("monitor"), entry.get("work")
    dpi, device = entry.get("dpi"), entry.get("device")
    for rect in (monitor, work):
        if (not isinstance(rect, list) or len(rect) != 4
                or not all(isinstance(v, int) for v in rect)
                or rect[2] <= 0 or rect[3] <= 0):
            return False
    ml, mt, mw, mh = monitor
    wl, wt2, ww, wh = work
    # full four-edge sanity: the work area sits inside the monitor
    if not (ml <= wl and mt <= wt2 and wl + ww <= ml + mw
            and wt2 + wh <= mt + mh):
        return False
    if (not isinstance(dpi, int)
            or not DPI_MIN <= dpi <= DPI_MAX
            or entry.get("dpi_source") != "per_monitor"):
        return False
    return isinstance(device, str) and bool(device)


def work_area_containing(rect: list[int],
                         displays: list[dict]) -> dict | None:
    left, top, width, height = rect
    right, bottom = left + width, top + height
    for display in displays:
        wl, wtop, ww, wh = display["work"]
        if (wl <= left and wtop <= top
                and right <= wl + ww and bottom <= wtop + wh):
            return display
    return None


def _rect_delta(a: list[int], b: list[int]) -> int:
    return max(abs(x - y) for x, y in zip(a, b))


def derive_identity_result(evidence: object,
                           candidate: dict | None = None) -> tuple[str, str]:
    """Bind launch, ownership and observed-image identity (R08-01)."""
    if not isinstance(evidence, dict):
        return INVALID, "identity evidence missing or malformed"
    hwnd = evidence.get("hwnd")
    reported = evidence.get("reported_pid")
    launched = evidence.get("launched_pid")
    owner = evidence.get("owning_pid")
    ownership = evidence.get("ownership")
    image_sha = evidence.get("image_sha256")
    for name, value in (("hwnd", hwnd), ("reported_pid", reported),
                        ("launched_pid", launched), ("owning_pid", owner)):
        if not isinstance(value, int) or value <= 0:
            return INVALID, f"identity field {name} missing or invalid"
    if not isinstance(image_sha, str) or len(image_sha) != 64:
        return INVALID, ("observed process image hash missing - the "
                         "window cannot be tied to a verified binary")
    if evidence.get("ownership_verified") is not True:
        return INVALID, "window ownership was not verified"
    if owner != reported:
        return FAIL, (f"self-reported pid {reported} does not own the "
                      f"window (owner {owner})")
    if ownership == "direct":
        # even a "direct" window must belong to the process WE launched
        if owner != launched:
            return FAIL, (f"window owner {owner} is not the launched "
                          f"process {launched}")
    elif ownership == "hosted":
        if evidence.get("host_parent_pid") != launched:
            return FAIL, ("window owner is not the launched process nor "
                          "its documented child")
    else:
        return INVALID, f"unknown ownership kind {ownership!r}"
    if isinstance(candidate, dict):
        candidate_sha = candidate.get("exe_sha256")
        if not isinstance(candidate_sha, str) or len(candidate_sha) != 64:
            return INVALID, ("candidate record lacks the observed image "
                             "hash")
        if candidate_sha.lower() != image_sha.lower():
            return FAIL, ("candidate hash differs from the observed "
                          "process image")
    return PASS, ("process, self-report and native owner agree; image "
                  "hash matches the candidate")


def derive_display_facts_result(evidence: object) -> tuple[str, str]:
    if not isinstance(evidence, dict):
        return INVALID, "display facts evidence missing or malformed"
    displays = evidence.get("displays")
    if not isinstance(displays, list) or not displays:
        return INVALID, "no display records"
    broken = []
    for entry in displays:
        try:
            ok = _valid_display(entry)
        except Exception:  # noqa: BLE001 - malformed entries are facts
            ok = False
        if not ok:
            broken.append(entry.get("device")
                          if isinstance(entry, dict) else repr(entry)[:40])
    if broken:
        return FAIL, (f"invalid or degraded display records: {broken}; "
                      "per-monitor DPI measurement is required")
    return PASS, f"{len(displays)} display(s) with verified per-monitor DPI"


def _valid_rect(rect: object) -> bool:
    """A window rect with real, positive extent (R08-05)."""
    return (isinstance(rect, list) and len(rect) == 4
            and all(isinstance(v, int) and not isinstance(v, bool)
                    for v in rect)
            and rect[2] > 0 and rect[3] > 0)


def _valid_foot(foot: object) -> bool:
    return (isinstance(foot, list) and len(foot) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in foot))


def derive_containment_result(evidence: object,
                              displays: list[dict]) -> tuple[str, str]:
    if not isinstance(evidence, dict):
        return INVALID, "containment evidence missing or malformed"
    rect = evidence.get("rect")
    if not _valid_rect(rect):
        return INVALID, "window rect missing or invalid"
    # R08-04: judge the rect against the topology AT ITS SAMPLE TIME -
    # the evidence binds the display records it was observed against
    own_displays = evidence.get("displays")
    if isinstance(own_displays, list) and own_displays:
        displays = own_displays
    container = work_area_containing(rect, displays)
    if container is None:
        return FAIL, f"window {rect} is not fully inside any work area"
    return PASS, (f"window {rect} fully inside {container['device']} "
                  f"work area at {container['work']}")


def _phase_parts(position: object) -> tuple[list[int] | None,
                                            list[float] | None,
                                            list[dict] | None]:
    """(rect, screen-space foot anchor, displays) of one phase record."""
    if not isinstance(position, dict):
        return None, None, None
    rect = position.get("rect")
    own_displays = position.get("displays")
    return (rect if _valid_rect(rect) else None,
            position.get("foot_point_screen") if _valid_foot(
                position.get("foot_point_screen")) else None,
            own_displays if isinstance(own_displays, list)
            and own_displays else None)


def _sample_identity_ok(sample: dict, identity: dict) -> bool:
    """The diagnostic record must come from the BOUND window (R08-03):
    a stale file or another pid/hwnd's observation can never count."""
    hwnd = identity.get("hwnd")
    reported = identity.get("reported_pid")
    owner = identity.get("owning_pid")
    return (sample.get("hwnd") == hwnd
            and sample.get("pid") in (reported, owner))


def _parse_iso(value: object) -> "datetime | None":
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _native_rect_to_logical(native_rect: list[int],
                            displays: list[dict],
                            dpi: int) -> list[float] | None:
    """Map a PHYSICAL native rect to Qt logical global coordinates.

    Windows/Qt global coordinates do NOT share one zero point across
    scales: a screen's logical origin equals its physical origin, and
    only the in-screen offset and size scale by DPR (OVR-02).  The
    mapping anchors on the display whose monitor contains the native
    rect's centre; returns None when no display anchors it.
    """
    dpr = dpi / 96.0
    if dpr <= 0:
        return None
    cx = native_rect[0] + native_rect[2] / 2.0
    cy = native_rect[1] + native_rect[3] / 2.0
    anchor = None
    for display in displays:
        m = display.get("monitor") if isinstance(display, dict) else None
        if (isinstance(m, list) and len(m) == 4
                and m[2] > 0 and m[3] > 0
                and m[0] <= cx <= m[0] + m[2]
                and m[1] <= cy <= m[1] + m[3]):
            anchor = display
            break
    if anchor is None:
        return None
    mx, my = anchor["monitor"][0], anchor["monitor"][1]
    return [mx + (native_rect[0] - mx) / dpr,
            my + (native_rect[1] - my) / dpr,
            native_rect[2] / dpr,
            native_rect[3] / dpr]


def _frames_agree(native_rect: list[int], logical_frame: list[float],
                  displays: list[dict], dpi: int) -> bool:
    """Same-tick association: diagnostic logical frame vs natively
    observed physical rect, mapped with the screen origin preserved."""
    logical_native = _native_rect_to_logical(native_rect, displays, dpi)
    if logical_native is None:
        return False
    return max(abs(a - b) for a, b in
               zip(logical_native, logical_frame)) \
        <= FRAME_ASSOCIATION_TOLERANCE_DIP


def _diagnostic_usable(record: dict, native_rect: list[int] | None,
                       read_at: "datetime",
                       displays: list[dict] | None = None) \
        -> tuple[bool, str]:
    """Same-tick usability of one diagnostic generation (R08-03).

    Fresh (generated_at within the age bound at read time), bound to a
    real layout foot anchor, and its Qt-logical window frame agrees
    with the natively observed rect after the recorded DPI converts
    physical to logical.  Returns (ok, reason).
    """
    generated = _parse_iso(record.get("generated_at"))
    if generated is None:
        return False, "diagnostic lacks a parsable generated_at"
    if generated > read_at:
        return False, "diagnostic generated_at lies in the future"
    age_s = (read_at - generated).total_seconds()
    if age_s > DIAGNOSTIC_MAX_AGE_S:
        return False, f"diagnostic is {age_s:.1f}s stale"
    if not _valid_foot(record.get("foot_point_screen")):
        return False, "diagnostic lacks a screen-space foot anchor"
    dpi = record.get("window_dpi")
    if not isinstance(dpi, int) or not DPI_MIN <= dpi <= DPI_MAX:
        return False, "diagnostic lacks a usable window_dpi"
    frame = record.get("window_frame")
    if (not isinstance(frame, list) or len(frame) != 4
            or not all(isinstance(v, (int, float)) and
                       not isinstance(v, bool) for v in frame)
            or frame[2] <= 0 or frame[3] <= 0):
        return False, "diagnostic lacks a usable window_frame"
    if native_rect is not None and displays:
        # the native rect is PHYSICAL global pixels; the Qt frame is
        # LOGICAL.  The conversion anchors on the screen that contains
        # the rect: screen origin preserved, in-screen offset and size
        # scaled by DPR.  A flat 1/dpr division of absolute coordinates
        # breaks on every non-zero screen origin (OVR-02).  When no
        # displays are supplied (harness sample gate) the association
        # is left to the derivation, which always has them.
        try:
            agree = _frames_agree(native_rect, frame, displays, dpi)
        except Exception:  # noqa: BLE001 - hostile records
            agree = False
        if not agree:
            return False, ("diagnostic window_frame contradicts the "
                           "natively observed window")
    return True, ""


def _foot_within_frame(record: dict) -> bool:
    """The foot anchor must sit inside its own reported window frame -
    a physical/logical unit mix pushes it outside (R08-03 unit check)."""
    frame = record.get("window_frame")
    foot = record.get("foot_point_screen")
    if not isinstance(frame, list) or not _valid_foot(foot):
        return False
    slack = FOOT_TOLERANCE_DIP
    return (frame[0] - slack <= foot[0] <= frame[0] + frame[2] + slack
            and frame[1] - slack <= foot[1] <= frame[1] + frame[3] + slack)


def derive_foot_result(evidence: object,
                       displays: list[dict],
                       identity: object = None,
                       events: object = None) -> tuple[str, str]:
    """Recompute stability from RAW, identity-bound samples (R08-03).

    The foot anchor is the app's real BodyLayout.foot_point mapped to
    global coordinates in Qt LOGICAL pixels (DIP); the tolerance is
    the contract constant in the same unit and a report cannot enlarge
    it.  Every sample and phase record carries its diagnostic's
    generated_at/window_frame/window_dpi plus the native rect read at
    the same tick: the derivation re-checks freshness (bounded age),
    the frame/native association after DPI conversion, the full x/y
    drift within each same-topology group (single-sample groups only
    survive when tied to a logged event), and a strictly increasing
    sequence across samples AND phases.
    """
    if not isinstance(evidence, dict):
        return INVALID, "foot stability evidence missing or malformed"
    samples = evidence.get("samples")
    if not isinstance(samples, list) or not samples:
        return INVALID, "raw no-input samples missing"
    if not isinstance(identity, dict):
        identity = {}
    events_list = events if isinstance(events, list) else []
    for sample in samples:
        if not isinstance(sample, dict) or not _valid_rect(
                sample.get("rect")):
            return INVALID, "a raw sample lacks a valid window rect"
        if not _valid_foot(sample.get("foot_point_screen")):
            return INVALID, ("a raw sample lacks a screen-space "
                             "foot_point observation - the window "
                             "edge is not an equivalent substitute")
        if not isinstance(sample.get("monotonic_ms"), int):
            return INVALID, "a raw sample lacks its timestamp"
        if not isinstance(sample.get("sequence"), int):
            return INVALID, "a raw sample lacks its diagnostic sequence"
        if not _sample_identity_ok(sample, identity):
            return INVALID, ("a diagnostic sample does not belong to the "
                             "bound pid/hwnd (stale or foreign record)")
    sequences = [s["sequence"] for s in samples]
    if any(b <= a for a, b in zip(sequences, sequences[1:])):
        return INVALID, ("diagnostic samples are not fresh - the "
                         "sequence did not advance between reads")
    tolerance = evidence.get("tolerance_px")
    if tolerance != FOOT_TOLERANCE_DIP:
        return INVALID, (f"tolerance {tolerance!r} is not the contract "
                         f"constant {FOOT_TOLERANCE_DIP} (logical px); "
                         "reports cannot enlarge it")
    if evidence.get("no_input_stable") is not True:
        return FAIL, "window rect drifted without input"

    # freshness + same-tick window association, re-checked at the
    # consumer (the harness already filtered, but evidence is evidence)
    for sample in samples:
        read_at = _parse_iso(sample.get("read_at_utc"))
        if read_at is None:
            return INVALID, "a raw sample lacks its read_at_utc"
        usable, reason = _diagnostic_usable(
            {"generated_at": sample.get("generated_at"),
             "foot_point_screen": sample.get("foot_point_screen"),
             "window_dpi": sample.get("window_dpi"),
             "window_frame": sample.get("window_frame")},
            sample.get("native_rect"), read_at,
            sample.get("displays"))
        if not usable:
            return INVALID, f"a raw sample was not usable: {reason}"
        if not _foot_within_frame(sample):
            return INVALID, ("a sample's foot anchor lies outside its "
                             "reported window frame (coordinate unit "
                             "mismatch)")
        if sample.get("window_frame") and _valid_rect(
                sample.get("rect")):
            # OVR-02: use the shared origin-preserving mapping
            own_displays = sample.get("displays") \
                if isinstance(sample.get("displays"), list) else displays
            if not _frames_agree(sample["rect"], sample["window_frame"],
                                 own_displays, sample.get("window_dpi")
                                 or 96):
                return INVALID, ("a sample's native rect contradicts its "
                                 "diagnostic window frame")

    # groups are formed from RECOMPUTED fingerprints: each sample stores
    # its display records, so a forged fingerprint that does not match
    # its own records invalidates the whole run (R08-03 probe 3)
    groups: dict[str, list[dict]] = {}
    for sample in samples:
        records = sample.get("displays")
        if not isinstance(records, list) or not records:
            return INVALID, "a raw sample lacks its display records"
        try:
            recomputed = topology_of(records)["fingerprint"]
        except Exception:  # noqa: BLE001 - hostile records
            return INVALID, ("a raw sample's display records are not "
                             "evaluable")
        if sample.get("topology_fingerprint") != recomputed:
            return INVALID, ("a sample's topology fingerprint does not "
                             "match its own display records")
        groups.setdefault(recomputed, []).append(sample)

    event_after_fps = set()
    for event in events_list:
        if isinstance(event, dict):
            after = event.get("after")
            if isinstance(after, dict):
                fp = after.get("fingerprint")
                if isinstance(fp, str):
                    event_after_fps.add(fp)
    unverifiable = [fp for fp, group in groups.items()
                    if len(group) < 2 and fp not in event_after_fps]
    if unverifiable:
        return INVALID, ("sample groups without two samples cannot be "
                         "tied to a logged topology event: "
                         f"{len(unverifiable)} group(s)")
    for _fp, group in groups.items():
        if len(group) < 2:
            continue
        if any(s["rect"] != group[0]["rect"] for s in group):
            return FAIL, "raw samples show the window moved without input"
        if any(s["foot_point_screen"] != group[0]["foot_point_screen"]
               for s in group):
            return FAIL, ("raw samples show the layout foot anchor moved "
                          "while the window stood still")
    if evidence.get("panel_observed_open") is not True \
            or evidence.get("panel_observed_closed") is not True:
        return INVALID, ("panel open/close was not really observed "
                         "(IPC success alone is not evidence)")
    positions = evidence.get("positions")
    if not isinstance(positions, dict):
        return INVALID, "phase positions missing"

    # phase records carry the same binding; their sequences must
    # strictly follow the samples AND each other (replay of one
    # generation across phases is a stale recording, not three
    # observations)
    ordered: list[dict] = list(samples)
    for phase in ("before", "panel_open", "panel_closed"):
        record = positions.get(phase)
        if not isinstance(record, dict):
            return INVALID, f"phase {phase} observation missing"
        rect = record.get("rect")
        if not _valid_rect(rect):
            return INVALID, f"phase {phase} rect missing"
        if not _valid_foot(record.get("foot_point_screen")):
            return INVALID, (f"phase {phase} lacks a screen-space foot "
                             "observation")
        if not isinstance(record.get("sequence"), int):
            return INVALID, f"phase {phase} lacks its diagnostic sequence"
        if not _sample_identity_ok(record, identity):
            return INVALID, (f"phase {phase} diagnostic does not belong "
                             "to the bound pid/hwnd")
        read_at = _parse_iso(record.get("read_at_utc"))
        if read_at is None:
            return INVALID, f"phase {phase} lacks its read_at_utc"
        usable, reason = _diagnostic_usable(
            record, record.get("native_rect") or rect, read_at,
            record.get("displays") if isinstance(
                record.get("displays"), list) else None)
        if not usable:
            return INVALID, (f"phase {phase} diagnostic was not usable: "
                             f"{reason}")
        if not _foot_within_frame(record):
            return INVALID, (f"phase {phase} foot anchor lies outside "
                             "its window frame (coordinate unit mismatch)")
        ordered.append(record)
    phase_sequences = [record["sequence"] for record in ordered]
    if any(b <= a for a, b in zip(phase_sequences, phase_sequences[1:])):
        return INVALID, ("diagnostic sequences are not strictly "
                         "increasing across samples and phases")
    for phase in ("before", "panel_open", "panel_closed"):
        rect = positions[phase]["rect"]
        own_displays = positions[phase].get("displays")
        if work_area_containing(rect, (own_displays if isinstance(
                own_displays, list) and own_displays else displays)) \
                is None:
            return FAIL, f"window not contained during {phase}"
    rect_before = positions["before"]["rect"]
    foot_before = positions["before"]["foot_point_screen"]
    foot_closed = positions["panel_closed"]["foot_point_screen"]
    delta_x = abs(foot_closed[0] - foot_before[0])
    delta_y = abs(foot_closed[1] - foot_before[1])
    delta = max(delta_x, delta_y)
    if delta > FOOT_TOLERANCE_DIP:
        return FAIL, (f"foot anchor moved {delta:.1f} DIP (dx={delta_x:.1f}, "
                      f"dy={delta_y:.1f}) after the panel phase "
                      f"(tolerance {FOOT_TOLERANCE_DIP} logical px)")
    # OVR-02: the phase rects are PHYSICAL native pixels; convert the
    # delta to DIP with the phase's recorded window_dpi before comparing
    window_dpi_closed = positions["panel_closed"].get("window_dpi") \
        if isinstance(positions["panel_closed"], dict) else None
    if not isinstance(window_dpi_closed, int) or window_dpi_closed <= 0:
        window_dpi_closed = 96
    window_delta = round(_rect_delta(rect_before,
                                     positions["panel_closed"]["rect"])
                         * 96.0 / window_dpi_closed)
    if window_delta > FOOT_TOLERANCE_DIP:
        return FAIL, (f"window rect moved {window_delta} DIP across the "
                      f"panel phase (tolerance {FOOT_TOLERANCE_DIP})")
    return PASS, (f"{len(samples)} identity-bound raw samples stable "
                  f"without input (real layout foot anchor, logical px); "
                  f"panel observed open and closed; foot delta "
                  f"{delta:.1f} DIP <= {FOOT_TOLERANCE_DIP}")



def derive_per_display_result(evidence: object,
                              displays: list[dict]) -> tuple[str, str]:
    """Every display must show a REAL landing on THAT display (R08-02)."""
    if not isinstance(evidence, dict):
        return INVALID, "per-display evidence missing or malformed"
    visited = evidence.get("visited")
    if not isinstance(visited, list):
        return INVALID, "per-display visit records missing"
    devices = [d["device"] for d in displays]
    visited_devices = [v.get("device") for v in visited
                       if isinstance(v, dict)]
    missing = [d for d in devices if d not in visited_devices]
    if missing:
        return FAIL, f"displays never visited: {missing}"
    bad = []
    for visit in visited:
        if not isinstance(visit, dict):
            bad.append("?")
            continue
        record = next((d for d in displays
                       if d.get("device") == visit.get("device")), None)
        rect = visit.get("rect")
        container = work_area_containing(rect, displays) \
            if _valid_rect(rect) else None
        landed_on_target = (container is not None
                            and record is not None
                            and container.get("device") == record["device"])
        dpi_ok = (isinstance(visit.get("dpi_observed"), int)
                  and record is not None
                  and visit["dpi_observed"] == record["dpi"])
        if (record is None
                or not landed_on_target
                or visit.get("moved") is not True
                or not dpi_ok):
            why = []
            if record is None or not landed_on_target:
                why.append("rect did not land on the target display")
            if visit.get("moved") is not True:
                why.append("move did not take effect")
            if not dpi_ok:
                why.append("per-window DPI after the move was not observed "
                           "at the target value")
            bad.append(f"{visit.get('device')}: {'; '.join(why)}")
    if bad:
        return FAIL, "; ".join(bad)
    return PASS, (f"all {len(displays)} display(s) visited: landed on the "
                  f"target screen with the target per-window DPI")


def derive_topology_result(evidence: object,
                           before_topology: object,
                           after_topology: object,
                           events: object = None) -> tuple[str, str]:
    """Recovery must be recomputed from recorded coordinates and be tied
    to a real, logged event - not from a boolean flag (R08-04)."""
    if not isinstance(evidence, dict):
        return INVALID, "topology recovery evidence missing or malformed"
    if evidence.get("event_detected") is not True:
        if evidence.get("reason") != "no_topology_event":
            return INVALID, "SKIP without the no-event reason"
        if events not in (None, []) and events:
            return INVALID, ("recovery skipped but the event log is not "
                             "empty")
        return SKIP, "no topology change observed; a real event is required"
    before = evidence.get("before_fingerprint")
    after = evidence.get("after_fingerprint")
    if (not isinstance(before, str) or not isinstance(after, str)
            or not before or not after):
        return INVALID, "before/after fingerprints missing"
    if not isinstance(before_topology, dict) \
            or not isinstance(after_topology, dict) \
            or before_topology.get("fingerprint") != before \
            or after_topology.get("fingerprint") != after:
        return INVALID, ("fingerprints do not match the recorded "
                         "before/after topologies")
    if before == after:
        return INVALID, "recovery claimed without a topology difference"
    if not isinstance(events, list) or not events:
        return INVALID, "recovery claimed without a logged topology event"
    matching = [e for e in events if isinstance(e, dict)
                and e.get("before", {}).get("fingerprint") == before
                and isinstance(e.get("detected_at"), str)]
    if not matching:
        return INVALID, ("the recovery's before-fingerprint matches no "
                         "logged event")
    after_rect = evidence.get("after_rect")
    if not _valid_rect(after_rect):
        return INVALID, "post-recovery window coordinates missing"
    # containment is recomputed against the evidence's OWN post-event
    # display records, or the recorded after-topology - never a mixture
    after_displays = evidence.get("after_displays")
    if not (isinstance(after_displays, list) and after_displays):
        after_displays = (after_topology.get("displays")
                          if isinstance(after_topology.get("displays"),
                                        list) else [])
    if not after_displays:
        return INVALID, "post-recovery display records missing"
    container = work_area_containing(after_rect, after_displays)
    if container is None:
        return FAIL, (f"window {after_rect} is not contained after the "
                      "topology change")
    return PASS, (f"window re-contained in {container['device']} after a "
                  "real, logged topology change")


def derive_all_results(evidence: dict, before_topology: object,
                       after_topology: object,
                       events: object = None,
                       candidate: dict | None = None) -> dict[str, dict]:
    """The single source of scenario truth: harness labels and the
    consumer's recomputation both come from here.  Any malformed input
    yields an INVALID scenario, never an exception (R08-05)."""
    facts = evidence.get("display_facts") \
        if isinstance(evidence.get("display_facts"), dict) else {}
    displays = facts.get("displays") if isinstance(facts, dict) else []
    if not isinstance(displays, list):
        displays = []
    events_list = events if isinstance(events, list) else []
    results: dict[str, dict] = {}

    def run(name, fn, *args):
        try:
            result, detail = fn(*args)
        except Exception as exc:  # noqa: BLE001 - malformed input is a
            # contract violation, never a crash (R08-05)
            result, detail = INVALID, (
                f"evidence evaluation failed: {type(exc).__name__}")
        results[name] = {"result": result, "detail": detail}

    run("identity_binding", derive_identity_result,
        evidence.get("identity_binding"), candidate)
    run("display_facts", derive_display_facts_result,
        evidence.get("display_facts"))
    run("window_containment", derive_containment_result,
        evidence.get("window_containment"), displays)
    run("foot_stability", derive_foot_result,
        evidence.get("foot_stability"), displays,
        evidence.get("identity_binding"), events_list)
    run("per_display_landing", derive_per_display_result,
        evidence.get("per_display_landing"), displays)
    run("topology_recovery", derive_topology_result,
        evidence.get("topology_recovery"), before_topology,
        after_topology, events_list)
    return results


def aggregate_display_verdicts(topology: dict,
                               results: dict[str, str]) -> dict[str, dict]:
    """Fail-closed verdicts over DERIVED scenario results.

    - an INVALID in a baseline component invalidates both verdicts
      (it is never downgraded to SKIP);
    - single_screen_baseline = identity + facts + containment all PASS;
    - multi_screen_certification additionally needs a real multi-screen
      mixed-DPI negative-coordinate topology, panel stability, ALL
      displays visited with verified containment/DPI, and a real
      recorded topology recovery.
    """
    def result_of(name: str) -> str | None:
        value = results.get(name)
        return value if value in RESULT_VALUES else None

    invalid_reasons = []
    for name in REQUIRED_SCENARIOS:
        if result_of(name) is None:
            invalid_reasons.append(f"scenario {name} missing/unknown")
    if result_of("identity_binding") == SKIP:
        invalid_reasons.append("identity_binding cannot be skipped")

    base_components = ("identity_binding", "display_facts",
                       "window_containment")
    if invalid_reasons:
        single_result = INVALID
        single_detail = "; ".join(invalid_reasons)
    elif any(result_of(name) in (FAIL, INVALID)
             for name in base_components):
        problems = [f"{n}:{result_of(n)}" for n in base_components
                    if result_of(n) in (FAIL, INVALID)]
        single_result = INVALID if any(
            result_of(n) == INVALID for n in problems[:0]) else FAIL
        if any(result_of(n) == INVALID for n in base_components):
            single_result = INVALID
        single_detail = f"failed components: {', '.join(problems)}"
    elif all(result_of(name) == PASS for name in base_components):
        single_result = PASS
        single_detail = "identity, display facts and containment verified"
    else:
        single_result = SKIP
        single_detail = "baseline components were not fully observed"

    multi_result = INVALID
    multi_detail = ""
    if single_result == INVALID:
        multi_detail = "baseline could not be verified"
    elif single_result == FAIL:
        multi_result = FAIL
        multi_detail = "single-screen baseline failed"
    elif single_result == SKIP:
        multi_result = SKIP
        multi_detail = "baseline was not established"
    else:
        followups = ("foot_stability", "per_display_landing",
                     "topology_recovery")
        if any(result_of(name) == INVALID for name in followups):
            multi_result = INVALID
            multi_detail = "multi-screen scenario observation is invalid"
        elif any(result_of(name) == FAIL for name in followups):
            failed = [n for n in followups if result_of(n) == FAIL]
            multi_result = FAIL
            multi_detail = f"failed: {', '.join(failed)}"
        elif not topology.get("multi_screen"):
            multi_result = SKIP
            multi_detail = "topology provides no second display"
        elif not topology.get("mixed_dpi"):
            multi_result = SKIP
            multi_detail = "topology provides no mixed-DPI pair"
        elif not topology.get("negative_coordinates"):
            multi_result = SKIP
            multi_detail = "topology provides no negative-coordinate display"
        elif result_of("foot_stability") != PASS:
            multi_result = SKIP
            multi_detail = "panel-toggle stability was not verified"
        elif result_of("per_display_landing") != PASS:
            multi_result = SKIP
            multi_detail = "per-display containment was not fully verified"
        elif result_of("topology_recovery") != PASS:
            multi_result = SKIP
            multi_detail = ("no real topology change was observed during "
                            "the run; unplug/replug or DPI change is "
                            "required")
        else:
            multi_result = PASS
            multi_detail = ("mixed-DPI multi-screen containment, per-display "
                            "landings, panel stability and a real topology "
                            "recovery all verified")
    return {
        "single_screen_baseline": {"result": single_result,
                                   "detail": single_detail},
        "multi_screen_certification": {"result": multi_result,
                                       "detail": multi_detail},
    }


def validate_display_report(report: object) -> list[str]:
    """Consumer-side contract check: evidence is re-derived, not trusted.

    Any report object produces a diagnostic error list - malformed
    nested structures NEVER raise (R08-05)."""
    try:
        return _validate_display_report_impl(report)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash
        return [f"report evaluation failed: {type(exc).__name__}"]


def _validate_display_report_impl(report: object) -> list[str]:
    if not isinstance(report, dict):
        return ["display report is not an object"]
    errors: list[str] = []
    if report.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    candidate = report.get("candidate")
    if (not isinstance(candidate, dict)
            or not str(candidate.get("kind") or "").strip()
            or not str(candidate.get("identity") or "").strip()):
        errors.append("candidate binding missing kind or identity")
    elif (candidate.get("kind") == "exe"
            and not (isinstance(candidate.get("exe_sha256"), str)
                     and len(candidate["exe_sha256"]) == 64)):
        errors.append("exe candidate lacks the observed image sha256")
    evidence = report.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("scenario evidence missing")
        evidence = {}
    for name in REQUIRED_SCENARIOS:
        if name not in evidence:
            errors.append(f"evidence for {name} missing")
    facts = evidence.get("display_facts")
    displays = facts.get("displays") if isinstance(facts, dict) else None
    if not isinstance(displays, list) or not displays:
        errors.append("display records missing")
        displays = []
    before = report.get("topology_before")
    after = report.get("topology_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        errors.append("before/after topology records missing")
        before = after = {}
    events = report.get("topology_events")
    if events is not None and not isinstance(events, list):
        errors.append("topology_events must be a list")

    def _check_topology_snapshot(name: str, snapshot: object) -> None:
        """Validate nested display records BEFORE any fingerprint math
        (R08-05: a null/None entry used to raise AttributeError)."""
        if not isinstance(snapshot, dict) or not snapshot:
            return
        records = snapshot.get("displays")
        if not isinstance(records, list) or not records:
            errors.append(f"{name}-topology lacks its display records")
            return
        for entry in records:
            try:
                ok = _valid_display(entry)
            except Exception:  # noqa: BLE001 - hostile record
                ok = False
            if not ok:
                errors.append(
                    f"{name}-topology contains malformed display records")
                return
        try:
            recomputed = topology_of(records)
        except Exception:  # noqa: BLE001
            errors.append(f"{name}-topology records are not evaluable")
            return
        if recomputed["fingerprint"] != snapshot.get("fingerprint"):
            errors.append(f"{name}-topology fingerprint does not match "
                          "its records")

    _check_topology_snapshot("before", before)
    _check_topology_snapshot("after", after)

    if errors:
        return errors

    labeled = report.get("scenarios")
    if not isinstance(labeled, dict):
        errors.append("scenario labels missing")
        labeled = {}
    # R08-05: derivation must never raise on a hostile report
    try:
        derived = derive_all_results(evidence, before, after,
                                     report.get("topology_events"),
                                     candidate)
    except Exception as exc:  # noqa: BLE001
        return [f"evidence derivation raised {type(exc).__name__}"]
    for name in REQUIRED_SCENARIOS:
        entry = labeled.get(name)
        label = entry.get("result") if isinstance(entry, dict) else None
        if label not in RESULT_VALUES:
            errors.append(f"scenario {name} label missing or malformed")
            continue
        if label != derived[name]["result"]:
            errors.append(
                f"scenario {name} labeled {label} but the evidence "
                f"derives {derived[name]['result']}")
        # the scenario entry must reference the SAME evidence the
        # derivation used - a duplicated, mutated copy would otherwise
        # hide what the label was actually computed from
        entry_evidence = entry.get("evidence") if isinstance(entry, dict) \
            else None
        if entry_evidence != evidence.get(name):
            errors.append(
                f"scenario {name} evidence does not match the report's "
                "evidence record")
    # R08-01: the candidate hash must be the OBSERVED image hash
    identity = evidence.get("identity_binding")
    if isinstance(identity, dict) and isinstance(candidate, dict):
        observed = identity.get("image_sha256")
        claimed = candidate.get("exe_sha256")
        if isinstance(observed, str) and len(observed) == 64 \
                and isinstance(claimed, str) and len(claimed) == 64 \
                and observed.lower() != claimed.lower():
            errors.append("candidate hash differs from the observed "
                          "process image")
    for name in ("single_screen_baseline", "multi_screen_certification"):
        verdict = report.get(name)
        if not isinstance(verdict, dict) \
                or verdict.get("result") not in RESULT_VALUES:
            errors.append(f"verdict {name} missing or malformed")
    # verdict comparison runs REGARDLESS of label errors: a report whose
    # verdicts contradict the aggregation rules must be caught even when
    # its scenario labels are also wrong
    recomputed = aggregate_display_verdicts(
        after if isinstance(after, dict) and after else before,
        {name: derived[name]["result"] for name in REQUIRED_SCENARIOS})
    for name, verdict in recomputed.items():
        observed = report.get(name)
        observed_result = (observed.get("result")
                           if isinstance(observed, dict) else None)
        if observed_result != verdict["result"]:
            errors.append(
                f"verdict {name} ({observed_result}) does not "
                f"follow the aggregation rules ({verdict['result']})")
    return errors


def _scenario(result: str, detail: str, evidence: object) -> dict:
    if result not in RESULT_VALUES:
        raise ValueError(f"invalid scenario result: {result}")
    return {"result": result, "detail": detail, "evidence": evidence}


def run_harness(args, *, target: Target | None = None,
                observers: Observers | None = None) -> dict:
    """Orchestrates one real (or injected) verification run."""
    observers = observers or Observers()
    started = datetime.now(timezone.utc)
    evidence: dict[str, object] = {}
    scenarios: dict[str, dict] = {}
    topology_events: list[dict] = []

    data_dir = Path(tempfile.mkdtemp(prefix="verify-displays-data-"))
    own_target = target is None
    target = target or Target(
        "exe" if args.exe else "source",
        artifact_dir=args.artifact_dir,
        expected_build_id=args.expected_build_id,
        expected_exe_sha256=args.expected_exe_sha256,
    )
    instance = f"verify-displays-{uuid4().hex[:8]}"
    report_file = data_dir / PET_WINDOW_REPORT
    proc = target.spawn(["--report-window", str(report_file)], data_dir,
                        instance, "display verification")
    candidate: dict[str, object] = {"kind": target.kind,
                                    "identity": f"launched_pid={proc.pid}"}
    try:
        _run_scenarios(args, target, observers, proc, instance,
                       report_file, candidate, evidence, topology_events)
    finally:
        if own_target:
            try:
                proc.terminate()
                wait_exited(proc, timeout_s=10.0)
            except Exception:  # noqa: BLE001 - teardown is best effort
                pass

    before_topology = topology_events[0]["before"] if topology_events \
        else _topology_snapshot(evidence)
    after_topology = topology_events[-1]["after"] if topology_events \
        else _topology_snapshot(evidence)
    results = derive_all_results(evidence, before_topology, after_topology,
                                 topology_events, candidate)
    scenarios = {
        name: {"result": derived["result"], "detail": derived["detail"],
               "evidence": evidence.get(name)}
        for name, derived in results.items()
    }
    verdicts = aggregate_display_verdicts(
        after_topology,
        {name: derived["result"] for name, derived in results.items()})
    return {
        "schema": SCHEMA,
        "generated_at": started.isoformat(timespec="seconds"),
        "completed_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "target": target.kind,
        "candidate": candidate,
        "topology_before": before_topology,
        "topology_after": after_topology,
        "topology_events": topology_events,
        "scenarios": scenarios,
        "evidence": evidence,
        **verdicts,
    }


def _topology_snapshot(evidence: dict) -> dict:
    facts = evidence.get("display_facts")
    displays = facts.get("displays") if isinstance(facts, dict) else []
    if not isinstance(displays, list):
        displays = []
    return topology_of(displays)


def _run_scenarios(args, target: Target, observers: Observers, proc,
                   instance: str, report_file: Path,
                   candidate: dict, evidence: dict,
                   topology_events: list[dict]) -> None:
    observed = find_pet_hwnd(report_file, timeout_s=45.0)
    hwnd = int(observed["hwnd"]) if observed else None
    if hwnd is None or not observers.window_alive(hwnd):
        evidence["identity_binding"] = {
            "reason": "no live window reported by the target"}
        return
    reported_pid = int(observed.get("pid") or 0)
    owner_pid = observers.window_owner_pid(hwnd)
    ownership = "direct"
    host_parent = None
    if owner_pid != proc.pid:
        host_parent = observers.process_parent_pid(owner_pid)
        ownership = "hosted" if host_parent == proc.pid else "unrelated"
    image = observers.process_image(owner_pid)
    image_sha = (image or {}).get("sha256")
    identity_evidence = {
        "hwnd": hwnd,
        "reported_pid": reported_pid,
        "launched_pid": proc.pid,
        "owning_pid": owner_pid,
        "ownership": ownership,
        "ownership_verified": True,
        "host_parent_pid": host_parent,
        "image_path": (image or {}).get("path"),
        # R08-01: the OBSERVED image hash always travels in the identity
        # evidence, and the candidate record carries the SAME value - a
        # report whose candidate hash was never observed cannot validate
        "image_sha256": image_sha,
    }
    evidence["identity_binding"] = identity_evidence
    if isinstance(image_sha, str) and len(image_sha) == 64:
        candidate["exe_sha256"] = image_sha
    if owner_pid != reported_pid:
        # keep going: later scenarios still observe what they can, but
        # the identity derivation will FAIL this run
        candidate["identity"] += f";owner_mismatch={owner_pid}"

    displays = observers.enumerate_displays()
    evidence["display_facts"] = {"displays": displays}
    topology_before = topology_of(displays)

    rect = (observers.window_rect(hwnd)
            if observers.window_alive(hwnd) else None)
    # every observation binds the topology AT ITS SAMPLE TIME (R08-04):
    # a rect sampled on the initial screens is never judged against a
    # later topology
    evidence["window_containment"] = {
        "rect": rect,
        "displays": displays,
        "topology_fingerprint": topology_before["fingerprint"],
    }

    # -- observation phase (R08-04): raw samples + topology event watch
    # + recovery containment, ALL before any manual window move.  The
    # recovery verdict must reflect what the APP did, not what the
    # harness could have rescued by carrying the window around.
    # R08-03: only FRESH diagnostics bound to THIS pid/hwnd are counted
    # - a stale or foreign record is waited out, never re-counted.
    samples = []
    stale_reads = 0
    stale_reasons = []
    last_sequence = None
    stable = rect is not None
    deadline = time.monotonic() + args.observe_seconds
    event_detected = False
    started_ms = time.monotonic()
    while time.monotonic() < deadline:
        observers.sleep(0.4)
        observation = observers.read_observation(report_file) or {}
        read_at = datetime.now(timezone.utc)
        current = (observers.window_rect(hwnd)
                   if observers.window_alive(hwnd) else None)
        current_displays = observers.enumerate_displays()
        current_topology = topology_of(current_displays)
        # topology events are detected FIRST: they must be observed
        # even when the diagnostic of the moment is stale or unusable
        # (OVR-02 regression - a skipped diagnostic used to suppress
        # the event too)
        sequence = observation.get("sequence")
        fresh = (isinstance(sequence, int)
                 and (last_sequence is None or sequence > last_sequence)
                 and observation.get("pid") == proc.pid
                 and observation.get("hwnd") == hwnd
                 and _valid_foot(observation.get("foot_point_screen")))
        if fresh:
            # same-tick usability: bounded generation age; the frame/
            # native association is re-derived per recorded sample in
            # the consumer, not gated here
            usable, reason = _diagnostic_usable(observation, current,
                                                read_at)
            if not usable:
                fresh = False
                stale_reasons.append(reason)
        if not fresh:
            stale_reads += 1
            continue
        last_sequence = sequence
        samples.append({
            "monotonic_ms": int((time.monotonic() - started_ms) * 1000),
            "rect": current,
            "foot_point_screen": observation.get("foot_point_screen"),
            "pid": observation.get("pid"),
            "hwnd": observation.get("hwnd"),
            "sequence": sequence,
            "generated_at": observation.get("generated_at"),
            "window_frame": observation.get("window_frame"),
            "window_dpi": observation.get("window_dpi"),
            "read_at_utc": read_at.isoformat(timespec="milliseconds"),
            "native_rect": current,
            "displays": current_displays,
            "topology_fingerprint": current_topology["fingerprint"],
        })
        if current_topology["fingerprint"] \
                != topology_before["fingerprint"]:
            event_detected = True
            topology_events.append({
                "detected_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "before": topology_of(displays),
                "after": current_topology,
            })
            displays = current_topology["displays"]
            evidence["display_facts"] = {"displays": displays}
            break
    if samples:
        first = samples[0]
        stable = (rect is not None
                  and all(s["rect"] == first["rect"] for s in samples
                          if s.get("topology_fingerprint")
                          == first.get("topology_fingerprint")))

    if event_detected:
        # let the app settle on its own; NO manual move in this phase
        observers.sleep(3.0)
        after_displays = observers.enumerate_displays()
        after_topology = topology_of(after_displays)
        rect_after = (observers.window_rect(hwnd)
                      if observers.window_alive(hwnd) else None)
        evidence["topology_recovery"] = {
            "event_detected": True,
            "before_fingerprint": topology_events[0]["before"]["fingerprint"],
            "after_fingerprint": after_topology["fingerprint"],
            "after_displays": after_displays,
            # raw coordinates: the consumer recomputes containment
            "after_rect": rect_after,
        }
        topology_events[-1]["after"] = after_topology
    else:
        evidence["topology_recovery"] = {
            "event_detected": False,
            "reason": "no_topology_event",
        }

    # -- panel phase: re-baselined AFTER a recovered event (the panel
    # comparison must use the post-recovery position), with REAL
    # open/close observation, per-phase topology binding and the same
    # freshness/identity rules as the sample loop
    panel_open_seen = False
    panel_closed_seen = False
    panel_topology = topology_of(displays)

    def _fresh_observation():
        """Wait (bounded) for a NEW, usable diagnostic for this target."""
        nonlocal last_sequence
        for _attempt in range(10):
            observation = observers.read_observation(report_file) or {}
            read_at = datetime.now(timezone.utc)
            sequence = observation.get("sequence")
            if (isinstance(sequence, int)
                    and (last_sequence is None or sequence > last_sequence)
                    and observation.get("pid") == proc.pid
                    and observation.get("hwnd") == hwnd
                    and _valid_foot(observation.get("foot_point_screen"))):
                native = (observers.window_rect(hwnd)
                          if observers.window_alive(hwnd) else None)
                usable, _reason = _diagnostic_usable(
                    observation, native, read_at, displays)
                if not usable:
                    observers.sleep(0.2)
                    continue
                last_sequence = sequence
                observation = dict(observation)
                observation["_read_at_utc"] = read_at.isoformat(
                    timespec="milliseconds")
                return observation
            observers.sleep(0.2)
        return None

    def _phase_record():
        observation = _fresh_observation()
        if observation is None:
            return {"stale": True}
        native = (observers.window_rect(hwnd)
                  if observers.window_alive(hwnd) else None)
        return {"rect": native,
                "foot_point_screen": observation.get("foot_point_screen"),
                "pid": observation.get("pid"),
                "hwnd": observation.get("hwnd"),
                "sequence": observation.get("sequence"),
                "generated_at": observation.get("generated_at"),
                "window_frame": observation.get("window_frame"),
                "window_dpi": observation.get("window_dpi"),
                "read_at_utc": observation.get("_read_at_utc"),
                "native_rect": native,
                "displays": displays,
                "topology_fingerprint": panel_topology["fingerprint"]}

    baseline_observation = _fresh_observation() or {}
    baseline_native = (observers.window_rect(hwnd)
                       if observers.window_alive(hwnd) else rect)
    position_before = {
        "rect": baseline_native,
        "foot_point_screen": baseline_observation.get("foot_point_screen"),
        "pid": baseline_observation.get("pid"),
        "hwnd": baseline_observation.get("hwnd"),
        "sequence": baseline_observation.get("sequence"),
        "generated_at": baseline_observation.get("generated_at"),
        "window_frame": baseline_observation.get("window_frame"),
        "window_dpi": baseline_observation.get("window_dpi"),
        "read_at_utc": baseline_observation.get("_read_at_utc"),
        "native_rect": baseline_native,
        "displays": displays,
        "topology_fingerprint": panel_topology["fingerprint"]}
    position_panel = None
    position_closed = None
    if stable and observers.window_alive(hwnd):
        if target.ipc("panel", instance):
            observers.sleep(2.0)
            panel_open_seen = observers.panel_visible(proc.pid)
            position_panel = _phase_record()
        if target.ipc("panel-close", instance):
            observers.sleep(2.0)
            panel_closed_seen = not observers.panel_visible(proc.pid)
            position_closed = _phase_record()
    evidence["foot_stability"] = {
        # raw, re-derivable observation records (R08-03): identity-bound,
        # freshness-checked (bounded generation age), same-tick window
        # association, real layout foot anchor in logical px
        "samples": samples,
        "stale_diagnostic_reads": stale_reads,
        "stale_reasons": stale_reasons[:10],
        "no_input_stable": stable,
        "panel_observed_open": panel_open_seen,
        "panel_observed_closed": panel_closed_seen,
        "positions": {"before": position_before,
                      "panel_open": position_panel,
                      "panel_closed": position_closed},
        # the tolerance is a contract constant in Qt logical px; a
        # report cannot enlarge it
        "tolerance_px": FOOT_TOLERANCE_PX,
    }

    # -- per-display phase (R08-02): real landings on SPECIFIC screens,
    # with the per-window DPI measured AFTER each move
    visited = []
    for display in displays:
        work = display["work"]
        target_rect = [work[0] + 8, work[1] + 8,
                       min(rect[2] if rect else 200, work[2] - 16),
                       min(rect[3] if rect else 160, work[3] - 16)]
        moved = (observers.window_alive(hwnd)
                 and observers.move_window(hwnd, target_rect))
        observers.sleep(0.5)
        actual = (observers.window_rect(hwnd)
                  if observers.window_alive(hwnd) else None)
        container = work_area_containing(actual or [], displays)
        dpi_observed = (observers.window_dpi(hwnd)
                        if observers.window_alive(hwnd) else None)
        visited.append({
            "device": display["device"],
            "target_rect": target_rect,
            "rect": actual,
            "moved": moved,
            "contained_device": container["device"] if container else None,
            "dpi_observed": dpi_observed,
        })
    evidence["per_display_landing"] = {"visited": visited}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exe", action="store_true")
    group.add_argument("--source", action="store_true")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--expected-build-id")
    parser.add_argument("--expected-exe-sha256")
    parser.add_argument("--evidence-root", type=Path,
                        default=ROOT / "evidence")
    parser.add_argument("--observe-seconds", type=float, default=6.0)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.evidence_root / f"{stamp}-displays-{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)

    report = run_harness(args)
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    errors = validate_display_report(report)
    for name, entry in report["scenarios"].items():
        print(f"  [{entry['result']}] {name}: {entry['detail']}")
    for name in ("single_screen_baseline", "multi_screen_certification"):
        verdict = report[name]
        print(f"  [{verdict['result']}] {name}: {verdict['detail']}")
    print(f"evidence: {run_dir}")
    if errors:
        print(f"  [INVALID] report contract: {'; '.join(errors[:5])}")
        return 1
    results = [entry["result"] for entry in report["scenarios"].values()]
    if FAIL in results or INVALID in results:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
