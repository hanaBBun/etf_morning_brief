"""브리핑 발행 채널 경계.

수집·분석·렌더링 코드는 카카오나 Slack의 구현을 알지 않는다.
개인용 v1은 Kakao를 쓰고, 회사 전환 때 이 모듈에 Slack 어댑터만
추가해 나머지 파이프라인을 그대로 재사용한다.
"""
from __future__ import annotations

from . import kakao


def channel(cfg: dict) -> str:
    return str((cfg.get("발행") or {}).get("채널") or "kakao").strip().lower()


def build_messages(mode: str, data: dict, ai: dict) -> list[str]:
    """모드별 공개 발송 본문을 한곳에서 확정한다."""
    messages = []
    generated = ai.get("카톡") or {}
    for key in ("1", "2"):
        if generated.get(key):
            messages.append(str(generated[key]))
    if not messages:
        if mode == "thursday":
            count = len(ai.get("etf_뉴스6선") or [])
            messages = [
                "📰 ETF 뉴스·출연자 추천이 준비됐습니다.\n\n"
                f"최근 ETF 뉴스 {count}건과 출연자 추천을 담았습니다."
            ]
        else:
            messages = kakao.fallback_messages(data)
    return messages[:1] if mode == "thursday" else messages


def send_brief(cfg: dict, messages: list[str], url: str = "") -> bool:
    provider = channel(cfg)
    if provider == "kakao":
        return kakao.send_brief(cfg, messages, url)
    raise RuntimeError(f"지원하지 않는 발행 채널: {provider}")


def send_operator_alert(cfg: dict, issues: list[str], date_text: str = "오늘") -> bool:
    provider = str((cfg.get("발행") or {}).get("운영자_알림_채널")
                   or channel(cfg)).strip().lower()
    if provider == "kakao":
        return kakao.send_operator_alert(cfg, issues, date_text)
    raise RuntimeError(f"지원하지 않는 운영자 알림 채널: {provider}")
