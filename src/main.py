"""진입점. python -m src.main [--mode daily|weekly] [--no-send] [--dry-run]"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from . import kakao, krx, llm, market, news, render, youtube
from .config import kdate, load_config, now_kst

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("brief")


def collect_handoff(cfg: dict) -> dict[str, Any]:
    """목요일 전달문용 — 시장 데이터는 필요 없고 뉴스만 깊게 모은다."""
    from .config import KST, now_kst as _now
    from datetime import timedelta

    now = _now()
    tue = (now - timedelta(days=(now.weekday() - 1) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    data: dict[str, Any] = {
        "날짜표시": kdate(),
        "모드": "thursday",
        "수집범위": f"{tue.month}/{tue.day}(화) ~ {now.month}/{now.day}({'월화수목금토일'[now.weekday()]}) 오전",
    }

    log.info("1/3 이번 주 뉴스 수집 (화요일 00시 기준)")
    try:
        data["뉴스"] = news.collect_since_tuesday(cfg)
    except Exception as e:  # noqa: BLE001
        log.error("뉴스 수집 실패: %s", e)
        data["뉴스"] = {}

    log.info("2/3 상위 기사 본문 추출 (발언 인용을 찾기 위함)")
    try:
        etf_items = (data["뉴스"] or {}).get("ETF") or []
        dom_items = (data["뉴스"] or {}).get("국내") or []
        if etf_items:
            data["뉴스"]["ETF"] = news.enrich_with_body(etf_items, limit=14)
        if dom_items:
            data["뉴스"]["국내"] = news.enrich_with_body(dom_items, limit=6)
        got = sum(
            1 for g in data["뉴스"].values() for it in (g or []) if it.get("본문")
        )
        log.info("본문 확보 %d건", got)
    except Exception as e:  # noqa: BLE001
        log.error("본문 추출 실패: %s", e)

    return data


def collect(cfg: dict, mode: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "날짜표시": kdate(),
        "모드": mode,
    }

    log.info("1/6 국내 증시 데이터 수집")
    data["국내지수"] = []
    data["종목_후보_국내"] = []
    if krx.krx_ready():
        try:
            day = krx.last_business_day()
            data["국내기준일"] = day
            data["국내기준일_표시"] = f"{day[4:6]}/{day[6:8]}"
            data["국내기준일_ISO"] = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            data["국내지수"] = krx.index_snapshot(day)
            data["수급"] = krx.investor_flow(day, "KOSPI")
            data["수급_코스닥"] = krx.investor_flow(day, "KOSDAQ")
            # 아래는 '후보'다. 브리핑에 그대로 싣는 목록이 아니라,
            # 지수·ETF 움직임을 설명할 때 근거로 쓸 수 있는 재료로만 전달한다.
            data["종목_후보_국내"] = krx.notable_stocks(day, cfg)
            data["ETF_후보"] = krx.etf_radar(day, cfg)
        except Exception as e:  # noqa: BLE001
            log.error("국내 데이터 수집 실패: %s", e)
    else:
        # KRX 계정이 없으면 호출 자체를 건너뛴다 (실패 로그 도배 방지)
        log.info("KRX 건너뜀 — 아래에서 yfinance 값으로 대체합니다")

    log.info("2/6 글로벌 지표 수집")
    try:
        data["지표"] = market.collect_indicators(cfg)
    except Exception as e:  # noqa: BLE001
        log.error("글로벌 지표 실패: %s", e)
        data["지표"] = {}

    # KRX가 막히면(로그인 필요 등) 국내 지수는 yfinance 값으로 대체한다.
    if not data.get("국내지수"):
        alt = [r for r in (data.get("지표") or {}).get("국내", [])
               if r["이름"] in ("코스피", "코스닥") and r.get("종가") is not None]
        if alt:
            log.warning("KRX 지수 조회 실패 → yfinance 값으로 대체합니다 (%d개)", len(alt))
            data["국내지수"] = alt
            if alt[0].get("기준일"):
                iso = alt[0]["기준일"]
                data["국내기준일_ISO"] = iso
                data["국내기준일_표시"] = f"{iso[5:7]}/{iso[8:10]}"
                data["기준일태그"] = data["국내기준일_표시"]
    if not data.get("수급"):
        log.warning("투자자별 수급 데이터 없음 — 해당 섹션은 브리핑에서 생략됩니다")

    log.info("3/6 미국 개별 종목 스크리닝 (후보 재료)")
    try:
        data["종목_후보_미국"] = market.collect_us_movers(cfg)
    except Exception as e:  # noqa: BLE001
        log.error("미국 종목 실패: %s", e)
        data["종목_후보_미국"] = []

    log.info("4/6 뉴스 수집")
    try:
        data["뉴스"] = news.collect_news(cfg, hours=30 if mode == "daily" else 170)
    except Exception as e:  # noqa: BLE001
        log.error("뉴스 실패: %s", e)
        data["뉴스"] = {}

    log.info("5/6 유튜브 트렌드")
    try:
        data["유튜브"] = youtube.collect(cfg)
    except Exception as e:  # noqa: BLE001
        log.error("유튜브 실패: %s", e)
        data["유튜브"] = {}

    us_day = ""
    for row in (data.get("지표") or {}).get("해외지수", []):
        if row.get("기준일"):
            us_day = row["기준일"]
            break
    data["해외기준일_표시"] = us_day[5:].replace("-", "/") if us_day else ""
    data["기준설명"] = (
        f"전일({data.get('국내기준일_표시','')}) 국내 증시 마감 · "
        f"{data.get('해외기준일_표시','')} 미국 증시 기준"
    )
    data["기준일태그"] = data.get("국내기준일_표시", "")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly", "thursday"], default="daily")
    ap.add_argument("--no-send", action="store_true", help="카톡 발송 생략")
    ap.add_argument("--dry-run", action="store_true", help="AI 호출·발송 모두 생략")
    args = ap.parse_args()

    cfg = load_config()
    log.info("=== %s 브리핑 시작 (%s) ===", args.mode, now_kst().strftime("%Y-%m-%d %H:%M"))

    data = collect_handoff(cfg) if args.mode == "thursday" else collect(cfg, args.mode)

    ai: dict[str, Any] = {}
    if not args.dry_run:
        log.info("AI 생성")
        ai = llm.generate(cfg, data, args.mode)
        if not ai:
            log.warning("AI 결과가 비었습니다. 데이터 표만으로 렌더링합니다.")

    path, url = render.render(cfg, data, ai, args.mode)
    render.dump_debug(data, ai)
    log.info("HTML 생성: %s", path)
    log.info("공개 주소: %s", url or "(사이트_주소 미설정)")

    if args.no_send or args.dry_run:
        log.info("발송 생략")
        return 0

    msgs = []
    kk = ai.get("카톡") or {}
    for key in ("1", "2"):
        if kk.get(key):
            msgs.append(kk[key])
    if not msgs:
        if args.mode == "thursday":
            n = len(ai.get("etf_뉴스6선") or [])
            msgs = [f"📰 ETF 처방전 전달문이 준비됐습니다.\n\n"
                    f"이번 주 ETF 뉴스 {n}건과 전문가 발언 정리를 담았습니다.\n"
                    f"링크에서 '복사용 텍스트'를 열면 바로 전달하실 수 있습니다."]
        else:
            msgs = kakao.fallback_messages(data)
    if args.mode == "thursday":
        msgs = msgs[:1]  # 전달문은 1건만

    try:
        ok = kakao.send_brief(cfg, msgs, url)
        log.info("카톡 발송 %s", "성공" if ok else "일부 실패")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        log.error("카톡 발송 실패: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
