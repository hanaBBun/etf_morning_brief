"""뉴스 RSS 수집."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .config import now_kst

log = logging.getLogger(__name__)


def collect_news(cfg: dict, hours: int = 30) -> dict[str, list[dict]]:
    """config의 RSS 목록에서 최근 N시간 기사만 수집."""
    import feedparser

    cutoff = now_kst() - timedelta(hours=hours)
    limit = int((cfg.get("뉴스") or {}).get("기사_최대개수", 40))
    out: dict[str, list[dict]] = {}

    for group, sources in (cfg.get("뉴스") or {}).items():
        if not isinstance(sources, list):
            continue
        items: list[dict] = []
        undated = stale = 0
        for src in sources:
            try:
                feed = feedparser.parse(src["url"])
                for e in feed.entries[:60]:
                    pub = _parsed_time(e)
                    # 발행일이 없으면 버린다.
                    # 예전에는 통과시켰는데, 그 기사들이 신선도 검사를 전부 우회해
                    # 석 달 전 기사가 '최근'으로 실리는 사고가 났다.
                    if not pub:
                        undated += 1
                        continue
                    if pub < cutoff:
                        stale += 1
                        continue
                    items.append({
                        "제목": _clean(getattr(e, "title", "")),
                        "요약": _clean(getattr(e, "summary", ""))[:400],
                        "링크": getattr(e, "link", ""),
                        "출처": src["이름"],
                        "날짜": pub.strftime("%Y-%m-%d") if pub else "",
                        "시각": pub.strftime("%m/%d %H:%M") if pub else "",
                        "경과시간": _elapsed_hours(pub),
                        "_ts": pub.timestamp() if pub else 0,
                    })
            except Exception as ex:  # noqa: BLE001
                log.warning("RSS 실패 %s: %s", src.get("이름"), ex)
        items.sort(key=lambda r: r["_ts"], reverse=True)
        for it in items:
            it.pop("_ts", None)
        out[group] = items[:limit]
        log.info("뉴스 %s: %d건 수집 (날짜없음 %d건·기간초과 %d건 제외, 최근 %d시간)",
                 group, len(out[group]), undated, stale, hours)
    return out


WEEKDAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def week_start(cfg: dict):
    """설정한 요일의 이번 주 00시(KST)를 돌려준다."""
    label = str(((cfg.get("목요일_전달문") or {}).get("뉴스_시작요일") or "월")).strip()
    target = WEEKDAY_INDEX.get(label, 0)
    now = now_kst()
    days_since = (now.weekday() - target) % 7
    return (now - timedelta(days=days_since)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ), label


def collect_since_weekday(cfg: dict) -> dict[str, list[dict]]:
    """설정한 요일 00시(KST)부터 지금까지의 기사만. 목요일 전달문용."""
    start, label = week_start(cfg)
    hours = max(int((now_kst() - start).total_seconds() // 3600), 24)
    log.info("전달문 뉴스 범위: 최근 %d시간 (%s요일 00시 기준)", hours, label)
    return collect_news(cfg, hours=hours)


# 예전 이름 호환
collect_since_tuesday = collect_since_weekday


# ─────────────────────────────────────────────
# 기사 본문 추출
# RSS 요약만으로는 "누가 어떤 의견을 냈는지"가 잘 안 잡힌다.
# 목요일 전달문에서만 상위 기사 본문을 가볍게 긁어온다.
# ─────────────────────────────────────────────
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def resolve_link(url: str, timeout: int = 12) -> str:
    """구글뉴스 중계 링크를 실제 기사 주소로 바꾼다. 못 바꾸면 원래 값 그대로.

    구글뉴스 RSS 의 link 는 news.google.com/rss/articles/... 형태의 중계 주소다.
    이걸 그대로 requests 로 열면 자바스크립트 리다이렉트 껍데기만 나와서
    본문이 한 글자도 안 잡힌다. (발언 인용이 통째로 비는 원인이었다.)
    """
    import base64
    import re

    if "news.google.com" not in (url or ""):
        return url

    # ① 주소 안에 실제 URL 이 base64 로 들어있는 형식
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", url)
    if m:
        s = m.group(1)
        try:
            raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
            hit = re.search(rb"https?://[^\x00-\x20\"'<>\\]{12,}", raw)
            if hit:
                real = hit.group(0).decode("utf-8", "ignore")
                if "google.com" not in real:
                    return real
        except Exception:  # noqa: BLE001
            pass

    # ② 안 되면 중계 페이지를 열어 진짜 주소를 찾는다
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        html = r.text
        if r.url and "news.google.com" not in r.url:
            return r.url
        for pat in (r'data-n-au="([^"]+)"',
                    r'<meta[^>]+http-equiv="refresh"[^>]+url=([^"\']+)',
                    r'<a[^>]+href="(https?://(?!\w*\.?google\.)[^"]+)"'):
            hit = re.search(pat, html, re.I)
            if hit:
                return hit.group(1).strip()
    except Exception as e:  # noqa: BLE001
        log.debug("링크 해석 실패 %s: %s", url[:60], e)
    return url


def fetch_article_text(url: str, max_chars: int = 2500, timeout: int = 12) -> str:
    """기사 본문을 최선 노력으로 추출. 실패하면 빈 문자열."""
    import re

    import requests

    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding
        html = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("본문 수집 실패 %s: %s", url[:60], e)
        return ""

    # 구글 동의 화면·리다이렉트 껍데기는 본문이 아니다
    if "news.google.com" in url and len(html) < 4000:
        return ""

    html = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", html)

    # ① <article> 또는 본문으로 보이는 컨테이너
    best = ""
    for pat in (
        r"(?is)<article[^>]*>(.*?)</article>",
        r'(?is)<div[^>]*(?:id|class)="[^"]*(?:article|news[_-]?body|content[_-]?body|'
        r'articleBody|view[_-]?content|entry[_-]?content)[^"]*"[^>]*>(.*?)</div>',
    ):
        for m in re.finditer(pat, html):
            text = _strip(m.group(1))
            if len(text) > len(best):
                best = text

    # ② 그래도 짧으면 <p> 태그를 모은다
    if len(best) < 300:
        ps = [_strip(m.group(1)) for m in re.finditer(r"(?is)<p[^>]*>(.*?)</p>", html)]
        joined = " ".join(p for p in ps if len(p) > 30)
        if len(joined) > len(best):
            best = joined

    return best[:max_chars]


def _strip(fragment: str) -> str:
    import html as _html
    import re
    t = re.sub(r"(?is)<br\s*/?>", " ", fragment)
    t = re.sub(r"(?is)</(p|div|li|h[1-6])>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def enrich_with_body(items: list[dict], limit: int = 14) -> list[dict]:
    """상위 N건의 링크를 실제 기사 주소로 바꾸고 본문을 붙인다."""
    out, got, fixed = [], 0, 0
    for i, it in enumerate(items):
        row = dict(it)
        if i < limit and row.get("링크"):
            real = resolve_link(row["링크"])
            if real and real != row["링크"]:
                row["링크"] = real
                fixed += 1
            body = fetch_article_text(row["링크"])
            if body and len(body) > 200:
                row["본문"] = body
                got += 1
        out.append(row)
    if items:
        log.info("  본문 %d/%d건 · 실주소 복원 %d건", got, min(limit, len(items)), fixed)
    return out


def _parsed_time(entry: Any):
    import datetime as dt
    from .config import KST
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None)
        if t:
            try:
                return dt.datetime(*t[:6], tzinfo=dt.timezone.utc).astimezone(KST)
            except Exception:  # noqa: BLE001
                continue
    return None


def _elapsed_hours(pub) -> int | None:
    if not pub:
        return None
    try:
        return int((now_kst() - pub).total_seconds() // 3600)
    except Exception:  # noqa: BLE001
        return None


def _clean(s: str) -> str:
    import html
    import re
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()
