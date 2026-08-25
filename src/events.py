"""무료·무키 방식의 공식 경제 일정 수집."""
from __future__ import annotations

import logging
import re
from html import unescape
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .config import now_kst

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
JACKSON_HOLE_URL = "https://www.kansascityfed.org/research/jackson-hole-economic-symposium/"
BOK_MPC_URL = ("https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do"
               "?menuNo=200755&mtgSe=A")
FED_CALENDAR_URL = "https://www.federalreserve.gov/newsevents/{year}-august.htm"
NVIDIA_RSS_URL = "https://nvidianews.nvidia.com/rss.xml"
NVIDIA_SITEMAP_URL = "https://nvidianews.nvidia.com/sitemap.xml"

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


def _date_event(day: datetime, title: str, source: str, url: str) -> dict:
    """발표 시각을 공식적으로 확인할 수 없는 일정은 날짜만 표시한다."""
    local = day.astimezone(KST)
    wd = "월화수목금토일"[local.weekday()]
    return {"유형": "일정", "때": f"{local.month}/{local.day} ({wd})",
            "날짜": local.strftime("%Y-%m-%d"), "내용": title,
            "출처": {"이름": source, "url": url}}


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


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


def _bok_mpc(start: datetime, end: datetime) -> list[dict]:
    text = _plain(requests.get(BOK_MPC_URL, timeout=15).text)
    out = []
    # 연간 회의 목록은 시각을 공시하지 않으므로 임의의 시각을 붙이지 않는다.
    for month, day in re.findall(r"(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금토일]\)", text):
        dt = datetime(start.year, int(month), int(day), tzinfo=KST)
        if start <= dt < end:
            out.append(_date_event(dt, "한국은행 금융통화위원회 기준금리 결정",
                                   "한국은행", BOK_MPC_URL))
    return out


def _nvidia_earnings(start: datetime, end: datetime) -> list[dict]:
    rss = requests.get(NVIDIA_RSS_URL, timeout=15).text
    urls = []
    for item in rss.split("<item>")[1:]:
        if "Sets Conference Call" in item and "Financial Results" in item:
            link = re.search(r"<link>\s*(?:<!\[CDATA\[)?(https?://[^<\]]+)", item)
            if link:
                urls.append(unescape(link.group(1).strip()))
    # RSS는 최신 기사 수가 제한돼 한 달 전 공지된 실적 일정이 사라질 수 있다.
    # 공식 사이트맵에서 같은 보도자료를 보충한다.
    sitemap = requests.get(NVIDIA_SITEMAP_URL, timeout=15).text
    urls.extend(re.findall(r"<loc>(https?://[^<]*sets-conference-call[^<]*)</loc>",
                           sitemap, re.I))
    for url in dict.fromkeys(unescape(u) for u in urls):
        text = _plain(requests.get(url, timeout=15).text)
        hit = re.search(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s*"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s*"
            r"(\d{1,2}),\s*at\s*(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.)\s*PT",
            text, re.I)
        if not hit:
            continue
        months = {name: i for i, name in enumerate(
            "January February March April May June July August September October November December".split(), 1)}
        call_hour = int(hit.group(3)) % 12 + (12 if hit.group(5).lower().startswith("p") else 0)
        release = re.search(r"publicly announced at approximately\s*(\d{1,2})(?::(\d{2}))?\s*"
                            r"(a\.m\.|p\.m\.)\s*PT", text, re.I)
        hour, minute = call_hour, int(hit.group(4) or 0)
        if release:
            hour = int(release.group(1)) % 12 + (12 if release.group(3).lower().startswith("p") else 0)
            minute = int(release.group(2) or 0)
        dt = datetime(start.year, months[hit.group(1).title()], int(hit.group(2)),
                      hour, minute, tzinfo=ZoneInfo("America/Los_Angeles"))
        local = dt.astimezone(KST)
        if start <= local < end:
            return [_event(dt, f"엔비디아 실적 발표(미국 {dt.month}/{dt.day} 장 마감 후)·콘퍼런스콜 06:00 KST",
                           "NVIDIA 뉴스룸", url)]
    return []


def _fed_speeches(start: datetime, end: datetime) -> list[dict]:
    url = FED_CALENDAR_URL.format(year=start.year)
    text = _plain(requests.get(url, timeout=15).text)
    # 연준 월간 캘린더의 '10:00 a.m. Speech - Chairman ... Keynote ... 28' 구조.
    anchor = re.search(r"Speech\s*-\s*Chairman\s+Kevin\s+Warsh", text, re.I)
    if not anchor:
        return []
    nearby = text[max(0, anchor.start() - 250):anchor.end() + 600]
    clock = re.search(r"(\d{1,2}):(\d{2})\s*(a\.?\s*m\.?|p\.?\s*m\.?)", nearby, re.I)
    keynote = re.search(r"Keynote\s+Remarks", nearby, re.I)
    # 캘린더 HTML은 날짜가 연설 블록의 앞이나 뒤에 올 수 있다.
    day_hits = re.findall(r"\b(2[7-9])\b", nearby)
    if not clock or not keynote or not day_hits:
        return []
    meridiem = re.sub(r"[^ap]", "", clock.group(3).lower())
    hour = int(clock.group(1)) % 12 + (12 if meridiem.startswith("p") else 0)
    dt = datetime(start.year, 8, int(day_hits[0]), hour, int(clock.group(2)),
                  tzinfo=ZoneInfo("America/New_York"))
    if not (start <= dt.astimezone(KST) < end):
        return []
    return [_event(dt, "케빈 워시 연준 의장 잭슨홀 기조연설",
                   "미 연방준비제도", url)]


def _jackson_hole(start: datetime, end: datetime) -> list[dict]:
    html = requests.get(JACKSON_HOLE_URL, timeout=15).text
    year = start.year
    hit = re.search(rf"{year} Jackson Hole Economic Policy Symposium will take place "
                    rf"Aug\.\s*(\d{{1,2}})[-–](\d{{1,2}})", html, re.I)
    if not hit:
        return []
    first, last = int(hit.group(1)), int(hit.group(2))
    dt = datetime(year, 8, first, tzinfo=KST)
    if not (start <= dt < end):
        return []
    return [_date_event(dt, f"잭슨홀 경제정책 심포지엄({first}~{last}일·현지시간)",
                        "캔자스시티 연방준비은행", JACKSON_HOLE_URL)]


def collect_week() -> list[dict]:
    """이번 주의 시장 영향도가 높은 공식 일정만 반환한다."""
    start, end = _week_bounds(now_kst())
    out = []
    for label, fn in (("BEA", _bea), ("BLS", _bls), ("한국은행 금통위", _bok_mpc),
                      ("NVIDIA 실적", _nvidia_earnings), ("연준 연설", _fed_speeches),
                      ("Jackson Hole", _jackson_hole)):
        try:
            out.extend(fn(start, end))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s 공식 일정 수집 실패: %s", label, exc)
    out.sort(key=lambda x: (x.get("날짜", ""), x.get("때", ""), x.get("내용", "")))
    return out
