"""국내 증시 데이터 수집 (pykrx).

모든 함수는 실패해도 예외를 던지지 않고 빈 값을 돌려준다.
데이터 하나가 막혀도 브리핑 전체가 죽지 않게 하기 위함이다.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .config import now_kst

log = logging.getLogger(__name__)


def _stock():
    from pykrx import stock
    return stock


def last_business_day(offset: int = 0) -> str:
    """가장 최근 영업일을 YYYYMMDD로. offset=1이면 그 전 영업일."""
    s = _stock()
    d = now_kst().date()
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


# ─────────────────────────────────────────────
# 투자자별 수급
# ─────────────────────────────────────────────
def investor_flow(day: str, market: str = "KOSPI") -> list[dict]:
    """개인·외국인·기관 순매수 금액(원)."""
    s = _stock()
    try:
        df = s.get_market_trading_value_by_investor(day, day, market)
        if df is None or df.empty:
            return []
        want = {"개인": "개인", "외국인": "외국인", "기관합계": "기관"}
        rows = []
        for idx, label in want.items():
            if idx in df.index:
                rows.append({"주체": label, "순매수": float(df.loc[idx, "순매수거래대금"])})
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
        df = df.sort_values("순매수거래대금", ascending=False)
        top = df.head(n)
        bot = df.tail(n)
        rows = []
        for tk, r in top.iterrows():
            rows.append({"티커": tk, "종목명": r.get("종목명", ""),
                         "순매수": float(r["순매수거래대금"]), "구분": "순매수"})
        for tk, r in bot.iterrows():
            rows.append({"티커": tk, "종목명": r.get("종목명", ""),
                         "순매수": float(r["순매수거래대금"]), "구분": "순매도"})
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
def etf_radar(day: str, cfg: dict) -> dict[str, Any]:
    """ETF 후보를 6갈래로 수집. 실제 3개 선별은 llm 단계에서 한다."""
    s = _stock()
    rule = cfg.get("ETF_레이더", {})
    thr_flow = float(rule.get("순매수_임계치_억원", 300)) * 1e8
    vol_mult = float(rule.get("거래량_급증_배수", 3.0))
    prev = last_business_day(1)

    result: dict[str, Any] = {
        "전일_순매수": [], "전일_순매도": [], "거래량_급증": [],
        "신규상장": [], "순자산_급증": [], "기준일": day,
    }

    try:
        df = s.get_etf_ohlcv_by_ticker(day)
        if df is None or df.empty:
            return result

        names = {}
        for tk in df.index:
            try:
                names[tk] = s.get_etf_ticker_name(tk)
            except Exception:  # noqa: BLE001
                names[tk] = tk

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
    return result
