"""Stable diagnostic codes (PETPACK_SPEC 19; CONFORMANCE 3).

Codes are stable within a major version and never localized.  Params must
be sanitized (no user text, absolute paths, URLs, or manifest dumps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    phase: str
    message_key: str
    params: dict[str, Any] = field(default_factory=dict)
    recoverable: bool = False
    user_action: str | None = None
    cause_code: str | None = None

    def __str__(self) -> str:
        return f"{self.code}({self.severity.value},{self.phase})"


class ValidationFailure(Exception):
    """Raised when an ERROR-level diagnostic aborts validation."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(str(diagnostic))
        self.diagnostic = diagnostic


# -- archive (PPK-ARC-*) -------------------------------------------------------

ARC_E001_UNSUPPORTED_CONTAINER = "PPK-ARC-E001"
ARC_E002_ENCRYPTED_OR_MULTIVOLUME = "PPK-ARC-E002"
ARC_E003_UNSAFE_PATH = "PPK-ARC-E003"
ARC_E004_COLLISION = "PPK-ARC-E004"
ARC_E005_LINK_OR_REPARSE = "PPK-ARC-E005"
ARC_E006_DISABLED_COMPRESSION_OR_NESTED = "PPK-ARC-E006"
ARC_E007_BUDGET_EXCEEDED = "PPK-ARC-E007"
ARC_E008_TRUNCATED_OR_CRC = "PPK-ARC-E008"

# -- manifest (PPK-MAN-*) ------------------------------------------------------

MAN_E001_MISSING_OR_INVALID_UTF8 = "PPK-MAN-E001"
MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE = "PPK-MAN-E002"
MAN_E003_UNSUPPORTED_SCHEMA = "PPK-MAN-E003"
MAN_E004_MISSING_OR_INVALID_FIELD = "PPK-MAN-E004"
MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE = "PPK-MAN-E005"
MAN_E006_INCOMPATIBLE_ENGINE = "PPK-MAN-E006"
MAN_E007_UNDECLARED_OR_MISSING_FILE = "PPK-MAN-E007"
MAN_E008_SIZE_OR_HASH_MISMATCH = "PPK-MAN-E008"
MAN_E009_RESERVED_NAMESPACE_OR_TRUST = "PPK-MAN-E009"

# -- resources (PPK-RES-*) -----------------------------------------------------

RES_E001_DISALLOWED_TYPE_OR_MIME = "PPK-RES-E001"
RES_E002_REMOTE_OR_EXTERNAL_LOCATOR = "PPK-RES-E002"
RES_E003_IMAGE_DECODE_OR_PIXELS = "PPK-RES-E003"
RES_E004_FRAMES_OR_DECODE_MEMORY = "PPK-RES-E004"
RES_E005_AUDIO_FORMAT_OR_DURATION = "PPK-RES-E005"

# -- actions & text (PPK-ACT-*, PPK-TXT-*) --------------------------------------

ACT_E001_CORE_IDLE_MISSING = "PPK-ACT-E001"
ACT_E002_UNKNOWN_CORE_SEMANTIC = "PPK-ACT-E002"
ACT_E003_INVALID_NAMESPACE = "PPK-ACT-E003"
ACT_E004_UNSUPPORTED_RENDERER = "PPK-ACT-E004"
ACT_E005_MISSING_ASSET_OR_TYPE = "PPK-ACT-E005"
ACT_E006_TIMING_LOOP_OR_BUDGET = "PPK-ACT-E006"
ACT_E007_SYSTEM_TRIGGER_REQUESTED = "PPK-ACT-E007"

TXT_E001_FORBIDDEN_TEMPLATE_OR_EXPR = "PPK-TXT-E001"
TXT_E002_INVALID_PROFILE = "PPK-TXT-E002"
TXT_E003_CONTROL_OR_RICH_TEXT = "PPK-TXT-E003"

# -- rights (PPK-RGT-*) ---------------------------------------------------------

RGT_E001_INVALID_RIGHTS = "PPK-RGT-E001"
RGT_E002_MISSING_REF = "PPK-RGT-E002"
RGT_E003_OFFICIAL_UNKNOWN_RIGHTS = "PPK-RGT-E003"
RGT_W001_UNKNOWN_BASIS = "PPK-RGT-W001"
RGT_W002_PUBLISHER_UNVERIFIED = "PPK-RGT-W002"

# -- lifecycle & privacy (PPK-LCY-*, PPK-PRV-*) ----------------------------------

LCY_E002_PACKAGE_ID_CONFLICT = "PPK-LCY-E002"
LCY_E007_DIGEST_CONFLICT_SAME_VERSION = "PPK-LCY-E007"

PRV_E001_FORBIDDEN_PERMISSION = "PPK-PRV-E001"
PRV_E002_EXECUTABLE_URL_OR_AUTO_NETWORK = "PPK-PRV-E002"
PRV_E003_SENSITIVE_DATA_IN_LOGS = "PPK-PRV-E003"
