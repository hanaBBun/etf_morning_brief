"""글로벌 시세 수집 (yfinance)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 시세 스냅샷
#
# ★ 정합성 원칙 ★
# 레벨·등락폭·등락률은 반드시 같은 timestamp의 봉에서 계산한다.
# 장중 데이터와 확정 종가를 절대 섞지 않는다.
# 각 값에는 그 값이 어느 시점의 것인지(기준일 / 확정 여부)를 함께 붙여
# 렌더링·AI 단계에서 혼용을 막는다.
# ─────────────────────────────────────────────

# 그룹별 정규장 마감 시각. (거래소 현지시간 기준 시, 분)
SESSION_CLOSE = {
    "국내": ("Asia/Seoul", 15, 30),
    "해외지수": ("America/New_York", 16, 0),
    "금리": ("America/New_York", 16, 0),
}
CONTINUOUS = {"원자재", "변동성"}  # 사실상 24시간 — '스냅샷'으로 표기


def _is_settled(group: str, bar_date: str) -> bool:
    """이 봉이 확정 종가인지 판정. 마감 시각을 지난 뒤여야 확정."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    tzname, hh, mm = SESSION_CLOSE.get(group, ("America/New_York", 16, 0))
    tz = ZoneInfo(tzname)
    try:
        d = datetime.strptime(bar_date, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return False
    close_at = datetime.combine(d, time(hh, mm), tzinfo=tz)
    # 마감 후 15분 여유를 둔다 (정산·지연 반영)
    return datetime.now(tz) >= close_at + timedelta(minutes=15)


def _quote(ticker: str, group: str = "") -> dict[str, Any] | None:
    """직전 2개 일봉에서 레벨·등락폭·등락률을 한 세트로 계산."""
    import yfinance as yf

    try:
        hist = yf.Ticker(ticker).history(period="10d", interval="1d")
        if hist is None or len(hist) < 2:
            log.warning("%s: 데이터 부족", ticker)
            return None
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 2:
            return None

        last_row, prev_row = hist.iloc[-1], hist.iloc[-2]
        last, prev = float(last_row["Close"]), float(prev_row["Close"])
        bar_date = str(hist.index[-1].date())
        prev_date = str(hist.index[-2].date())

        settled = True if group in CONTINUOUS else _is_settled(group, bar_date)
        return {
            "종가": last,
            "전일": prev,
            "등락": last - prev,
            "등락률": (last - prev) / prev * 100 if prev else None,
            "변화bp": (last - prev) * 100 if group == "금리" else None,
            "고가": float(last_row.get("High", last)),
            "저가": float(last_row.get("Low", last)),
            "기준일": bar_date,
            "비교일": prev_date,
            "확정": bool(settled),
            "상태": ("스냅샷" if group in CONTINUOUS
                     else ("마감" if settled else "장중")),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("%s 조회 실패: %s", ticker, e)
        return None


def collect_indicators(cfg: dict) -> dict[str, list[dict]]:
    """config.yaml의 지표 목록을 그룹별로 수집.

    같은 그룹 안에서 기준일이 섞이면 로그로 경고한다.
    (렌더링 단계에서 각 값에 기준일을 개별 표기해 혼동을 막는다.)
    """
    out: dict[str, list[dict]] = {}
    for group, items in (cfg.get("지표") or {}).items():
        rows = []
        for item in items:
            q = _quote(item["티커"], group)
            if q:
                rows.append({"이름": item["이름"], "티커": item["티커"], "그룹": group, **q})
            else:
                rows.append({"이름": item["이름"], "티커": item["티커"],
                             "그룹": group, "종가": None})
        dates = {r.get("기준일") for r in rows if r.get("종가") is not None}
        if len(dates) > 1:
            log.warning("[%s] 기준일이 섞였습니다: %s — 각 항목에 날짜를 개별 표기합니다",
                        group, sorted(d for d in dates if d))
        out[group] = rows
    return out


def collect_us_movers(cfg: dict, universe: list[str] | None = None) -> list[dict]:
    """미국 개별 종목 중 편성 기준을 넘긴 것만 반환.

    universe 를 주지 않으면 대표 종목 + ETF 관련주 기본 목록을 사용한다.
    시가총액 제한은 두지 않고, 등락률과 거래대금(달러)으로만 거른다.
    """
    import yfinance as yf

    rule = cfg.get("미국종목_편성기준", {})
    thr = float(rule.get("등락률_임계치", 5.0))
    min_cap = float(rule.get("최소_시가총액_억달러", 20)) * 1e8
    cap_n = int(rule.get("최대_표시개수", 10))

    universe = universe or DEFAULT_US_UNIVERSE
    movers: list[dict] = []
    for sym in universe:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5d", interval="1d")
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            pct = (last - prev) / prev * 100 if prev else 0.0
            if abs(pct) < thr:
                continue
            info = getattr(t, "fast_info", {}) or {}
            cap = float(info.get("market_cap") or 0)
            if cap and cap < min_cap:
                continue
            movers.append({
                "티커": sym,
                "이름": US_NAMES.get(sym, sym),
                "업종": US_SECTORS.get(sym, ""),
                "종가": last,
                "등락률": pct,
                "시가총액": cap or None,
            })
        except Exception as e:  # noqa: BLE001
            log.debug("%s 스킵: %s", sym, e)
    movers.sort(key=lambda r: abs(r["등락률"]), reverse=True)
    return movers[:cap_n]


# 국내 ETF·테마와 연결점이 있는 미국 종목 위주 유니버스.
DEFAULT_US_UNIVERSE = [
    "NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "ASML", "AMAT", "LRCX", "KLAC",
    "WOLF", "ON", "MPWR", "FN", "COHR", "ANET", "CRWV", "VRT", "SMCI", "DELL",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NFLX", "ORCL", "CRM", "PLTR",
    "XOM", "CVX", "COP", "SLB", "OXY", "TRGP", "LNG", "MPC", "VLO",
    "JPM", "BAC", "GS", "BRK-B", "V", "MA",
    "LLY", "UNH", "JNJ", "PFE", "MRNA", "BIO",
    "BA", "DAL", "UAL", "CAT", "DE", "GEV", "ETN",
    "COIN", "MSTR", "HOOD", "RBLX", "ULTA", "INTU", "NOW", "SNOW", "DDOG",
]

US_NAMES = {
    "NVDA": "엔비디아", "AMD": "AMD", "AVGO": "브로드컴", "MU": "마이크론",
    "INTC": "인텔", "TSM": "TSMC", "ASML": "ASML", "AMAT": "어플라이드머티어리얼즈",
    "LRCX": "램리서치", "KLAC": "KLA", "WOLF": "울프스피드", "ON": "온세미컨덕터",
    "MPWR": "모놀리식파워", "FN": "파브리넷", "COHR": "코히어런트", "ANET": "아리스타네트웍스",
    "CRWV": "코어위브", "VRT": "버티브", "SMCI": "슈퍼마이크로", "DELL": "델",
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳", "AMZN": "아마존",
    "META": "메타", "TSLA": "테슬라", "NFLX": "넷플릭스", "ORCL": "오라클",
    "CRM": "세일즈포스", "PLTR": "팔란티어", "XOM": "엑슨모빌", "CVX": "셰브론",
    "COP": "코노코필립스", "SLB": "슐럼버거", "OXY": "옥시덴탈", "TRGP": "타가리소스",
    "LNG": "셰니에르에너지", "MPC": "마라톤페트롤리엄", "VLO": "발레로",
    "JPM": "JP모건", "BAC": "뱅크오브아메리카", "GS": "골드만삭스",
    "BRK-B": "버크셔해서웨이", "V": "비자", "MA": "마스터카드",
    "LLY": "일라이릴리", "UNH": "유나이티드헬스", "JNJ": "존슨앤드존슨",
    "PFE": "화이자", "MRNA": "모더나", "BIO": "바이오라드",
    "BA": "보잉", "DAL": "델타항공", "UAL": "유나이티드항공", "CAT": "캐터필러",
    "DE": "디어", "GEV": "GE버노바", "ETN": "이튼",
    "COIN": "코인베이스", "MSTR": "스트래티지", "HOOD": "로빈후드",
    "RBLX": "로블록스", "ULTA": "얼타뷰티", "INTU": "인튜이트",
    "NOW": "서비스나우", "SNOW": "스노우플레이크", "DDOG": "데이터독",
}

US_SECTORS = {
    "NVDA": "AI 반도체", "AMD": "반도체", "AVGO": "반도체·네트워크",
    "MU": "메모리 반도체", "INTC": "반도체", "TSM": "반도체 파운드리",
    "ASML": "반도체 장비 (EUV)", "AMAT": "반도체 장비", "LRCX": "반도체 장비",
    "KLAC": "반도체 검사장비", "WOLF": "전력반도체 (SiC)", "ON": "전력반도체",
    "MPWR": "전력관리 반도체", "FN": "광통신 부품 위탁생산", "COHR": "광통신·레이저",
    "ANET": "데이터센터 네트워크", "CRWV": "AI GPU 클라우드", "VRT": "데이터센터 전력·냉각",
    "SMCI": "AI 서버", "DELL": "서버·PC",
    "AAPL": "스마트폰·소비자전자", "MSFT": "소프트웨어·클라우드", "GOOGL": "플랫폼·검색",
    "AMZN": "이커머스·클라우드", "META": "소셜플랫폼", "TSLA": "전기차·자율주행",
    "NFLX": "스트리밍", "ORCL": "기업 소프트웨어·클라우드", "CRM": "기업 소프트웨어",
    "PLTR": "AI 데이터 분석",
    "XOM": "정유·에너지", "CVX": "정유·에너지", "COP": "원유 탐사·생산",
    "SLB": "유전 서비스", "OXY": "원유 탐사·생산", "TRGP": "천연가스 미드스트림",
    "LNG": "LNG 수출", "MPC": "정제", "VLO": "정제",
    "JPM": "은행", "BAC": "은행", "GS": "투자은행", "BRK-B": "복합기업",
    "V": "결제", "MA": "결제",
    "LLY": "제약 (비만·당뇨)", "UNH": "건강보험", "JNJ": "제약·의료기기",
    "PFE": "제약", "MRNA": "백신·mRNA", "BIO": "바이오 실험장비",
    "BA": "항공기 제조", "DAL": "항공", "UAL": "항공", "CAT": "건설기계",
    "DE": "농기계", "GEV": "발전 설비", "ETN": "전력기기",
    "COIN": "가상자산 거래소", "MSTR": "비트코인 보유기업", "HOOD": "증권 플랫폼",
    "RBLX": "게임 플랫폼", "ULTA": "화장품 유통", "INTU": "소프트웨어·세무회계",
    "NOW": "기업 워크플로 소프트웨어", "SNOW": "데이터 클라우드", "DDOG": "모니터링 소프트웨어",
}
