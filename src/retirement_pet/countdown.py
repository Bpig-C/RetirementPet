"""Retirement countdown calculation.

The remaining time is ALWAYS computed as ``target - now`` with a fixed target
datetime.  There is no internal decrementing counter, so sleep, hibernation,
shutdown, restarts and system-clock changes can never drift the countdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from retirement_pet.models import LifeStage

DEFAULT_TARGET = datetime(2060, 7, 7, 21, 32, 0)

STAGE_TEXT = {
    LifeStage.YOUNG: "青年 · 正当时",
    LifeStage.MIDDLE: "中年 · 行路期",
    LifeStage.OLD: "晚年 · 归途期",
    LifeStage.RETIRED: "退休啦",
}

# 每个阶段一组副文案，按“年积日 % 池长”确定性轮换：同一天稳定不变，
# 跨天自然换句。产品主旨是提醒时间的流逝与所剩，而非“退休=解放”，
# 青年期聚焦“趁年轻享受当下”，其余阶段保留原句为首选并补充同主题句。
STAGE_SUBTITLES: dict[LifeStage, tuple[str, ...]] = {
    LifeStage.YOUNG: (
        "今天，是你余生里最年轻的一天",
        "趁年轻，去享受这个世界",
        "世界很大，趁现在去看看",
        "想做的事，趁早去做",
        "最好的年华，别只用来等待",
        "年轻不是用来熬的，是用来活的",
        "日子过一天少一天，快乐要一天天攒",
    ),
    LifeStage.MIDDLE: (
        "认真生活，也记得看看远方",
        "时间在走，别弄丢了生活",
        "一半是担当，一半是自己",
    ),
    LifeStage.OLD: (
        "时间渐近，步子可以慢一点",
        "剩下的日子，更要过得像自己",
        "回望来路，也别忘了眼前",
    ),
    LifeStage.RETIRED: (
        "今天开始，时间属于你自己",
        "时间从未停下，此刻由你定义",
    ),
}

# 兼容旧接口：每阶段取第一句。
STAGE_SUBTITLE = {stage: subs[0] for stage, subs in STAGE_SUBTITLES.items()}


def subtitle_for(now: datetime, stage: LifeStage) -> str:
    """Deterministic per-day pick, so the line is stable within a day."""
    pool = STAGE_SUBTITLES[stage]
    return pool[now.timetuple().tm_yday % len(pool)]


def _years_before(moment: datetime, years: int) -> datetime:
    """Same month/day ``years`` earlier; Feb-29 falls back to Feb-28."""
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:
        return moment.replace(year=moment.year - years, day=28)


def stage_for(now: datetime, target: datetime) -> LifeStage:
    """Life stage derived from the fixed target (retirement at age 60).

    young  : before target-30y, middle: target-30y..target-10y,
    old    : target-10y..target, retired: at/after target.
    """
    if now >= target:
        return LifeStage.RETIRED
    if now >= _years_before(target, 10):
        return LifeStage.OLD
    if now >= _years_before(target, 30):
        return LifeStage.MIDDLE
    return LifeStage.YOUNG


@dataclass(frozen=True)
class CountdownSnapshot:
    now: datetime
    target: datetime
    days: int
    hours: int
    minutes: int
    seconds: int
    total_seconds: int
    stage: LifeStage
    is_past: bool

    @property
    def stage_text(self) -> str:
        return STAGE_TEXT[self.stage]

    @property
    def stage_subtitle(self) -> str:
        return subtitle_for(self.now, self.stage)

    @property
    def clock_text(self) -> str:
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}"

    @property
    def days_text(self) -> str:
        return f"{self.days:,} DAYS"


def compute_countdown(now: datetime, target: datetime) -> CountdownSnapshot:
    """Snapshot of the countdown; clamped at zero once past the target."""
    delta = target - now
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return CountdownSnapshot(
        now=now,
        target=target,
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
        total_seconds=total,
        stage=stage_for(now, target),
        is_past=delta.total_seconds() <= 0,
    )


def parse_target(value: str) -> datetime:
    """Parse a target datetime string like ``2060-07-07T21:32:00``."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def target_display(target: datetime) -> str:
    return target.strftime("%Y.%m.%d  %H:%M")


__all__ = [
    "DEFAULT_TARGET",
    "CountdownSnapshot",
    "compute_countdown",
    "parse_target",
    "stage_for",
    "subtitle_for",
    "target_display",
    "STAGE_TEXT",
    "STAGE_SUBTITLE",
    "STAGE_SUBTITLES",
    "timedelta",
]
