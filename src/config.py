"""설정 로딩 및 공통 유틸."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> str:
    return now_kst().strftime("%Y-%m-%d")


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    """환경변수를 읽는다. required면 없을 때 예외."""
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(
            f"환경변수 {name} 가 설정되지 않았습니다. "
            f"GitHub 저장소 Settings > Secrets and variables > Actions 에서 등록하세요."
        )
    return val


WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def kdate(dt: datetime | None = None) -> str:
    dt = dt or now_kst()
    return f"{dt.year}년 {dt.month}월 {dt.day}일 ({WEEKDAY_KO[dt.weekday()]})"


def fmt_num(v: float | int | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:,.0f}"


def fmt_pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+.{digits}f}%"


def fmt_eok(won: float | None) -> str:
    """원 단위 금액을 억/조 단위 한국어 표기로."""
    if won is None:
        return "—"
    sign = "-" if won < 0 else ""
    a = abs(won)
    jo = int(a // 1_000_000_000_000)
    eok = int((a % 1_000_000_000_000) // 100_000_000)
    if jo and eok:
        return f"{sign}{jo}조 {eok:,}억"
    if jo:
        return f"{sign}{jo}조"
    return f"{sign}{eok:,}억"
