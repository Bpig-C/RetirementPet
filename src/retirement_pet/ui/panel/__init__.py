"""Multi-page control panel (M6; DESIGN_V2 11.3).

A standard Qt window (NOT the transparent pet surface).  One page is
instantiated/shown at a time; pages are lazy factories so opening the
panel never builds every page.  No timers: pages refresh on show.
"""

from retirement_pet.ui.panel.control_panel import ControlPanel, PAGE_IDS

__all__ = ["ControlPanel", "PAGE_IDS"]
