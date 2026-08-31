"""수집 데이터 + AI 결과 → HTML 렌더링, GitHub Pages 인덱스 갱신."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, env as config_env, now_kst

log = logging.getLogger(__name__)
DOCS = ROOT / "docs"


def _dir(v: float | None) -> str:
    if v is None:
        return "flat"
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def _cell(row: dict, digits: int = 2, unit: str = "", as_bp: bool = False) -> dict:
    """레벨과 변화를 같은 봉(row)에서만 만든다. 서로 다른 시점 값을 섞지 않는다."""
    close = row.get("종가")
    if close is None:
        return {"이름": row["이름"], "값": "—", "변화": "", "방향": "flat", "기준일": ""}

    if as_bp:
        bp = row.get("변화bp")
        if bp is None and row.get("전일") is not None:
            bp = (close - row["전일"]) * 100
        변화 = f"{bp:+.1f}bp" if bp is not None else ""
        방향 = _dir(bp)
    else:
        pct = row.get("등락률")
        변화 = f"{pct:+.2f}%" if pct is not None else ""
        방향 = _dir(pct)

    return {
        "이름": row["이름"],
        "값": f"{close:,.{digits}f}{unit}",
        "변화": 변화,
        "방향": 방향,
        "기준일": row.get("기준일", ""),
        "상태": row.get("상태", ""),
    }


def _md(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}" if iso and len(iso) >= 10 else ""


def _stamp(cells: list[dict]) -> str:
    """그룹 안 기준일이 하나면 라벨에 붙이고, 섞였으면 빈 값을 돌려준다."""
    dates = {c.get("기준일") for c in cells if c.get("기준일")}
    states = {c.get("상태") for c in cells if c.get("상태")}
    if len(dates) == 1:
        d = _md(next(iter(dates)))
        st = next(iter(states)) if len(states) == 1 else ""
        return f"{d} {st}".strip()
    return ""


def _annotate_mixed(cells: list[dict]) -> None:
    """기준일이 섞인 그룹은 각 항목 이름 옆에 날짜를 박아 혼동을 막는다."""
    dates = {c.get("기준일") for c in cells if c.get("기준일")}
    if len(dates) <= 1:
        return
    log.warning("기준일 혼재 — 항목별 날짜를 표기합니다: %s", sorted(d for d in dates if d))
    for c in cells:
        if c.get("기준일"):
            c["이름"] = f"{c['이름']} ({_md(c['기준일'])})"


def _glance(data: dict) -> list[dict]:
    """시장 한눈에 — 그룹별 요약. 값이 없는 그룹은 빠진다.

    한 그룹 안의 레벨·변화는 모두 같은 timestamp에서 나온 것만 담는다.
    """
    ind = data.get("지표") or {}
    groups: list[dict] = []

    def add(label: str, cells: list[dict]):
        # 시세가 안 잡힌 항목은 빼되, 무엇이 빠졌는지는 라벨에 남긴다.
        # (항목 개수가 날마다 달라 보이는 이유를 알 수 있게)
        missing = [c["이름"] for c in cells if c["값"] == "—"]
        cells = [c for c in cells if c["값"] != "—"]
        if not cells:
            if missing:
                log.warning("%s 그룹 전체 미집계: %s", label, ", ".join(missing))
            return
        _annotate_mixed(cells)
        stamp = _stamp(cells)
        text = f"{label} · {stamp}" if stamp else label
        if missing:
            log.warning("%s 미집계 항목: %s", label, ", ".join(missing))
            text += f" · {', '.join(missing)} 미집계"
        groups.append({"라벨": text, "항목": cells})

    # 국내 지수 · 환율 · 수급은 모두 같은 거래일 마감 기준이라 한 그룹으로 묶는다.
    국내 = [_cell(r, 2) for r in (data.get("국내지수") or [])]
    국내 += [_cell(r, 1, "원") for r in ind.get("국내", []) if r["이름"] == "원/달러"]
    if data.get("수급"):
        from .config import fmt_eok
        for f in data["수급"]:
            if f["주체"] == "외국인":
                국내.append({
                    "이름": "외국인 수급", "값": fmt_eok(f["순매수"]), "변화": "",
                    "방향": "up" if f["순매수"] >= 0 else "down",
                    "기준일": data.get("국내기준일_ISO", ""), "상태": "마감",
                })
    add("국내 증시 · 환율", 국내)

    add("해외 지수", [_cell(r, 2) for r in ind.get("해외지수", [])])
    # 국채 금리 변화는 항상 bp로 표기한다.
    add("국채 금리", [_cell(r, 3, "%", as_bp=True) for r in ind.get("금리", [])])
    add("원자재 · 변동성",
        [_cell(r, 2) for r in ind.get("원자재", [])]
        + [_cell(r, 2) for r in ind.get("변동성", [])])

    return groups


def _elapsed(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return f"{int(h)}시간 전" if h >= 1 else f"{int(h * 60)}분 전"
    except Exception:  # noqa: BLE001
        return ""


OVERLAP_CLASS = {"높음": "high", "보통": "mid", "낮음": "low"}


def _channel_relevance(video: dict) -> tuple[str, str]:
    """영상 제목이 ETF 아는형의 상시 콘텐츠 범위와 얼마나 밀접한지 판정.

    수집기의 ETF점수는 후보를 넓게 모으기 위한 값이므로 등급 판정에는 쓰지 않는다.
    점수만으로 '높음'을 주면 일반 시황 영상까지 ETF 직접 주제로 오인된다.
    """
    title = str(video.get("제목") or "")
    direct = ("ETF", "상장지수", "ISA", "연금저축", "퇴직연금", "IRP",
              "월배당", "커버드콜", "분배금", "S&P500", "S&P 500",
              "나스닥100", "코스피200", "레버리지", "인버스")
    adjacent = ("금리", "채권", "반도체", "배당", "자산배분", "포트폴리오",
                "주식", "증시", "지수", "연금", "절세", "환율", "원자재")
    hits = [word for word in direct if word.lower() in title.lower()]
    if hits:
        return "높음", f"채널 핵심 주제: {', '.join(hits[:2])}"
    near = [word for word in adjacent if word.lower() in title.lower()]
    if near:
        return "보통", f"채널 인접 주제: {', '.join(near[:2])}"
    return "낮음", "ETF 채널과 직접 연결이 적음"


def _youtube(data: dict, ai: dict) -> list[dict]:
    """수집한 영상 + AI가 붙인 주제·훅·겹침 판정을 합친다."""
    raw = (data.get("유튜브") or {}).get("급상승") or []
    cached_notes = (data.get("유튜브") or {}).get("분석") or []
    notes = {n.get("영상ID"): n for n in cached_notes if isinstance(n, dict)}
    notes.update({n.get("영상ID"): n for n in (ai.get("유튜브") or []) if isinstance(n, dict)})
    if not raw:
        return []
    # AI가 일부 영상만 분석했더라도 수집기가 당일 보존한 영상은 모두 표시한다.
    # 과거에는 notes가 하나라도 있으면 미분석 영상 전체가 화면에서 탈락했다.
    picked = raw[:5]
    out = []
    for v in picked[:5]:
        n = notes.get(v.get("영상ID"), {})
        ov, auto_reason = _channel_relevance(v)
        out.append({
            "제목": v.get("제목", ""),
            "채널": v.get("채널", ""),
            "링크": v.get("링크", ""),
            "조회수표시": f"{v.get('조회수', 0):,}회",
            "경과": _elapsed(v.get("업로드", "")),
            "핵심주제": n.get("핵심주제", ""),
            "훅": n.get("훅", ""),
            "관련성": ov,
            "관련성근거": auto_reason,
            "관련성등급": OVERLAP_CLASS.get(ov, "low"),
            "ETF관련": bool(v.get("ETF관련")),
            "길이초": v.get("길이초"),
            "채널유형": _channel_type(v.get("채널", ""), bool(v.get("ETF관련"))),
        })
    return out


def _channel_type(channel: str, etf_related: bool) -> str:
    official = ("KODEX", "스마트 타이거", "RISE ETF", "미래에셋 스마트머니")
    if any(name.lower() in str(channel).lower() for name in official):
        return "운용사 공식"
    return "ETF 경쟁" if etf_related else "일반 경제"


def _weekly_table(data: dict, mode: str) -> list[dict]:
    """기간 비교는 토요일 주간판에서만 노출한다."""
    if mode != "weekly":
        return []
    daily_rates = {}
    for r in (data.get("국내지수") or []):
        if r.get("등락률") is not None:
            daily_rates[str(r.get("이름"))] = float(r["등락률"])
    for group in (data.get("지표") or {}).values():
        if not isinstance(group, list):
            continue
        for r in group:
            if isinstance(r, dict) and r.get("등락률") is not None:
                daily_rates[str(r.get("이름"))] = float(r["등락률"])
    rows = []
    for r in data.get("주간_대표흐름") or []:
        row = {"이름": r.get("이름", ""), "기준일": _md(r.get("기준일", ""))}
        for key in ("1일", "1주", "1개월"):
            # 1일 값은 시장 한눈에와 같은 확정 봉을 사용한다. 서로 다른
            # 데이터 공급자의 최신 봉 시각이 어긋나 표와 본문이 충돌하는 것을 막는다.
            value = daily_rates.get(str(r.get("이름"))) if key == "1일" else r.get(key)
            if value is not None and (not isinstance(value, (int, float)) or abs(float(value)) > 100):
                log.warning("기간별 흐름 비정상 값 제외: %s %s=%s", r.get("이름"), key, value)
                value = None
            row[key] = "—" if value is None else f"{float(value):+.2f}%"
            row[f"{key}방향"] = _dir(value)
        rows.append(row)
    return rows


def _monday_summary(data: dict, ai: dict) -> dict | None:
    """월요일에만 지난주 복기·주말 변수·국내 개장 연결을 짧게 묶는다."""
    if data.get("브리핑역할") != "이번 주 준비":
        return None
    raw = ai.get("월요일요약") if isinstance(ai.get("월요일요약"), dict) else {}
    recap = [str(x).strip()[:100] for x in (raw.get("지난주복기") or []) if str(x).strip()][:3]
    if not recap:
        ranked = sorted(
            [x for x in (data.get("주간_대표흐름") or []) if x.get("1주") is not None],
            key=lambda x: abs(float(x.get("1주") or 0)), reverse=True)
        recap = [f"{x.get('이름')}: 지난주 {float(x['1주']):+.2f}%"
                 for x in ranked[:3]]
    weekend = []
    for x in raw.get("주말변수") or []:
        if not isinstance(x, dict) or not str(x.get("제목") or "").strip():
            continue
        weekend.append({"제목": str(x.get("제목"))[:55],
                        "설명": str(x.get("설명") or "")[:160],
                        "출처": x.get("출처") or []})
    open_impact = str(raw.get("국내개장영향") or "").strip()[:180]
    if not open_impact:
        domestic = next((x for x in (ai.get("시장브리핑") or [])
                         if x.get("시장") == "국내"), {})
        open_impact = str(domestic.get("ETF연결") or "")[:180]
    return {"지난주복기": recap, "주말변수": weekend[:3],
            "국내개장영향": open_impact}


def _checkpoint_groups(items: list[dict]) -> list[dict]:
    """이번 주 확인 → 날짜별 일정 → 상시 확인 순으로 묶는다."""
    import re

    buckets: dict[tuple[int, str], list[dict]] = {}
    for item in items or []:
        when = str(item.get("때") or "").strip()
        if item.get("유형") == "확인" and "상시" in when:
            key = (2, "상시 확인")
        elif item.get("유형") == "확인":
            key = (0, "이번 주 확인")
        else:
            iso = str(item.get("날짜") or "")
            match = re.search(r"(\d{1,2})/(\d{1,2})\s*\([월화수목금토일]\)", when)
            sort_date = iso or (f"9999-{int(match.group(1)):02d}-{int(match.group(2)):02d}"
                                if match else "9999-99-99")
            label = re.sub(r"\s+\d{2}:\d{2}$", "", when) or "날짜 일정"
            key = (1, f"{sort_date}|{label}")
        shown = dict(item)
        clock = re.search(r"(\d{2}:\d{2})$", when)
        shown["표시때"] = clock.group(1) if item.get("유형") == "일정" and clock else (
            "" if item.get("유형") == "일정" else when)
        buckets.setdefault(key, []).append(shown)
    groups = []
    for key in sorted(buckets):
        label = key[1].split("|", 1)[-1]
        groups.append({"라벨": label, "항목": buckets[key]})
    return groups


YT_NOTICE = {
    # 수집기의 내부 상태는 로그에서만 확인하고 독자에게는 확보한 정보만 보여준다.
    "키없음": "",
    "꺼짐": "",
    "새영상없음": "",
}


def _youtube_footnote(data: dict, videos: list[dict]) -> str:
    """ETF 영상이 하나도 없을 때 그 사실을 밝힌다."""
    if not videos:
        return ""
    if any(v.get("ETF관련") for v in videos):
        return ""
    return ("최근 이틀 0시 이후 등록 채널이 ETF를 정면으로 다룬 영상이 없어, "
            "일반 경제 영상만 담았습니다. 오늘 ETF 주제는 선점되지 않았다는 뜻입니다.")


def _youtube_notice(data: dict) -> str:
    """영상이 없을 때 '왜 없는지'를 밝힌다.

    섹션을 조용히 숨기면 읽는 사람은 '오늘은 경쟁 채널이 잠잠했나 보다'로
    오해한다. 수집을 못 한 것과 수집했는데 없는 것은 다른 얘기다.
    """
    yt = data.get("유튜브") or {}
    if yt.get("급상승"):
        return ""
    return YT_NOTICE.get(str(yt.get("상태") or ""), "")


BASE_SOURCES = [
    {"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"},
    {"이름": "Yahoo Finance", "url": "https://finance.yahoo.com"},
]


def _sources(data: dict, ai: dict) -> list[dict]:
    """카드에 달린 출처 + 뉴스 소스를 모아 중복 제거."""
    seen: dict[str, dict] = {}

    def put(name: str, url: str = "", date: str = ""):
        name = (name or "").strip()
        if not name or name in seen:
            return
        seen[name] = {"이름": name, "url": url, "날짜": date}

    for s in BASE_SOURCES:
        put(s["이름"], s["url"])
    for key in ("시장브리핑", "etf_레이더"):
        for c in ai.get(key) or []:
            for s in c.get("출처") or []:
                if isinstance(s, dict):
                    put(s.get("이름", ""), s.get("url", ""), s.get("날짜", ""))
    for s in (ai.get("시장국면") or {}).get("출처", []):
        if isinstance(s, dict):
            put(s.get("이름", ""), s.get("url", ""), s.get("날짜", ""))
    for c in ai.get("체크포인트") or []:
        s = c.get("출처") if isinstance(c, dict) else None
        if isinstance(s, dict):
            put(s.get("이름", ""), s.get("url", ""), s.get("날짜", ""))
    # 실제 카드에서 인용한 기사만 출처로 표시한다. 단순히 RSS에서 수집했다는
    # 이유로 매체명을 나열하면 독자가 해당 문장의 근거로 오해할 수 있다.
    if (data.get("유튜브") or {}).get("급상승"):
        put("YouTube Data API", "https://www.youtube.com")
    return list(seen.values())



def _daily_etf_news(data: dict, limit: int = 6) -> list[dict]:
    """전 거래일의 한국어 ETF 기사 중 서로 다른 주제의 대표 기사만 고른다."""
    import re

    target = str(data.get("국내기준일_ISO") or "")
    terms = ("ETF", "상장지수", "자산운용", "펀드", "레버리지", "인버스",
             "분배금", "커버드콜", "TDF", "연금", "ISA")
    source_rank = (
        "연합뉴스", "한국경제", "매일경제", "머니투데이", "서울경제", "아시아경제",
        "뉴시스", "뉴스핌", "파이낸셜뉴스", "한국경제TV", "아주경제",
    )
    topic_rules = (
        ("시간외거래", r"애프터마켓|퇴근길|시간외|넥스트레이드"),
        ("레버리지·인버스", r"레버리지|인버스|곱버스|\b[23]배\b"),
        ("연금·절세계좌", r"TDF|퇴직연금|연금저축|\bISA\b|개인형IRP|\bIRP\b"),
        ("배당·분배금", r"월배당|배당|분배금|커버드콜"),
        ("채권·금리", r"채권|국채|금리"),
        ("신규상장·상품", r"신규\s*상장|출시|상장\s*예정"),
        ("자금흐름·수급", r"자금\s*(유입|이탈|이동)|순매수|수급"),
        ("수익률·테마", r"수익률|급등|급락|강세|약세|테마"),
        ("규제·제도", r"규제|제도|과세|세제|당국"),
    )
    stop = {"etf", "상장지수", "펀드", "관련", "국내", "투자", "시장", "전망",
            "올해", "이번", "지난", "대한", "통해"}

    def korean(title: str) -> bool:
        hangul = len(re.findall(r"[가-힣]", title))
        letters = len(re.findall(r"[가-힣A-Za-z]", title))
        return hangul >= 2 and (letters == 0 or hangul / letters >= 0.25)

    def topic(title: str) -> str:
        for name, pattern in topic_rules:
            if re.search(pattern, title, re.I):
                return name
        tokens = [x for x in re.findall(r"[가-힣]{2,}|[A-Za-z0-9]+", title.lower())
                  if x not in stop]
        return "기타:" + "|".join(tokens[:2])

    def tokens(title: str) -> set[str]:
        return {x for x in re.findall(r"[가-힣]{2,}|[a-z0-9]+", title.lower())
                if x not in stop}

    def rank(item: dict) -> int:
        src = str(item.get("출처") or "")
        return next((i for i, name in enumerate(source_rank) if name in src), len(source_rank))

    candidates = []
    for group, items in (data.get("뉴스") or {}).items():
        for item in items or []:
            title = str(item.get("제목") or "").strip()
            if (not title or not item.get("링크")
                    or str(item.get("날짜") or "")[:10] != target
                    or not korean(title)
                    or not any(term.lower() in title.lower() for term in terms)):
                continue
            candidates.append((rank(item), title, group, item))

    # 같은 사건이면 주요 매체 기사를 먼저 남긴다.
    candidates.sort(key=lambda row: row[0])
    used_topics: set[str] = set()
    used_tokens: list[set[str]] = []
    rows = []
    for _, title, group, item in candidates:
        cluster = topic(title)
        words = tokens(title)
        duplicate = cluster in used_topics
        if not duplicate and words:
            duplicate = any(len(words & prev) / max(1, min(len(words), len(prev))) >= 0.55
                            for prev in used_tokens if prev)
        if duplicate:
            continue
        used_topics.add(cluster)
        used_tokens.append(words)
        rows.append({
            "제목": title, "링크": item.get("링크"), "출처": item.get("출처", ""),
            "날짜": _md(item.get("날짜", "")), "분류": group,
        })
        if len(rows) >= limit:
            break
    return rows


def build_context(cfg: dict, data: dict[str, Any], ai: dict[str, Any], mode: str) -> dict:
    br = cfg.get("브리핑") or {}
    videos = _youtube(data, ai)
    competitors = [v for v in videos if v.get("채널유형") != "운용사 공식"][:4]
    # 2분 미만 운용사 영상은 상품 광고·숏폼 성격이라 작가용 리서치에서 제외한다.
    official = [v for v in videos if v.get("채널유형") == "운용사 공식"
                and (v.get("길이초") is None or int(v.get("길이초")) >= 120)][:1]
    suffix = SUFFIX.get(mode, "")
    stamp = now_kst().strftime("%Y-%m-%d")
    base = str(br.get("사이트_주소") or "").rstrip("/")
    share_url = f"{base}/{stamp}{suffix}.html" if base else ""
    kakao_text = str(((ai or {}).get("카톡") or {}).get("1") or "").strip()
    return {
        "제목": data.get("브리핑제목") or br.get("제목", "아침 경제·ETF 브리핑"),
        "날짜표시": data.get("날짜표시", ""),
        "기준설명": data.get("기준설명", ""),
        "기준일태그": data.get("기준일태그", ""),
        "브리핑역할": data.get("브리핑역할", ""),
        "수집상태": data.get("수집상태", {}),
        "한눈에": _glance(data),
        "주간대표흐름": _weekly_table(data, mode),
        "월요일요약": _monday_summary(data, ai or {}),
        "체크포인트그룹": _checkpoint_groups((ai or {}).get("체크포인트") or []),
        "ETF흐름판": (data.get("ETF_후보") or {}).get("흐름판") or {},
        "전일ETF뉴스": _daily_etf_news(data),
        "유튜브영상": competitors,
        "유튜브운용사": official,
        "유튜브안내": _youtube_notice(data),
        "유튜브각주": _youtube_footnote(data, competitors),
        "출처목록": _sources(data, ai),
        "레이더_최대": (cfg.get("ETF_레이더") or {}).get("최대_항목수", 3),
        "ai": ai or {},
        "모드": mode,
        "생성시각": now_kst().strftime("%Y-%m-%d %H:%M KST"),
        # JavaScript 키는 브라우저에 공개되는 키다. REST API 키와 혼용하지 않는다.
        "카카오JS키": config_env("KAKAO_JAVASCRIPT_KEY", "") or "",
        "공유주소": share_url,
        "공유본문": kakao_text,
    }


def validate_daily(cfg: dict, data: dict, ai: dict) -> list[str]:
    """불완전한 브리핑이 기존 정상 HTML을 덮어쓰지 않도록 최종 계약을 검사한다."""
    errors = []
    if len(ai.get("top5") or []) < 5:
        errors.append("TOP5 5개 미만")
    markets = {x.get("시장") for x in (ai.get("시장브리핑") or [])}
    if not {"국내", "미국"}.issubset(markets):
        errors.append("국내·미국 시장브리핑 누락")
    for card in (ai.get("시장브리핑") or []):
        if card.get("시장") not in ("국내", "미국"):
            continue
        missing = [key for key in ("결과", "원인", "ETF연결")
                   if not str(card.get(key) or "").strip()]
        if card.get("자동생성") or missing:
            errors.append(f"{card.get('시장')} 시장브리핑 미완성")
    flowboard = ((data.get("ETF_후보") or {}).get("흐름판") or {})
    if not (ai.get("etf_레이더") or []) and not (flowboard.get("상승") or flowboard.get("하락")):
        errors.append("ETF 레이더 누락")
    if not (ai.get("콘텐츠후보") or []):
        errors.append("ETF 아는형 콘텐츠 후보 누락")
    if not (ai.get("오늘의개념") or {}).get("용어"):
        errors.append("오늘의 개념 누락")
    if not (ai.get("체크포인트") or []):
        errors.append("체크포인트·일정 누락")
    videos = _youtube(data, ai)
    if (data.get("유튜브") or {}).get("급상승"):
        if not videos:
            errors.append("경쟁 채널 영상 누락")
        elif any(not v.get("관련성") for v in videos):
            errors.append("경쟁 채널 관련성 분석 누락")
    kakao = str((ai.get("카톡") or {}).get("1") or "")
    if not all(f"{i}." in kakao for i in range(1, 6)):
        errors.append("카카오 TOP1~5 누락")
    return errors


def flowboard_errors(data: dict) -> list[str]:
    """깨진 종목명·비현실적 수익률이 공개 HTML로 나가기 전에 잡는다."""
    flow = ((data.get("ETF_후보") or {}).get("흐름판") or {})
    errors = []
    weekly = flow.get("기간") == "주간"
    for group in ("상승", "하락", "고변동상품"):
        limit = 100.0 if weekly or group == "고변동상품" else 40.0
        for row in flow.get(group) or []:
            name = str(row.get("이름") or "")
            try:
                rate = float(row.get("등락률"))
            except (TypeError, ValueError):
                rate = float("inf")
            if not name or len(name) > 100 or "dtype:" in name or "\n" in name:
                errors.append(f"{group} 종목명 오류: {name[:30] or '(빈 이름)'}")
            if not math.isfinite(rate) or abs(rate) > limit:
                errors.append(f"{group} 수익률 오류: {name[:30]} {row.get('등락률')}")
    return errors


def build_handoff_context(cfg: dict, data: dict, ai: dict) -> dict:
    return {
        "날짜표시": data.get("날짜표시", ""),
        "수집범위": data.get("수집범위", "이번 주"),
        "출처목록": _sources(data, ai),
        "ai": ai or {},
        "생성시각": now_kst().strftime("%Y-%m-%d %H:%M KST"),
    }


SUFFIX = {"daily": "", "weekly": "-weekly", "thursday": "-handoff"}
TEMPLATE = {"daily": "brief.html.j2", "weekly": "brief.html.j2",
            "thursday": "handoff.html.j2"}


def _available_update_path(stamp: str) -> Path:
    """같은 날 재실행 결과가 기존 일간본을 덮어쓰지 않도록 별도 이름을 만든다."""
    clock = now_kst().strftime("%H%M")
    candidate = DOCS / f"{stamp}-update-{clock}.html"
    seq = 2
    while candidate.exists():
        candidate = DOCS / f"{stamp}-update-{clock}-{seq}.html"
        seq += 1
    return candidate


def render(cfg: dict, data: dict, ai: dict, mode: str = "daily",
           replace_existing: bool = False) -> tuple[Path, str]:
    DOCS.mkdir(exist_ok=True)
    stamp = now_kst().strftime("%Y-%m-%d")
    canonical = DOCS / f"{stamp}{SUFFIX.get(mode, '')}.html"
    protected_update = mode == "daily" and canonical.exists() and not replace_existing
    out = _available_update_path(stamp) if protected_update else canonical

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ctx = (build_handoff_context(cfg, data, ai) if mode == "thursday"
           else build_context(cfg, data, ai, mode))
    base = (cfg.get("브리핑") or {}).get("사이트_주소", "").rstrip("/")
    if mode != "thursday":
        # 재실행 테스트본의 공유 버튼도 실제 테스트본 주소를 가리켜야 한다.
        ctx["공유주소"] = f"{base}/{out.name}" if base else out.name
    html = env.get_template(TEMPLATE.get(mode, "brief.html.j2")).render(**ctx)

    out.write_text(html, encoding="utf-8")
    if mode != "thursday" and not protected_update:
        # 전달문·테스트본은 '최근 공식 브리핑'을 덮어쓰지 않는다.
        (DOCS / "latest.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    url = f"{base}/{out.name}" if base else out.name
    if not protected_update:
        _update_index(cfg)
    else:
        log.warning("기존 공식 일간본 보호: %s → 테스트본 %s", canonical.name, out.name)
    return out, url


def _update_index(cfg: dict) -> None:
    files = sorted(DOCS.glob("20*.html"), key=lambda p: p.name, reverse=True)
    rows = "\n".join(
        '<li><a href="{}">{}</a></li>'.format(
            p.name,
            p.stem.replace("-weekly", " (주간)").replace("-handoff", " (ETF 처방전 전달문)"),
        )
        for p in files[:400]
    )
    title = (cfg.get("브리핑") or {}).get("제목", "아침 경제·ETF 브리핑")
    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} 아카이브</title>
<style>
:root{{--bg:#0e1116;--surface:#161b22;--line:#242c3a;--ink:#e8edf5;--ink3:#6f7d92;--accent:#7c6cf0}}
@media(prefers-color-scheme:light){{:root{{--bg:#f6f7f9;--surface:#fff;--line:#e2e6ee;--ink:#161b22;--ink3:#8792a4}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif}}
.w{{max-width:640px;margin:0 auto;padding:40px 20px}}
h1{{font-size:22px;margin:0 0 6px}} p.s{{color:var(--ink3);font-size:13px;margin:0 0 22px}}
ul{{list-style:none;padding:0;margin:0}} li{{border-bottom:1px solid var(--line)}}
li a{{display:block;padding:13px 4px;color:var(--ink);text-decoration:none;font-size:14.5px}}
li a:hover{{color:var(--accent)}}
.latest{{display:inline-block;margin-bottom:20px;padding:10px 18px;background:var(--accent);
color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px}}
</style></head><body><div class="w">
<h1>{title}</h1><p class="s">매일 오전 7시 자동 생성</p>
<a class="latest" href="latest.html">가장 최근 브리핑 보기 →</a>
<ul>{rows}</ul></div></body></html>"""
    (DOCS / "index.html").write_text(html, encoding="utf-8")


def dump_debug(data: dict, ai: dict) -> None:
    try:
        (ROOT / "debug").mkdir(exist_ok=True)
        stamp = now_kst().strftime("%Y%m%d")
        (ROOT / "debug" / f"{stamp}-data.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (ROOT / "debug" / f"{stamp}-ai.json").write_text(
            json.dumps(ai, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("디버그 저장 실패: %s", e)
