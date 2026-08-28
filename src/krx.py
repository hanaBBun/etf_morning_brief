"""국내 증시 데이터 수집 (pykrx).

모든 함수는 실패해도 예외를 던지지 않고 빈 값을 돌려준다.
데이터 하나가 막혀도 브리핑 전체가 죽지 않게 하기 위함이다.
"""
from __future__ import annotations

import logging
import math
import json
import urllib.request
from datetime import timedelta
from typing import Any

from .config import ROOT, now_kst

log = logging.getLogger(__name__)


_WARNED = {"krx": False}
ETF_SNAPSHOT_CACHE = ROOT / "etf_snapshot_cache.json"
ETF_CLOSE_CACHE = ROOT / "etf_close_snapshots.json"


def krx_ready() -> bool:
    """KRX 계정이 설정돼 있는지 확인. 없으면 한 번만 안내 로그를 남긴다.

    2025년부터 KRX 정보데이터시스템이 로그인을 요구하도록 바뀌어,
    pykrx도 KRX_ID / KRX_PW 환경변수를 필요로 한다.
    """
    import os

    ok = bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))
    if not ok and not _WARNED["krx"]:
        _WARNED["krx"] = True
        log.warning(
            "KRX_ID / KRX_PW 가 설정되지 않았습니다. "
            "국내 지수는 yfinance 값으로 대체하고, "
            "투자자별 수급·주목 종목·ETF 레이더는 이번 브리핑에서 생략됩니다. "
            "data.krx.co.kr 에서 무료 회원가입 후 GitHub Secrets 에 "
            "KRX_ID / KRX_PW 를 등록하면 전부 살아납니다."
        )
    return ok


def _stock():
    from pykrx import stock
    return stock


def last_business_day(offset: int = 0) -> str:
    """가장 최근 *확정 마감* 영업일을 YYYYMMDD로.

    pykrx가 장중에도 오늘 날짜의 부분 집계를 돌려줄 수 있으므로
    15:45 KST 이전에는 오늘을 기준일로 사용하지 않는다.
    """
    s = _stock()
    now = now_kst()
    d = now.date()
    if (now.hour, now.minute) < (15, 45):
        d -= timedelta(days=1)
    day = s.get_nearest_business_day_in_a_week(date=d.strftime("%Y%m%d"), prev=True)
    for _ in range(offset):
        prev = (
            __import__("datetime").datetime.strptime(day, "%Y%m%d").date()
            - timedelta(days=1)
        )
        day = s.get_nearest_business_day_in_a_week(
            date=prev.strftime("%Y%m%d"), prev=True
        )
    return day


# ─────────────────────────────────────────────
# 지수
# ─────────────────────────────────────────────
def index_snapshot(day: str) -> list[dict]:
    """코스피·코스닥 종가와 등락률, 장중 고저."""
    s = _stock()
    out = []
    for code, name in (("1001", "코스피"), ("2001", "코스닥")):
        try:
            df = s.get_index_ohlcv(_shift(day, -10), day, code)
            if df is None or len(df) < 2:
                continue
            last, prev = df.iloc[-1], df.iloc[-2]
            close, pclose = float(last["종가"]), float(prev["종가"])
            bar_date = str(df.index[-1].date())
            # KRX는 직전 영업일 데이터만 조회하므로 항상 확정 종가다.
            out.append({
                "이름": name,
                "종가": close,
                "전일": pclose,
                "등락": close - pclose,
                "등락률": (close - pclose) / pclose * 100 if pclose else None,
                "고가": float(last["고가"]),
                "저가": float(last["저가"]),
                "고가등락률": (float(last["고가"]) - pclose) / pclose * 100 if pclose else None,
                "거래대금": float(last.get("거래대금", 0)),
                "기준일": bar_date,
                "비교일": str(df.index[-2].date()),
                "확정": True,
                "상태": "마감",
            })
        except Exception as e:  # noqa: BLE001
            log.warning("지수 %s 실패: %s", name, e)
    return out


def _shift(day: str, days: int) -> str:
    import datetime as _dt
    d = _dt.datetime.strptime(day, "%Y%m%d").date() + _dt.timedelta(days=days)
    return d.strftime("%Y%m%d")


_LEVERAGED_WORDS = ("레버리지", "인버스", "곱버스", "2X", "2배", "-2X")
_THEMES = ("코스피200", "코스닥150", "S&P500", "나스닥100", "반도체", "2차전지",
           "바이오", "화장품", "방산", "금선물", "금채굴", "골드", "은선물", "실버",
           "원유", "비트코인", "커버드콜",
           "은행", "증권", "자동차", "로봇", "AI", "조선", "전력")


def _is_leveraged_etf(name: str) -> bool:
    upper = str(name).upper()
    return any(word.upper() in upper for word in _LEVERAGED_WORDS)


def _theme_key(name: str) -> str:
    """운용사만 다른 유사 ETF가 상·하위 목록을 독식하지 않게 묶는다."""
    text = str(name).replace(" ", "").upper()
    for theme in _THEMES:
        if theme.upper() in text:
            return theme.upper()
    for brand in ("KODEX", "TIGER", "RISE", "ACE", "SOL", "PLUS", "HANARO", "KOSEF"):
        text = text.replace(brand, "")
    return text[:14]


def _dedupe_ranked(rows: list[dict], limit: int) -> list[dict]:
    out, themes = [], set()
    for row in rows:
        key = _theme_key(row.get("이름", ""))
        if key in themes:
            continue
        themes.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _naver_etf_snapshot():
    """KRX ETF 표가 비었을 때 쓰는 무료·무키 국내 ETF 시세 보조 소스."""
    try:
        req = urllib.request.Request(
            "https://finance.naver.com/api/sise/etfItemList.nhn",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"},
        )
        with urllib.request.urlopen(req, timeout=15) as response:  # noqa: S310
            raw = response.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp949")
        payload = json.loads(text)
        items = ((payload.get("result") or {}).get("etfItemList") or [])
        if not items:
            return None, {}
        import pandas as pd
        rows, names = [], {}
        for item in items:
            ticker = str(item.get("itemcode") or "")
            name = str(item.get("itemname") or ticker)
            if not ticker:
                continue
            close = float(item.get("nowVal") or 0)
            rate = float(item.get("changeRate") or 0)
            rise_fall = str(item.get("risefall") or "")
            if rise_fall in ("4", "5"):
                rate = -abs(rate)
            elif rise_fall in ("1", "2"):
                rate = abs(rate)
            volume = float(item.get("quant") or 0)
            rows.append({"티커": ticker, "종가": close, "등락률": rate,
                         "거래량": volume, "거래대금": close * volume,
                         "순자산총액": float(item.get("marketSum") or 0)})
            names[ticker] = name
        if not rows:
            return None, {}
        df = pd.DataFrame(rows).set_index("티커")
        log.warning("KRX ETF 표가 비어 네이버 금융 ETF 시세 %d건으로 대체합니다", len(df))
        return df, names
    except Exception as e:  # noqa: BLE001
        log.warning("네이버 금융 ETF 보조 시세 수집 실패: %s", e)
        return None, {}



def _load_close_cache() -> dict:
    try:
        return json.loads(ETF_CLOSE_CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _closing_etf_snapshot(day: str):
    """장 마감 뒤 저장한 정확한 거래일 ETF 전체 시세를 읽는다."""
    payload = _load_close_cache().get(str(day)) or {}
    rows = payload.get("항목") or []
    if not rows:
        return None, {}
    try:
        import pandas as pd
        df = pd.DataFrame(rows).set_index("티커")
        names = {str(x.get("티커")): str(x.get("이름") or x.get("티커")) for x in rows}
        log.info("ETF 마감 스냅샷 %s %d건 사용", day, len(df))
        return df, names
    except Exception as e:  # noqa: BLE001
        log.warning("ETF 마감 스냅샷 읽기 실패(%s): %s", day, e)
        return None, {}


def save_closing_etf_snapshot() -> str:
    """평일 장 마감 뒤 네이버 무료 시세를 날짜와 함께 고정 저장한다."""
    now = now_kst()
    if now.weekday() >= 5:
        log.info("주말에는 ETF 마감 스냅샷을 저장하지 않습니다")
        return ""
    if (now.hour, now.minute) < (15, 40):
        raise RuntimeError("ETF 마감 스냅샷은 15:40 KST 이후에만 저장할 수 있습니다")
    df, names = _naver_etf_snapshot()
    if df is None or df.empty:
        raise RuntimeError("네이버 금융 ETF 마감 시세가 비었습니다")
    if "거래대금" not in df.columns or float(df["거래대금"].max()) <= 0:
        raise RuntimeError("ETF 거래대금이 비어 마감 스냅샷으로 저장하지 않습니다")
    day = now.strftime("%Y%m%d")
    rows = []
    for tk, row in df.iterrows():
        rows.append({
            "티커": str(tk), "이름": names.get(str(tk), str(tk)),
            "종가": float(row.get("종가", 0)), "등락률": float(row.get("등락률", 0)),
            "거래량": float(row.get("거래량", 0)), "거래대금": float(row.get("거래대금", 0)),
            "순자산총액": float(row.get("순자산총액", 0)),
        })
    cache = _load_close_cache()
    cache[day] = {"저장시각": now.isoformat(), "항목": rows}
    keep = sorted(cache)[-15:]
    ETF_CLOSE_CACHE.write_text(
        json.dumps({k: cache[k] for k in keep}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log.info("ETF 마감 스냅샷 %s %d건 저장", day, len(rows))
    return day


def _snapshot_cache() -> dict:
    try:
        return json.loads(ETF_SNAPSHOT_CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_snapshot(day: str, df, names: dict) -> None:
    try:
        cache = _snapshot_cache()
        cache[day] = {str(tk): {"종가": float(r.get("종가", 0)), "이름": names.get(tk, str(tk))}
                      for tk, r in df.iterrows() if float(r.get("종가", 0)) > 0}
        keys = sorted(cache)[-12:]
        ETF_SNAPSHOT_CACHE.write_text(
            json.dumps({k: cache[k] for k in keys}, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("ETF 스냅샷 캐시 저장 실패: %s", e)


def _cached_prior_close(day: str) -> dict[str, float]:
    """주간 비교용으로 현재 기준일보다 최소 4일 앞선 가장 최근 스냅샷을 고른다."""
    import datetime as _dt
    cache = _snapshot_cache()
    current = _dt.datetime.strptime(day, "%Y%m%d").date()
    candidates = []
    for key in cache:
        try:
            d = _dt.datetime.strptime(key, "%Y%m%d").date()
            if (current - d).days >= 4:
                candidates.append(key)
        except ValueError:
            continue
    if not candidates:
        return {}
    return {tk: float(v.get("종가", 0)) for tk, v in cache[max(candidates)].items()}


# ─────────────────────────────────────────────
# 투자자별 수급
# ─────────────────────────────────────────────
# pykrx 버전에 따라 순매수 컬럼 이름이 다르다. 후보를 순서대로 찾는다.
NET_COLS = ("순매수거래대금", "순매수", "순매수대금", "거래대금_순매수")


def _net_col(df) -> str | None:
    for c in NET_COLS:
        if c in df.columns:
            return c
    # 이름이 또 바뀐 경우: '순매수'가 들어간 컬럼을 찾아본다.
    for c in df.columns:
        if "순매수" in str(c):
            return c
    return None


def investor_flow(day: str, market: str = "KOSPI") -> list[dict]:
    """개인·외국인·기관 순매수 금액(원)."""
    s = _stock()
    try:
        df = s.get_market_trading_value_by_investor(day, day, market)
        if df is None or df.empty:
            return []
        col = _net_col(df)
        if not col:
            log.warning("순매수 컬럼을 찾지 못했습니다. 실제 컬럼: %s", list(df.columns))
            return []
        want = {"개인": "개인", "외국인": "외국인", "기관합계": "기관"}
        rows = []
        for idx, label in want.items():
            if idx in df.index:
                rows.append({"주체": label, "순매수": float(df.loc[idx, col])})
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("투자자별 수급 실패: %s", e)
        return []


def net_purchase_top(day: str, investor: str, market: str = "KOSPI", n: int = 10) -> list[dict]:
    """투자자별 순매수 상위/하위 종목."""
    s = _stock()
    try:
        df = s.get_market_net_purchases_of_equities(day, day, market, investor)
        if df is None or df.empty:
            return []
        col = _net_col(df)
        if not col:
            log.warning("순매수 컬럼을 찾지 못했습니다. 실제 컬럼: %s", list(df.columns))
            return []
        df = df.sort_values(col, ascending=False)
        rows = []
        for tk, r in df.head(n).iterrows():
            rows.append({"티커": tk, "종목명": r.get("종목명", ""),
                         "순매수": float(r[col]), "구분": "순매수"})
        for tk, r in df.tail(n).iterrows():
            rows.append({"티커": tk, "종목명": r.get("종목명", ""),
                         "순매수": float(r[col]), "구분": "순매도"})
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("순매수 상위 실패(%s): %s", investor, e)
        return []


# ─────────────────────────────────────────────
# 주목할 종목 선정 (시가총액 제한 없음)
# ─────────────────────────────────────────────
def notable_stocks(day: str, cfg: dict) -> list[dict]:
    """config의 종목_편성기준을 적용해 '실린 이유'와 함께 반환."""
    s = _stock()
    rule = cfg.get("종목_편성기준", {})
    n_value = int(rule.get("거래대금_상위", 20))
    pct_thr = float(rule.get("등락률_임계치", 3.0))
    n_pct_value = int(rule.get("등락률_거래대금_상위", 100))
    n_flow = int(rule.get("수급_상위", 10))
    min_value = float(rule.get("최소_거래대금_억원", 100)) * 1e8
    cap_n = int(rule.get("최대_표시개수", 12))

    picked: dict[str, dict] = {}

    def add(ticker: str, reason: str, base: dict):
        if ticker in picked:
            if reason not in picked[ticker]["이유"]:
                picked[ticker]["이유"].append(reason)
        else:
            picked[ticker] = {**base, "이유": [reason]}

    try:
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            df = s.get_market_ohlcv(day, market=market)
            if df is not None and not df.empty:
                df = df.copy()
                df["시장"] = market
                frames.append(df)
        if not frames:
            return []
        import pandas as pd
        all_df = pd.concat(frames)
        all_df = all_df[all_df["거래대금"] >= min_value]

        names = {}
        for tk in all_df.index:
            try:
                names[tk] = s.get_market_ticker_name(tk)
            except Exception:  # noqa: BLE001
                names[tk] = tk

        def base_of(tk) -> dict:
            r = all_df.loc[tk]
            return {
                "티커": tk,
                "종목명": names.get(tk, tk),
                "시장": r["시장"],
                "종가": float(r["종가"]),
                "등락률": float(r["등락률"]),
                "거래대금": float(r["거래대금"]),
            }

        # ① 거래대금 상위
        for tk in all_df.sort_values("거래대금", ascending=False).head(n_value).index:
            add(tk, "거래대금 상위", base_of(tk))

        # ② 등락률 ±N% 이상 + 거래대금 상위 M위 이내
        value_rank = all_df.sort_values("거래대금", ascending=False).head(n_pct_value).index
        for tk in value_rank:
            pct = float(all_df.loc[tk, "등락률"])
            if abs(pct) >= pct_thr:
                add(tk, f"등락률 {pct:+.2f}%", base_of(tk))

        # ③ 외국인·기관 수급 상위
        for market in ("KOSPI", "KOSDAQ"):
            for investor, label in (("외국인", "외국인"), ("기관합계", "기관")):
                for row in net_purchase_top(day, investor, market, n_flow):
                    tk = row["티커"]
                    if tk in all_df.index:
                        add(tk, f"{label} {row['구분']} 상위", base_of(tk))

        # ④ 52주 신고가·신저가
        if rule.get("신고가_신저가_포함", True):
            for tk in list(picked.keys()):
                try:
                    hist = s.get_market_ohlcv(_shift(day, -370), day, tk)
                    if hist is None or hist.empty:
                        continue
                    close = float(hist["종가"].iloc[-1])
                    if close >= float(hist["고가"].max()) * 0.999:
                        add(tk, "52주 신고가", base_of(tk))
                    elif close <= float(hist["저가"].min()) * 1.001:
                        add(tk, "52주 신저가", base_of(tk))
                except Exception:  # noqa: BLE001
                    continue

    except Exception as e:  # noqa: BLE001
        log.warning("주목할 종목 선정 실패: %s", e)
        return []

    rows = list(picked.values())
    # 이유가 많을수록, 등락률이 클수록 위로
    rows.sort(key=lambda r: (len(r["이유"]), abs(r.get("등락률", 0))), reverse=True)
    for r in rows:
        r["이유_표시"] = " · ".join(r["이유"])
    return rows[:cap_n]


# ─────────────────────────────────────────────
# ETF 레이더
# ─────────────────────────────────────────────
def etf_radar(day: str, cfg: dict, mode: str = "daily", _force_naver: bool = False) -> dict[str, Any]:
    """ETF 뉴스 후보와 유동성 필터를 거친 수익률 흐름판을 함께 수집한다."""
    s = _stock()
    rule = cfg.get("ETF_레이더", {})
    thr_flow = float(rule.get("순매수_임계치_억원", 300)) * 1e8
    vol_mult = float(rule.get("거래량_급증_배수", 3.0))
    prev = last_business_day(1)

    result: dict[str, Any] = {
        "전일_순매수": [], "전일_순매도": [], "거래량_급증": [],
        "신규상장": [], "순자산_급증": [], "기준일": day, "흐름판": {},
        "진단": {"소스": "네이버 금융" if _force_naver else "KRX",
                 "원본": 0, "유동성필터통과": 0, "등락률유효": 0},
    }

    try:
        fallback_names = {}
        if _force_naver:
            # 과거 거래일은 반드시 그 날짜에 고정 저장한 마감본을 쓴다.
            # 장중 네이버 실시간 등락률을 전일 값으로 잘못 붙이지 않는다.
            df, fallback_names = _closing_etf_snapshot(day)
            result["진단"]["소스"] = "네이버 금융 마감 스냅샷"
            if (df is None or df.empty) and (now_kst().hour, now_kst().minute) < (9, 0):
                df, fallback_names = _naver_etf_snapshot()
                result["진단"]["소스"] = "네이버 금융 장전 시세"
        else:
            try:
                df = s.get_etf_ohlcv_by_ticker(day)
            except Exception as e:  # noqa: BLE001
                log.warning("KRX ETF 전체 시세 호출 실패: %s", e)
                df = None
            if df is None or df.empty:
                df, fallback_names = _closing_etf_snapshot(day)
                if df is not None and not df.empty:
                    result["진단"]["소스"] = "네이버 금융 마감 스냅샷"
            if (df is None or df.empty) and (now_kst().hour, now_kst().minute) < (9, 0):
                df, fallback_names = _naver_etf_snapshot()
                result["진단"]["소스"] = "네이버 금융 장전 시세"
        if df is None or df.empty:
            log.warning("ETF %s 확정 마감 시세가 없어 흐름판을 생성하지 못했습니다", day)
            result["진단"]["오류"] = "확정 마감 시세 없음"
            return result
        result["진단"]["원본"] = len(df)

        names = dict(fallback_names)
        for tk in df.index:
            if tk in names:
                continue
            try:
                names[tk] = s.get_etf_ticker_name(tk)
            except Exception:  # noqa: BLE001
                names[tk] = tk
        _save_snapshot(day, df, names)

        # 수익률 흐름판: 거래대금이 충분한 ETF만, 일반형과 고변동 상품을 분리한다.
        min_turnover = float(rule.get("흐름판_최소거래대금_억원", 50)) * 1e8
        rank_n = int(rule.get("흐름판_상하위개수", 3))
        ranked = df.copy()
        if "거래대금" in ranked.columns:
            ranked = ranked[ranked["거래대금"] >= min_turnover]
        filter_text = f"거래대금 {int(min_turnover / 1e8)}억원 이상 일반형 · 테마 중복 제외"
        # 거래대금이 없는 장전 실시간 값은 50억원 기준을 검증할 수 없으므로
        # 순자산으로 대신 채우지 않는다. 마감 스냅샷 수집 실패로 운영자에게 알린다.
        result["진단"]["유동성필터통과"] = len(ranked)
        period = "전일"
        rate_col = "등락률"
        if mode == "weekly":
            period = "주간"
            try:
                prior_guess = _shift(day, -7)
                prior_day = s.get_nearest_business_day_in_a_week(date=prior_guess, prev=True)
                prior_df = s.get_etf_ohlcv_by_ticker(prior_day)
                if prior_df is not None and not prior_df.empty:
                    prior_close = prior_df["종가"].rename("직전주종가")
                    ranked = ranked.join(prior_close, how="inner")
                    ranked["주간등락률"] = (
                        (ranked["종가"] - ranked["직전주종가"]) / ranked["직전주종가"] * 100
                    )
                    rate_col = "주간등락률"
                else:
                    prior_map = _cached_prior_close(day)
                    if prior_map:
                        ranked["직전주종가"] = [prior_map.get(str(tk)) for tk in ranked.index]
                        ranked["주간등락률"] = (
                            (ranked["종가"] - ranked["직전주종가"]) / ranked["직전주종가"] * 100
                        )
                        rate_col = "주간등락률"
            except Exception as e:  # noqa: BLE001
                log.debug("ETF 주간 수익률 계산 실패, 전일 등락률 사용: %s", e)
                prior_map = _cached_prior_close(day)
                if prior_map:
                    ranked["직전주종가"] = [prior_map.get(str(tk)) for tk in ranked.index]
                    ranked["주간등락률"] = (
                        (ranked["종가"] - ranked["직전주종가"]) / ranked["직전주종가"] * 100
                    )
                    rate_col = "주간등락률"

        perf_rows = []
        for tk, row in ranked.iterrows():
            rate = row.get(rate_col)
            if rate is None or not math.isfinite(float(rate)):
                continue
            perf_rows.append({
                "티커": tk, "이름": names.get(tk, tk), "등락률": round(float(rate), 2),
                "거래대금": float(row.get("거래대금", 0)),
                "유형": "레버리지·인버스" if _is_leveraged_etf(names.get(tk, tk)) else "일반형",
            })
        general = [x for x in perf_rows if x["유형"] == "일반형"]
        leveraged = [x for x in perf_rows if x["유형"] == "레버리지·인버스"]
        result["진단"]["등락률유효"] = len(perf_rows)
        result["흐름판"] = {
            "기간": period, "기준일": f"{day[4:6]}/{day[6:8]}",
            "상승": _dedupe_ranked(sorted(general, key=lambda x: x["등락률"], reverse=True), rank_n),
            "하락": _dedupe_ranked(sorted(general, key=lambda x: x["등락률"]), rank_n),
            "고변동상품": _dedupe_ranked(
                sorted(leveraged, key=lambda x: abs(x["등락률"]), reverse=True), 2),
            "최소거래대금_억원": int(min_turnover / 1e8),
            "필터설명": filter_text,
            "출처": result["진단"].get("소스") or "KRX",
        }

        # 거래대금 기준 상위/하위 (순매수 데이터가 없으므로 거래대금·등락으로 대체)
        if "거래대금" in df.columns:
            top = df.sort_values("거래대금", ascending=False).head(15)
            for tk, r in top.iterrows():
                if float(r["거래대금"]) < thr_flow:
                    continue
                item = {
                    "티커": tk, "이름": names.get(tk, tk),
                    "종가": float(r.get("종가", 0)),
                    "등락률": float(r.get("등락률", 0)),
                    "거래대금": float(r["거래대금"]),
                    "순자산": float(r.get("순자산총액", 0) or 0),
                }
                (result["전일_순매수"] if item["등락률"] >= 0 else result["전일_순매도"]).append(item)

        # 거래량 급증 (20일 평균 대비)
        try:
            base = _shift(day, -40)
            for tk in df.sort_values("거래대금", ascending=False).head(60).index:
                h = s.get_etf_ohlcv_by_date(base, day, tk)
                if h is None or len(h) < 21:
                    continue
                today_v = float(h["거래량"].iloc[-1])
                avg20 = float(h["거래량"].iloc[-21:-1].mean())
                if avg20 > 0 and today_v / avg20 >= vol_mult:
                    result["거래량_급증"].append({
                        "티커": tk, "이름": names.get(tk, tk),
                        "배수": round(today_v / avg20, 1),
                        "등락률": float(df.loc[tk].get("등락률", 0)),
                    })
        except Exception as e:  # noqa: BLE001
            log.debug("거래량 급증 계산 스킵: %s", e)

        result["흐름판"]["거래집중"] = _dedupe_ranked([
            {"티커": x.get("티커"), "이름": x.get("이름"), "배수": x.get("배수"),
             "등락률": x.get("등락률")}
            for x in sorted(result["거래량_급증"], key=lambda x: x.get("배수", 0), reverse=True)
        ], 3)

        # 신규 상장 (전 영업일 티커 목록과 비교)
        try:
            today_set = set(s.get_etf_ticker_list(day))
            prev_set = set(s.get_etf_ticker_list(prev))
            for tk in sorted(today_set - prev_set):
                result["신규상장"].append({"티커": tk, "이름": names.get(tk, tk)})
        except Exception as e:  # noqa: BLE001
            log.debug("신규상장 비교 스킵: %s", e)

    except Exception as e:  # noqa: BLE001
        log.warning("ETF 레이더 실패: %s", e)

    for k in ("전일_순매수", "전일_순매도", "거래량_급증"):
        result[k] = result[k][:8]
    flow = result.get("흐름판") or {}
    log.info("ETF 흐름판 %s 상승 %d개·하락 %d개·고변동 %d개",
             flow.get("기간", "-"), len(flow.get("상승") or []),
             len(flow.get("하락") or []), len(flow.get("고변동상품") or []))
    if not _force_naver and not (flow.get("상승") or flow.get("하락")):
        log.warning("KRX 기반 ETF 흐름판이 비어 네이버 금융 보조 시세로 다시 계산합니다")
        fallback = etf_radar(day, cfg, mode, _force_naver=True)
        if (fallback.get("흐름판") or {}).get("상승") or (fallback.get("흐름판") or {}).get("하락"):
            result["흐름판"] = fallback["흐름판"]
            result["진단"] = fallback.get("진단") or result["진단"]
        else:
            result["진단"] = fallback.get("진단") or result["진단"]
    return result
