"""유튜브 경쟁 채널 트렌드 (YouTube Data API v3).

YOUTUBE_API_KEY 가 없으면 조용히 빈 결과를 돌려준다.
채널 ID는 최초 1회 조회 후 channel_ids.json 에 캐시된다.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests

from .config import ROOT, env, now_kst

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/youtube/v3"

# ETF 관련도 판정용 키워드.
# 핵심어는 3점, 보조어는 1점. 0점이면 '일반 경제 영상'으로 본다.
# 조회수만으로 고르면 대형 종합 경제 채널의 증시 브리핑이 늘 1등이라
# ETF 채널의 영상이 한 번도 못 올라온다. 그래서 관련도를 먼저 본다.
ETF_CORE = ("ETF", "상장지수", "커버드콜", "월배당", "분배금", "TDF", "레버리지",
            "인버스", "리츠", "REITs", "자산배분", "연금저축", "퇴직연금", "IRP",
            "ISA", "나스닥100", "S&P500", "S&P 500", "코스피200", "인덱스", "패시브")
ETF_SUB = ("배당", "채권", "지수", "포트폴리오", "적립식", "서학개미", "분산투자",
           "괴리율", "보수", "운용사", "장기투자")
CACHE = ROOT / "channel_ids.json"


def _get(path: str, key: str, **params) -> dict:
    params["key"] = key
    r = requests.get(f"{API}/{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _load_cache() -> dict[str, str]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cache(d: dict[str, str]) -> None:
    try:
        CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("채널 캐시 저장 실패: %s", e)


def resolve_channel_ids(names: list[str], key: str) -> dict[str, str]:
    cache = _load_cache()
    changed = False
    for name in names:
        if name in cache:
            continue
        try:
            res = _get("search", key, part="snippet", q=name, type="channel", maxResults=1)
            items = res.get("items") or []
            if items:
                cache[name] = items[0]["snippet"]["channelId"]
                changed = True
        except Exception as e:  # noqa: BLE001
            log.warning("채널 ID 조회 실패 %s: %s", name, e)
    if changed:
        _save_cache(cache)
    return cache


def collect(cfg: dict) -> dict[str, Any]:
    yt = cfg.get("유튜브") or {}
    if not yt.get("사용", True):
        return {"상태": "꺼짐"}
    key = env("YOUTUBE_API_KEY")
    if not key:
        log.warning("YOUTUBE_API_KEY 없음 — 경쟁 채널 동향을 수집하지 못했습니다")
        return {"상태": "키없음"}

    names = yt.get("채널") or []
    top_n = int(yt.get("급상승_표시개수", 5))
    comment_n = int(yt.get("댓글_분석_영상수", 3))

    ids = resolve_channel_ids(names, key)
    name_by_id = {v: k for k, v in ids.items()}
    after = (now_kst() - timedelta(hours=36)).astimezone(
        __import__("datetime").timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    videos: list[dict] = []
    for name, cid in ids.items():
        try:
            res = _get("search", key, part="snippet", channelId=cid, type="video",
                       order="date", publishedAfter=after, maxResults=5)
            for it in res.get("items") or []:
                videos.append({
                    "영상ID": it["id"]["videoId"],
                    "제목": it["snippet"]["title"],
                    "채널": name,
                    "업로드": it["snippet"]["publishedAt"],
                })
        except Exception as e:  # noqa: BLE001
            log.warning("영상 조회 실패 %s: %s", name, e)

    if not videos:
        return {"상태": "새영상없음", "급상승": [], "채널수": len(ids)}

    core_extra = [w for w in (yt.get("ETF_키워드") or []) if w]
    for v in videos:
        v["ETF점수"] = _etf_score(v["제목"], core_extra)
        v["ETF관련"] = v["ETF점수"] > 0

    # 조회수 채우기 (50개씩)
    stats: dict[str, dict] = {}
    vid_list = [v["영상ID"] for v in videos]
    for i in range(0, len(vid_list), 50):
        chunk = vid_list[i:i + 50]
        try:
            res = _get("videos", key, part="statistics,snippet", id=",".join(chunk))
            for it in res.get("items") or []:
                stats[it["id"]] = it
        except Exception as e:  # noqa: BLE001
            log.warning("영상 통계 실패: %s", e)

    for v in videos:
        st = stats.get(v["영상ID"], {}).get("statistics", {})
        v["조회수"] = int(st.get("viewCount", 0))
        v["댓글수"] = int(st.get("commentCount", 0))
        v["링크"] = f"https://www.youtube.com/watch?v={v['영상ID']}"

    # ① ETF 관련 영상 먼저, ② 관련도 높은 순, ③ 조회수 순
    videos.sort(key=lambda r: (r.get("ETF관련", False), r.get("ETF점수", 0),
                               r.get("조회수", 0)), reverse=True)
    top = videos[:top_n]
    n_etf = sum(1 for v in top if v.get("ETF관련"))
    log.info("유튜브 %d건 중 상위 %d건 선정 (ETF 관련 %d건)",
             len(videos), len(top), n_etf)
    if not n_etf:
        log.warning("최근 36시간 안에 ETF를 다룬 영상이 없습니다 — 일반 경제 영상만 담깁니다")

    # 상위 영상 댓글 키워드
    keywords: list[str] = []
    for v in top[:comment_n]:
        try:
            res = _get("commentThreads", key, part="snippet",
                       videoId=v["영상ID"], maxResults=50, order="relevance")
            for it in res.get("items") or []:
                txt = it["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
                keywords.append(txt[:200])
        except Exception:  # noqa: BLE001
            continue

    return {
        "상태": "정상",
        "ETF관련영상수": n_etf,
        "급상승": top,
        "전체영상수": len(videos),
        "댓글샘플": keywords[:80],
        "채널수": len(ids),
        "미해결채널": [n for n in names if n not in ids],
    }


def _etf_score(title: str, extra: list[str] | None = None) -> int:
    """제목이 ETF를 얼마나 정면으로 다루는지 점수화."""
    t = str(title or "")
    core = ETF_CORE + tuple(extra or ())
    return 3 * sum(1 for w in core if w in t) + sum(1 for w in ETF_SUB if w in t)
