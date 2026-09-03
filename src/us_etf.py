"""미국 상장 ETF 흐름판 수집.

NASDAQ의 ETF 전체 목록에서 일반형과 레버리지·인버스를 먼저 분리한 뒤
극단값 후보의 종가·거래량을 NASDAQ 종목 정보로 다시 확인한다. 전체 목록이
막히면 대표 ETF 목록으로 축소해 복구하되 유동성을 확인하지 못한 상품은
공개 순위에 넣지 않는다.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

NASDAQ_ETF_URL = "https://api.nasdaq.com/api/screener/etf?download=true"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

# 전체 목록 호출 실패 시에도 대표 지수·업종·원자재·채권 ETF를 확인한다.
FALLBACK_UNIVERSE = [
    "SPY", "IVV", "VOO", "QQQ", "QQQM", "DIA", "IWM", "VTI", "RSP",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "SOXX", "SMH", "IGV", "SKYY", "CIBR", "HACK", "BOTZ", "ROBO", "ARKK", "ARKG",
    "IBB", "XBI", "IHI", "ITA", "PPA", "PAVE", "ICLN", "TAN", "URA", "URNM",
    "KWEB", "MCHI", "EWJ", "EWY", "EWT", "INDA", "EEM", "VEA", "VWO",
    "GLD", "IAU", "SLV", "USO", "UNG", "DBA", "COPX", "GDX", "GDXJ",
    "TLT", "IEF", "SHY", "SGOV", "BIL", "LQD", "HYG", "TIP", "MUB",
    "VNQ", "SCHD", "VIG", "JEPI", "JEPQ", "BITO", "IBIT", "FBTC", "ETHA",
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU", "LABU", "LABD", "NUGT", "DUST",
]

_LEVERAGED = re.compile(
    r"(?:\b[23]x\b|ultra(?:pro|short)?|leveraged|inverse|bear\b|short\b|"
    r"daily target|daily .* bull|daily .* bear)", re.I)

_LEGAL_PREFIX = re.compile(
    r"^(?:Amplify ETF Trust|Tidal Trust II|Investment Managers Series Trust II|"
    r"Simplify Exchange Traded Funds|GraniteShares ETF Trust)\s+", re.I)


def _number(value: Any) -> float | None:
    text = str(value or "").replace("$", "").replace(",", "").replace("%", "").strip()
    if not text or text in {"--", "N/A", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _theme_key(name: str, ticker: str) -> str:
    """같은 지수·테마 상품이 TOP3를 독식하지 않게 느슨한 대표 키를 만든다."""
    text = name.lower()
    aliases = (
        ("s&p 500", "sp500"), ("nasdaq-100", "nasdaq100"), ("nasdaq 100", "nasdaq100"),
        ("semiconductor", "semiconductor"), ("bitcoin", "bitcoin"), ("ethereum", "ethereum"),
        ("gold", "gold"), ("silver", "silver"), ("uranium", "uranium"),
        ("clean energy", "clean-energy"), ("cybersecurity", "cybersecurity"),
        ("biotech", "biotech"), ("aerospace", "aerospace"), ("treasury", "treasury"),
    )
    for needle, key in aliases:
        if needle in text:
            return key
    cleaned = re.sub(
        r"\b(etf|fund|trust|shares|ishares|vanguard|spdr|invesco|proshares|direxion|"
        r"first trust|global x|wisdomtree|fidelity|index|portfolio)\b", " ", text)
    words = re.findall(r"[a-z0-9]+", cleaned)
    return "-".join(words[:4]) or ticker


def _display_name(name: str) -> str:
    """순위표에서 의미 없는 법적 신탁명 접두어를 덜어낸다."""
    return _LEGAL_PREFIX.sub("", name).strip()


def _dedupe(rows: list[dict], limit: int) -> list[dict]:
    out, used = [], set()
    for row in rows:
        key = _theme_key(str(row.get("이름") or ""), str(row.get("티커") or ""))
        if key in used:
            continue
        used.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _nasdaq_universe(timeout: int = 25) -> tuple[list[str], dict[str, str]]:
    import requests

    response = requests.get(NASDAQ_ETF_URL, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    rows = (((payload.get("data") or {}).get("data") or {}).get("rows") or [])
    parsed = []
    names = {}
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().replace("/", "-")
        change = _number(row.get("percentageChange"))
        if not ticker or change is None:
            continue
        name = str(row.get("companyName") or ticker).strip()
        names[ticker] = name
        parsed.append((ticker, change, bool(_LEVERAGED.search(f"{name} {ticker}"))))
    # 전체 극단값은 단일종목 레버리지 ETF가 대부분이므로 유형을 먼저 나눈다.
    # 일반형 상승·하락 후보를 각각 확보한 뒤 거래대금으로 실제 TOP3를 정한다.
    general = [x for x in parsed if not x[2]]
    leveraged = [x for x in parsed if x[2]]
    positive = sorted((x for x in general if x[1] > 0), key=lambda x: x[1], reverse=True)[:45]
    negative = sorted((x for x in general if x[1] < 0), key=lambda x: x[1])[:45]
    volatile = sorted(leveraged, key=lambda x: abs(x[1]), reverse=True)[:20]
    tickers = list(dict.fromkeys([x[0] for x in positive + negative + volatile]))
    return tickers, names


def _fetch_quotes(tickers: list[str], names: dict[str, str]) -> list[dict]:
    import requests

    def fetch(symbol: str) -> dict | None:
        try:
            response = requests.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/info",
                params={"assetclass": "etf"}, headers=HEADERS, timeout=15)
            response.raise_for_status()
            data = response.json().get("data") or {}
            quote = data.get("primaryData") or {}
            close = _number(quote.get("lastSalePrice"))
            pct = _number(quote.get("percentageChange"))
            volume = _number(quote.get("volume"))
            if close is None or pct is None or volume is None:
                return None
            if close <= 0 or volume <= 0 or abs(pct) > 60:
                return None
            name = _display_name(names.get(symbol) or str(data.get("companyName") or symbol))
            leveraged = bool(_LEVERAGED.search(f"{name} {symbol}"))
            raw_day = str(quote.get("lastTradeTimestamp") or "")
            try:
                basis_day = datetime.strptime(raw_day, "%b %d, %Y").date().isoformat()
            except ValueError:
                basis_day = ""
            return {
                "티커": symbol, "이름": name, "종가": round(close, 4),
                "등락률": round(pct, 2), "거래대금_달러": round(close * volume, 2),
                "거래량": int(volume), "기준일": basis_day,
                "유형": "레버리지·인버스" if leveraged else "일반형",
            }
        except Exception as exc:  # noqa: BLE001
            log.debug("미국 ETF %s 조회 실패: %s", symbol, exc)
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        return [row for row in pool.map(fetch, tickers) if row]


def collect_us_etf_flow(cfg: dict, mode: str = "daily") -> dict:
    rule = cfg.get("ETF_레이더") or {}
    min_turnover = float(rule.get("미국_흐름판_최소거래대금_달러", 10_000_000))
    rank_n = int(rule.get("흐름판_상하위개수", 3))
    source = "NASDAQ ETF Screener"
    try:
        tickers, names = _nasdaq_universe()
        if not tickers:
            raise ValueError("NASDAQ ETF 후보 0건")
    except Exception as exc:  # noqa: BLE001
        log.warning("미국 ETF 전체 목록 실패, 대표 목록으로 복구: %s", exc)
        tickers, names = list(FALLBACK_UNIVERSE), {x: x for x in FALLBACK_UNIVERSE}
        source = "NASDAQ 대표 ETF 복구 목록"

    rows = [x for x in _fetch_quotes(tickers, names)
            if float(x.get("거래대금_달러") or 0) >= min_turnover]
    general = [x for x in rows if x["유형"] == "일반형"]
    leveraged = [x for x in rows if x["유형"] == "레버리지·인버스"]
    gainers = sorted((x for x in general if x["등락률"] > 0),
                     key=lambda x: x["등락률"], reverse=True)
    losers = sorted((x for x in general if x["등락률"] < 0), key=lambda x: x["등락률"])
    dates = sorted({x["기준일"] for x in rows if x.get("기준일")})
    day = dates[-1] if dates else ""
    board = {
        "국가": "미국", "기간": "주간" if mode == "weekly" else "전일",
        "기준일": day[5:].replace("-", "/") if day else "",
        "상승": _dedupe(gainers, rank_n), "하락": _dedupe(losers, rank_n),
        "고변동상품": _dedupe(sorted(leveraged, key=lambda x: abs(x["등락률"]), reverse=True), 2),
        "최소거래대금_달러": int(min_turnover),
        "필터설명": f"거래대금 {min_turnover / 10_000_000:g}천만달러 이상 일반형 · 테마 중복 제외",
        "출처": source,
        "진단": {"후보": len(tickers), "유동성필터통과": len(rows)},
    }
    log.info("미국 ETF 흐름판 상승 %d개·하락 %d개 (거래대금 통과 %d개)",
             len(board["상승"]), len(board["하락"]), len(rows))
    return board
