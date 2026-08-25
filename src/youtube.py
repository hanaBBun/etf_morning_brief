"""유튜브 경쟁 채널 트렌드 (YouTube Data API v3).

YOUTUBE_API_KEY 가 없으면 조용히 빈 결과를 돌려준다.
채널 ID와 업로드 재생목록 ID는 최초 1회 조회 후 캐시된다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import time, timedelta
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
UPLOADS_CACHE = ROOT / "channel_uploads.json"
DAILY_CACHE = ROOT / "youtube_daily_cache.json"


def _duration_seconds(value: str) -> int | None:
    """YouTube ISO 8601 재생시간(PT1H2M3S)을 초로 바꾼다."""
    hit = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(value or ""))
    if not hit:
        return None
    hours, minutes, seconds = (int(x or 0) for x in hit.groups())
    return hours * 3600 + minutes * 60 + seconds


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


def _load_uploads_cache() -> dict[str, str]:
    try:
        return json.loads(UPLOADS_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_uploads_cache(d: dict[str, str]) -> None:
    try:
        UPLOADS_CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("업로드 재생목록 캐시 저장 실패: %s", e)


def _load_daily_cache(day: str) -> dict[str, Any]:
    if not DAILY_CACHE.exists():
        return {}
    try:
        saved = json.loads(DAILY_CACHE.read_text(encoding="utf-8"))
        return saved if saved.get("날짜") == day else {}
    except Exception as e:  # noqa: BLE001
        log.warning("유튜브 당일 캐시 읽기 실패: %s", e)
        return {}


def _save_daily_cache(day: str, result: dict[str, Any]) -> None:
    try:
        payload = {"날짜": day, "급상승": result.get("급상승") or [],
                   "댓글샘플": result.get("댓글샘플") or [],
                   "분석": result.get("분석") or _load_daily_cache(day).get("분석") or []}
        DAILY_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("유튜브 당일 캐시 저장 실패: %s", e)


def save_analysis(items: list[dict]) -> None:
    """AI가 붙인 핵심주제·훅·겹침 판정도 같은 날 영상과 함께 보존한다."""
    day = now_kst().strftime("%Y-%m-%d")
    cached = _load_daily_cache(day)
    if not cached:
        return
    merged = {str(x.get("영상ID")): x for x in (cached.get("분석") or [])}
    merged.update({str(x.get("영상ID")): x for x in items if x.get("영상ID")})
    cached["분석"] = list(merged.values())
    _save_daily_cache(day, cached)


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


def resolve_upload_playlists(ids: dict[str, str], key: str) -> dict[str, str]:
    """채널별 uploads 재생목록을 저장한다. channels.list는 최대 50개를 한 번에 조회."""
    cache = _load_uploads_cache()
    missing = {name: cid for name, cid in ids.items() if not cache.get(name)}
    by_id = {cid: name for name, cid in missing.items()}
    channel_ids = list(by_id)
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i:i + 50]
        try:
            res = _get("channels", key, part="contentDetails", id=",".join(chunk), maxResults=50)
            for item in res.get("items") or []:
                name = by_id.get(item.get("id"))
                uploads = (((item.get("contentDetails") or {}).get("relatedPlaylists") or {})
                           .get("uploads"))
                if name and uploads:
                    cache[name] = uploads
        except Exception as e:  # noqa: BLE001
            log.warning("업로드 재생목록 조회 실패: %s", e)
    if missing:
        _save_uploads_cache(cache)
    return {name: cache[name] for name in ids if cache.get(name)}


def collect(cfg: dict) -> dict[str, Any]:
    yt = cfg.get("유튜브") or {}
    day = now_kst().strftime("%Y-%m-%d")
    cached = _load_daily_cache(day)
    if not yt.get("사용", True):
        return {"상태": "꺼짐"}
    key = env("YOUTUBE_API_KEY")
    if not key:
        log.warning("YOUTUBE_API_KEY 없음 — 경쟁 채널 동향을 수집하지 못했습니다")
        if cached.get("급상승"):
            return {"상태": "당일캐시", "급상승": cached["급상승"],
                    "댓글샘플": cached.get("댓글샘플") or [],
                    "분석": cached.get("분석") or []}
        return {"상태": "키없음"}

    names = yt.get("채널") or []
    top_n = int(yt.get("급상승_표시개수", 5))
    comment_n = int(yt.get("댓글_분석_영상수", 3))

    ids = resolve_channel_ids(names, key)
    uploads = resolve_upload_playlists(ids, key)
    # 영상은 뉴스보다 업로드 빈도가 낮다. 같은 날 재실행해도 아침에 잡힌
    # 영상이 사라지지 않도록 이틀 전 0시(KST)를 고정 경계로 쓴다.
    now = now_kst()
    after_kst = __import__("datetime").datetime.combine(
        now.date() - timedelta(days=2), time.min, tzinfo=now.tzinfo)
    after = after_kst.astimezone(
        __import__("datetime").timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    videos: list[dict] = []
    failed_channels = 0
    for name, playlist_id in uploads.items():
        try:
            res = _get("playlistItems", key, part="snippet,contentDetails",
                       playlistId=playlist_id, maxResults=5)
            for it in res.get("items") or []:
                snippet = it.get("snippet") or {}
                published = (it.get("contentDetails") or {}).get("videoPublishedAt") \
                    or snippet.get("publishedAt", "")
                if not published or published < after:
                    continue
                video_id = (it.get("contentDetails") or {}).get("videoId") \
                    or (snippet.get("resourceId") or {}).get("videoId")
                if not video_id:
                    continue
                videos.append({
                    "영상ID": video_id,
                    "제목": snippet.get("title", ""),
                    "채널": name,
                    "업로드": published,
                })
        except Exception as e:  # noqa: BLE001
            log.warning("영상 조회 실패 %s: %s", name, e)
            failed_channels += 1

    if not videos:
        if cached.get("급상승"):
            log.warning("유튜브 신규 수집 0건 — 아침에 저장한 당일 목록 %d건 유지",
                        len(cached["급상승"]))
            return {"상태": "당일캐시", "급상승": cached["급상승"],
                    "댓글샘플": cached.get("댓글샘플") or [],
                    "분석": cached.get("분석") or [], "채널수": len(ids)}
        state = "수집실패" if failed_channels else "새영상없음"
        return {"상태": state, "급상승": [], "채널수": len(ids)}

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
            res = _get("videos", key, part="statistics,snippet,contentDetails", id=",".join(chunk))
            for it in res.get("items") or []:
                stats[it["id"]] = it
        except Exception as e:  # noqa: BLE001
            log.warning("영상 통계 실패: %s", e)

    for v in videos:
        st = stats.get(v["영상ID"], {}).get("statistics", {})
        details = stats.get(v["영상ID"], {}).get("contentDetails", {})
        v["조회수"] = int(st.get("viewCount", 0))
        v["댓글수"] = int(st.get("commentCount", 0))
        v["길이초"] = _duration_seconds(details.get("duration"))
        v["링크"] = f"https://www.youtube.com/watch?v={v['영상ID']}"

    # 같은 날짜에 앞선 실행에서 잡힌 영상은 새 결과와 합친다. 조회수 순위가
    # 바뀌거나 API 쿼터가 불안정해도 아침에 보인 영상이 오후에 사라지지 않는다.
    merged = {str(v.get("영상ID")): v for v in (cached.get("급상승") or [])}
    merged.update({str(v.get("영상ID")): v for v in videos})
    videos = list(merged.values())

    # ① ETF 관련 영상 먼저, ② 관련도 높은 순, ③ 조회수 순
    videos.sort(key=lambda r: (r.get("ETF관련", False), r.get("ETF점수", 0),
                               r.get("조회수", 0)), reverse=True)
    top = videos[:top_n]
    n_etf = sum(1 for v in top if v.get("ETF관련"))
    log.info("유튜브 %d건 중 상위 %d건 선정 (ETF 관련 %d건)",
             len(videos), len(top), n_etf)
    if not n_etf:
        log.warning("최근 이틀 0시 이후 ETF를 다룬 영상이 없습니다 — 일반 경제 영상만 담깁니다")

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

    result = {
        "상태": "정상",
        "ETF관련영상수": n_etf,
        "급상승": top,
        "전체영상수": len(videos),
        "댓글샘플": keywords[:80],
        "채널수": len(ids),
        "미해결채널": [n for n in names if n not in uploads],
        "분석": cached.get("분석") or [],
    }
    if not result["댓글샘플"] and cached.get("댓글샘플"):
        result["댓글샘플"] = cached["댓글샘플"]
    _save_daily_cache(day, result)
    return result


def _etf_score(title: str, extra: list[str] | None = None) -> int:
    """제목이 ETF를 얼마나 정면으로 다루는지 점수화."""
    t = str(title or "")
    core = ETF_CORE + tuple(extra or ())
    return 3 * sum(1 for w in core if w in t) + sum(1 for w in ETF_SUB if w in t)
