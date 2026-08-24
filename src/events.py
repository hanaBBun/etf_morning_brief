"""무료·무키 방식의 공식 경제 일정 수집."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .config import now_kst

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
JACKSON_HOLE_URL = "https://www.kansascityfed.org/research/jackson-hole-economic-symposium/"

BEA_IMPORTANT = {
    "Gross Domestic Product": "미국 GDP",
    "Personal Income and Outlays": "미국 개인소득·소비 및 PCE 물가",
    "U.S. International Trade in Goods and Services": "미국 무역수지",
}
BLS_IMPORTANT = {
    "Employment Situation": "미국 고용보고서",
    "Consumer Price Index": "미국 소비자물가지수(CPI)",
    "Producer Price Index": "미국 생산자물가지수(PPI)",
    "Job Openings and Labor Turnover Survey": "미국 구인·이직보고서(JOLTS)",
    "Employment Cost Index": "미국 고용비용지수(ECI)",
}


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _event(dt: datetime, title: str, source: str, url: str) -> dict:
    local = dt.astimezone(KST)
    wd = "월화수목금토일"[local.weekday()]
    return {"유형": "일정", "때": f"{local.month}/{local.day} ({wd}) {local:%H:%M}",
            "날짜": local.strftime("%Y-%m-%d"), "내용": title,
            "출처": {"이름": source, "url": url}}


def _bea(start: datetime, end: datetime) -> list[dict]:
    out = []
    payload = requests.get(BEA_URL, timeout=15).json()
    for original, korean in BEA_IMPORTANT.items():
        for value in (payload.get(original) or {}).get("release_dates", []):
            dt = datetime.fromisoformat(value)
            if start <= dt.astimezone(KST) < end:
                out.append(_event(dt, korean, "미 상무부 경제분석국(BEA)", BEA_URL))
    return out


def _unfold_ics(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _bls(start: datetime, end: datetime) -> list[dict]:
    text = _unfold_ics(requests.get(BLS_ICS, timeout=15).text)
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"(?m)^SUMMARY:(.+)$", block)
        stamp = re.search(r"(?m)^DTSTART[^:]*:(\d{8}T\d{6})", block)
        if not summary or not stamp:
            continue
        matched = next((ko for en, ko in BLS_IMPORTANT.items()
                        if en.lower() in summary.group(1).lower()), None)
        if not matched:
            continue
        dt = datetime.strptime(stamp.group(1), "%Y%m%dT%H%M%S").replace(
            tzinfo=ZoneInfo("America/New_York"))
        if start <= dt.astimezone(KST) < end:
            out.append(_event(dt, matched, "미 노동통계국(BLS)", BLS_ICS))
    return out


def _jackson_hole(start: datetime, end: datetime) -> list[dict]:
    html = requests.get(JACKSON_HOLE_URL, timeout=15).text
    year = start.year
    hit = re.search(rf"{year} Jackson Hole Economic Policy Symposium will take place "
                    rf"Aug\.\s*(\d{{1,2}})[-–](\d{{1,2}})", html, re.I)
    if not hit:
        return []
    first, last = int(hit.group(1)), int(hit.group(2))
    dt = datetime(year, 8, first, 9, 0, tzinfo=ZoneInfo("America/Denver"))
    if not (start <= dt.astimezone(KST) < end):
        return []
    return [_event(dt, f"잭슨홀 경제정책 심포지엄({first}~{last}일·현지시간)",
                   "캔자스시티 연방준비은행", JACKSON_HOLE_URL)]


def collect_week() -> list[dict]:
    """이번 주의 시장 영향도가 높은 공식 일정만 반환한다."""
    start, end = _week_bounds(now_kst())
    out = []
    for label, fn in (("BEA", _bea), ("BLS", _bls), ("Jackson Hole", _jackson_hole)):
        try:
            out.extend(fn(start, end))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s 공식 일정 수집 실패: %s", label, exc)
    out.sort(key=lambda x: (x.get("날짜", ""), x.get("때", ""), x.get("내용", "")))
    return out
