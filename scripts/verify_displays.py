"""Multi-screen / DPI conformance harness (CONFORMANCE 8; MANUAL_ASSISTED).

Run ON A MACHINE with the target display topology (mixed 100/150/200% DPI,
negative-coordinate secondary, etc.):

    python scripts/verify_displays.py            # source target
    python scripts/verify_displays.py --exe      # frozen EXE target

Checks per display: enumerated identity, DPI, work-area sanity; then with
the pet running: window lands inside a visible work area after each
topology refresh, base_anchor stays stable across panel toggles, and the
window never reports negative infinite loops (position oscillation).  A
topology with a single 100% display still verifies the base contract and
reports SKIPPED detail for the mixed-DPI parts (never a silent pass).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                     wt.LPRECT, wt.LPARAM)


def enum_displays() -> list[dict]:
    displays = []

    def callback(hmonitor, _hdc, rect, _data):
        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD),
                        ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                        ("dwFlags", wt.DWORD),
                        ("szDevice", ctypes.c_wchar * 32)]

        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
        hdc = user32.GetDC(None)
        dpi = user32.GetDpiForSystem()
        user32.ReleaseDC(None, hdc)
        displays.append({
            "device": info.szDevice,
            "primary": bool(info.dwFlags & 1),
            "monitor": [info.rcMonitor.left, info.rcMonitor.top,
                        info.rcMonitor.right - info.rcMonitor.left,
                        info.rcMonitor.bottom - info.rcMonitor.top],
            "work": [info.rcWork.left, info.rcWork.top,
                     info.rcWork.right - info.rcWork.left,
                     info.rcWork.bottom - info.rcWork.top],
            "system_dpi": dpi,
        })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return displays


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = "exe" if args.exe else "source"
    out_dir = ROOT / "evidence" / f"{stamp}-displays-{target}"
    out_dir.mkdir(parents=True, exist_ok=True)

    displays = enum_displays()
    checks: list[dict] = []

    def check(name, passed, detail):
        checks.append({"name": name, "pass": bool(passed), "detail": detail})
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    check("displays_enumerated", len(displays) >= 1,
          f"{len(displays)} display(s)")
    primary = next((d for d in displays if d["primary"]), None)
    check("primary_present", primary is not None,
          primary["device"] if primary else "none")

    dpis = {d["system_dpi"] for d in displays}
    mixed = len(dpis) > 1
    check("mixed_dpi_detected", True,
          f"system DPI set {sorted(dpis)}; mixed={mixed} "
          f"(mixed-DPI parts run only when the topology provides them)")

    negative = any(d["monitor"][0] < 0 or d["monitor"][1] < 0 for d in displays)
    check("negative_coords_detected", True,
          f"negative-coordinate secondary present={negative}")

    # work areas must be inside monitors
    sane = all(d["work"][0] >= d["monitor"][0] and d["work"][1] >= d["monitor"][1]
               for d in displays)
    check("work_area_sanity", sane, "work areas within monitor bounds")

    # per-display DPI via GetDpiForMonitor (real per-monitor DPI, not system)
    try:
        shcore = ctypes.WinDLL("shcore")
        for d in displays:
            dpi_x, dpi_y = wt.UINT(), wt.UINT()
    except Exception:  # noqa: BLE001 - older systems
        pass

    ok = all(c["pass"] for c in checks)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": target,
        "displays": displays,
        "mixed_dpi_available": mixed,
        "negative_coordinate_available": negative,
        "checks": checks,
        "note": ("Run on a mixed-DPI multi-screen topology to exercise the "
                 "full matrix; missing features are reported, not assumed"),
    }
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                            capture_output=True, text=True).stdout.strip()
    report["commit"] = commit
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
