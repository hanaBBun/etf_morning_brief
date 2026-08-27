"""진입점. python -m src.main [--mode daily|weekly|thursday] [--no-send] [--dry-run].

main 브랜치의 코드 변경 검증은 GitHub Actions에서 --no-send로 실행된다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from typing import Any

from . import events, kakao, krx, llm, market, news, render, youtube
from .config import kdate, load_config, now_kst

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("brief")


def _brief_identity(run_at, mode: str) -> tuple[str, str]:
    if mode == "weekly":
        return "주간 경제·ETF 브리핑", "지난 한 주 복기"
    if run_at.weekday() == 0:
        return "월요일 경제·ETF 브리핑", "이번 주 준비"
    title = "아침 경제·ETF 브리핑" if run_at.hour < 9 else "경제·ETF 시장 업데이트"
    return title, "전일 시장 정리"


def collect_handoff(cfg: dict) -> dict[str, Any]:
    """ETF 처방전 전달문용 — 실행 시점 기준 최근 뉴스를 깊게 모은다."""
    from .config import now_kst as _now

    now = _now()
    hours = int((cfg.get("목요일_전달문") or {}).get("최근_뉴스_시간", 96))
    start = now - timedelta(hours=hours)
    data: dict[str, Any] = {
        "날짜표시": kdate(),
        "모드": "thursday",
        "수집범위": f"{start:%m/%d %H:%M} ~ {now:%m/%d %H:%M} KST",
    }

    log.info("1/3 최근 뉴스 수집 (실행 시점 기준 %d시간)", hours)
    try:
        data["뉴스"] = news.collect_news(cfg, hours=hours)
    except Exception as e:  # noqa: BLE001
        log.error("뉴스 수집 실패: %s", e)
        data["뉴스"] = {}

    # 어느 그룹에서 몇 건을 AI에 넘길지. llm._balanced_news 가 이 값을 쓴다.
    mix = (cfg.get("목요일_전달문") or {}).get("수집배분") or llm.HANDOFF_MIX
    data["수집배분"] = mix

    log.info("2/3 상위 기사 본문 추출 (발언 인용을 찾기 위함)")
    try:
        for group, want in mix.items():
            items = (data["뉴스"] or {}).get(group) or []
            if not items:
                log.info("  %s: 기사 없음", group)
                continue
            # 배분량보다 조금 넉넉히 긁어야 AI가 고를 여지가 생긴다
            data["뉴스"][group] = news.enrich_with_body(
                items, limit=min(len(items), int(want) + 2)
            )
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
        "수집상태": {},
    }
    run_at = now_kst()
    data["브리핑제목"], data["브리핑역할"] = _brief_identity(run_at, mode)
    data["호출시각"] = run_at.strftime("%m/%d %H:%M KST")

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
            data["ETF_후보"] = krx.etf_radar(day, cfg, mode)
            flow = (data["ETF_후보"].get("흐름판") or {})
            if not (flow.get("상승") or flow.get("하락")):
                log.warning("ETF 흐름판이 비어 수집을 한 번 더 시도합니다")
                retried = krx.etf_radar(day, cfg, mode)
                retry_flow = (retried.get("흐름판") or {})
                if retry_flow.get("상승") or retry_flow.get("하락"):
                    data["ETF_후보"] = retried
                    flow = retry_flow
            log.info("ETF 흐름판 상승 %d개·하락 %d개",
                     len(flow.get("상승") or []), len(flow.get("하락") or []))
            data["수집상태"]["KRX"] = "정상"
        except Exception as e:  # noqa: BLE001
            log.error("국내 데이터 수집 실패: %s", e)
            data["수집상태"]["KRX"] = f"실패: {type(e).__name__}"
    else:
        # KRX 계정이 없으면 호출 자체를 건너뛴다 (실패 로그 도배 방지)
        log.info("KRX 건너뜀 — 아래에서 yfinance 값으로 대체합니다")
        data["수집상태"]["KRX"] = "미설정"

    log.info("2/6 글로벌 지표 수집")
    try:
        data["지표"] = market.collect_indicators(cfg)
        if mode == "weekly":
            data["주간_대표흐름"] = market.collect_weekly_performance(cfg)
        data["수집상태"]["Yahoo Finance"] = "정상"
    except Exception as e:  # noqa: BLE001
        log.error("글로벌 지표 실패: %s", e)
        data["지표"] = {}
        data["수집상태"]["Yahoo Finance"] = f"실패: {type(e).__name__}"

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
        if mode == "daily":
            news_hours, news_range = news.daily_window()
            data["수집범위"] = news_range
        else:
            news_hours = 170
        data["ETF_주도테마후보"] = news.detect_etf_themes(data.get("ETF_후보") or {})
        data["뉴스"] = news.collect_news(
            cfg, hours=news_hours, themes=data["ETF_주도테마후보"]
        )
        total_news = sum(len(v or []) for v in data["뉴스"].values())
        data["수집상태"]["뉴스"] = f"정상({total_news}건)" if total_news else "수집 0건"
    except Exception as e:  # noqa: BLE001
        log.error("뉴스 실패: %s", e)
        data["뉴스"] = {}
        data["수집상태"]["뉴스"] = f"실패: {type(e).__name__}"

    log.info("4-1/6 공식 경제 일정")
    try:
        data["공식일정"] = events.collect_week()
        log.info("공식 일정 %d건", len(data["공식일정"]))
    except Exception as e:  # noqa: BLE001
        log.error("공식 일정 실패: %s", e)
        data["공식일정"] = []

    log.info("5/6 유튜브 트렌드")
    try:
        data["유튜브"] = youtube.collect(cfg)
        data["수집상태"]["YouTube"] = str((data["유튜브"] or {}).get("상태") or "정상")
    except Exception as e:  # noqa: BLE001
        log.error("유튜브 실패: %s", e)
        data["유튜브"] = {}
        data["수집상태"]["YouTube"] = f"실패: {type(e).__name__}"

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
    if data.get("수집범위"):
        data["기준설명"] += f" · 뉴스 {data['수집범위']}"
    data["기준일태그"] = data.get("국내기준일_표시", "")
    return data


def _daily_published() -> bool:
    """예약 재시도 시 오늘 공식 HTML이 이미 저장돼 있는지 확인한다."""
    stamp = now_kst().strftime("%Y-%m-%d")
    return (render.DOCS / f"{stamp}.html").exists()


def _operator_issues(data: dict, ai: dict, incomplete: list[str] | None = None) -> list[str]:
    """독자 화면이 아니라 운영자에게만 알려야 할 실제 수집·생성 누락."""
    issues: list[str] = []
    cards = {str(x.get("시장") or ""): x for x in (ai.get("시장브리핑") or [])
             if isinstance(x, dict)}
    for market_name in ("국내", "미국"):
        card = cards.get(market_name) or {}
        missing = [k for k in ("결과", "원인", "ETF연결") if not str(card.get(k) or "").strip()]
        if card.get("자동생성") or missing:
            issues.append(f"{market_name} 시황: AI 해설 미완성")

    etf = data.get("ETF_후보") or {}
    flow = etf.get("흐름판") or {}
    if not (flow.get("상승") or flow.get("하락")):
        diag = etf.get("진단") or {}
        issues.append("ETF 흐름판: 재조회 후 0건"
                      f"(원본 {diag.get('원본', 0)}·필터 {diag.get('유동성필터통과', 0)})")

    state = data.get("수집상태") or {}
    for label in ("뉴스", "YouTube", "Yahoo Finance"):
        value = str(state.get(label) or "")
        if value and ("실패" in value or "0건" in value or "초과" in value):
            issues.append(f"{label}: {value}")

    etf_news = sum(len((data.get("뉴스") or {}).get(group) or [])
                   for group in ("ETF시장", "ETF", "레버리지"))
    if etf_news and not (ai.get("etf_레이더") or []):
        issues.append(f"ETF 레이더: 후보 기사 {etf_news}건이 검증 후 모두 제외")

    for error in incomplete or []:
        if "시장브리핑" not in error and error not in issues:
            issues.append(error)
    return list(dict.fromkeys(issues))[:6]


def _send_operator_alert(cfg: dict, data: dict, issues: list[str], no_send: bool) -> None:
    if no_send or not issues:
        return
    try:
        ok = kakao.send_operator_alert(cfg, issues, str(data.get("날짜표시") or "오늘"))
        log.info("운영자 수집 알림 %s", "성공" if ok else "실패")
    except Exception as e:  # noqa: BLE001
        log.error("운영자 수집 알림 발송 실패: %s", e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly", "thursday"], default="daily")
    ap.add_argument("--no-send", action="store_true", help="카톡 발송 생략")
    ap.add_argument("--dry-run", action="store_true", help="AI 호출·발송 모두 생략")
    ap.add_argument("--replace-existing", action="store_true",
                    help="같은 날짜의 공식 일간본을 의도적으로 교체")
    ap.add_argument("--skip-if-existing", action="store_true",
                    help="오늘 공식 일간본이 이미 있으면 중복 생성·발송 생략")
    args = ap.parse_args()

    cfg = load_config()
    log.info("=== %s 브리핑 시작 (%s) ===", args.mode, now_kst().strftime("%Y-%m-%d %H:%M"))

    if args.mode == "daily" and args.skip_if_existing and _daily_published():
        log.info("오늘 공식 아침 브리핑이 이미 있어 중복 생성·카톡 발송을 생략합니다")
        return 0

    data = collect_handoff(cfg) if args.mode == "thursday" else collect(cfg, args.mode)

    ai: dict[str, Any] = {}
    if not args.dry_run:
        log.info("AI 생성")
        ai = llm.generate(cfg, data, args.mode)
        if args.mode == "daily" and ai.get("유튜브"):
            youtube.save_analysis(ai["유튜브"])
        data.setdefault("수집상태", {})["AI 요약"] = "정상" if ai else "실패"
        if not ai:
            log.warning("AI 결과가 비었습니다. 데이터 표만으로 렌더링합니다.")

    if args.mode == "daily" and not args.dry_run:
        incomplete = render.validate_daily(cfg, data, ai)
        if incomplete:
            _send_operator_alert(cfg, data, _operator_issues(data, ai, incomplete), args.no_send)
            raise RuntimeError("불완전한 브리핑 발행 중단: " + ", ".join(incomplete))

    path, url = render.render(cfg, data, ai, args.mode,
                              replace_existing=args.replace_existing)
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
            msgs = [f"📰 ETF 뉴스·출연자 추천이 준비됐습니다.\n\n"
                    f"최근 ETF 뉴스 {n}건과 출연자 추천을 담았습니다."
                    f""]
        else:
            msgs = kakao.fallback_messages(data)
    if args.mode == "thursday":
        msgs = msgs[:1]  # 전달문은 1건만

    try:
        ok = kakao.send_brief(cfg, msgs, url)
        log.info("카톡 발송 %s", "성공" if ok else "일부 실패")
        if args.mode == "daily":
            _send_operator_alert(cfg, data, _operator_issues(data, ai), False)
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        log.error("카톡 발송 실패: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
