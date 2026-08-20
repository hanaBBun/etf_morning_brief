"""카카오톡 '나에게 보내기' 발송 + 리프레시 토큰 자동 갱신.

필요한 GitHub Secrets
  KAKAO_REST_API_KEY   카카오 개발자 앱의 REST API 키
  KAKAO_REFRESH_TOKEN  최초 1회 발급한 리프레시 토큰
선택
  KAKAO_CLIENT_SECRET  앱에서 client_secret 을 켰다면 필요
  GH_PAT               리프레시 토큰이 새로 발급될 때 Secret 을 자동 갱신 (권장)
"""
from __future__ import annotations

import base64
import json
import logging
import time

import requests

from .config import env

log = logging.getLogger(__name__)

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def refresh_access_token() -> tuple[str, str | None]:
    """액세스 토큰을 갱신한다. 새 리프레시 토큰이 오면 함께 반환."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": env("KAKAO_REST_API_KEY", required=True),
        "refresh_token": env("KAKAO_REFRESH_TOKEN", required=True),
    }
    secret = env("KAKAO_CLIENT_SECRET")
    if secret:
        payload["client_secret"] = secret

    r = requests.post(TOKEN_URL, data=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(
            f"카카오 토큰 갱신 실패 ({r.status_code}): {r.text}\n"
            "리프레시 토큰이 만료됐을 수 있습니다. SETUP.md의 '토큰 재발급'을 참고하세요."
        )
    d = r.json()
    return d["access_token"], d.get("refresh_token")


def send_text(access_token: str, text: str, link_url: str = "", button: str = "전문 보기") -> bool:
    obj: dict = {"object_type": "text", "text": text}
    if link_url:
        obj["link"] = {"web_url": link_url, "mobile_web_url": link_url}
        obj["button_title"] = button
    else:
        obj["link"] = {}

    r = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(obj, ensure_ascii=False)},
        timeout=20,
    )
    if r.status_code != 200:
        log.error("카카오 발송 실패 (%s): %s", r.status_code, r.text)
        return False
    return True


def send_brief(cfg: dict, messages: list[str], url: str = "") -> bool:
    """메시지를 순서대로 발송한다. 마지막 메시지에만 링크 버튼을 붙인다."""
    kk = cfg.get("카카오") or {}
    limit = int(kk.get("글자수_제한", 195))
    want = int(kk.get("발송건수", 2))
    use_link = bool(kk.get("링크_붙이기", True))

    messages = [m.strip() for m in messages if m and m.strip()][:want]
    if not messages:
        log.warning("발송할 메시지가 없습니다.")
        return False

    # 사이트_주소가 비어 있으면 url 이 파일명뿐이라 카카오가 거부한다.
    # 온전한 http(s) 주소일 때만 링크 버튼을 붙인다.
    if url and not url.startswith(("http://", "https://")):
        log.warning(
            "사이트_주소가 설정되지 않아 링크 버튼 없이 발송합니다. "
            "GitHub Pages 설정 후 config.yaml 의 사이트_주소를 채워주세요."
        )
        url = ""

    token, new_refresh = refresh_access_token()
    if new_refresh:
        _persist_refresh_token(new_refresh)

    # 링크는 모든 메시지에 붙인다.
    # 카카오 텍스트 템플릿은 link 를 비워도 버튼 자리를 만들고 앱 기본 주소로 보내는데,
    # 그 주소가 등록돼 있지 않으면 404 가 뜬다. 그래서 빈 링크를 남기지 않는다.
    ok = True
    for i, msg in enumerate(messages):
        if len(msg) > limit:
            msg = msg[:limit].rstrip()
        is_last = i == len(messages) - 1
        sent = send_text(token, msg, url if use_link else "",
                         button="전문 보기" if is_last else "브리핑 열기")
        ok = ok and sent
        if not is_last:
            time.sleep(1.2)  # 순서 보장
    return ok


# ─────────────────────────────────────────────
# 리프레시 토큰 회전
# ─────────────────────────────────────────────
def _persist_refresh_token(new_token: str) -> None:
    """새 리프레시 토큰을 GitHub Secret 에 반영. GH_PAT 없으면 로그만 남긴다."""
    pat = env("GH_PAT")
    repo = env("GITHUB_REPOSITORY")
    if not pat or not repo:
        log.warning(
            "새 리프레시 토큰이 발급됐지만 GH_PAT 가 없어 자동 갱신하지 못했습니다. "
            "Actions 로그의 아래 값을 KAKAO_REFRESH_TOKEN Secret 에 직접 넣어주세요.\n"
            "새 토큰 앞 8자리: %s********", new_token[:8]
        )
        return
    try:
        from nacl import encoding, public

        h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
        key = requests.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers=h, timeout=20,
        ).json()
        pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
        sealed = public.SealedBox(pk).encrypt(new_token.encode())
        r = requests.put(
            f"https://api.github.com/repos/{repo}/actions/secrets/KAKAO_REFRESH_TOKEN",
            headers=h,
            json={
                "encrypted_value": base64.b64encode(sealed).decode(),
                "key_id": key["key_id"],
            },
            timeout=20,
        )
        if r.status_code in (201, 204):
            log.info("리프레시 토큰을 GitHub Secret 에 자동 갱신했습니다.")
        else:
            log.warning("Secret 갱신 실패 (%s): %s", r.status_code, r.text)
    except Exception as e:  # noqa: BLE001
        log.warning("Secret 자동 갱신 중 오류: %s", e)


def _fmt(row: dict, digits: int = 2, unit: str = "", bp: bool = False) -> str | None:
    close, pct = row.get("종가"), row.get("등락률")
    if close is None:
        return None
    if bp:
        v = row.get("변화bp")
        if v is None and row.get("전일") is not None:
            v = (close - row["전일"]) * 100
        chg = f"{v:+.1f}bp" if v is not None else ""
    else:
        chg = f"{pct:+.2f}%" if pct is not None else ""
    return f"{row['이름']} {close:,.{digits}f}{unit} {chg}".strip()


def fallback_messages(data: dict) -> list[str]:
    """AI 생성이 실패했을 때 쓸 카톡 문구.

    AI가 없어도 그날 숫자는 최대한 담는다. (지수·환율·해외·금리·유가)
    """
    ind = data.get("지표") or {}
    head = f"☀️ {data.get('날짜표시', '')} 아침 브리핑"

    # 1건: 국내
    dom = [_fmt(r) for r in (data.get("국내지수") or [])]
    fx = next((r for r in ind.get("국내", []) if r["이름"] == "원/달러"), None)
    if fx:
        dom.append(_fmt(fx, 1, "원"))
    if data.get("수급"):
        from .config import fmt_eok
        for f in data["수급"]:
            if f["주체"] == "외국인":
                dom.append(f"외국인 {fmt_eok(f['순매수'])}")
    dom = [x for x in dom if x]

    # 2건: 해외 + 금리 + 원자재
    ovs = [_fmt(r) for r in ind.get("해외지수", [])[:4]]
    rate = [_fmt(r, 3, "%", bp=True) for r in ind.get("금리", [])[:2]]
    comm = [_fmt(r) for r in ind.get("원자재", [])[:2]]
    ovs = [x for x in ovs if x]
    rate = [x for x in rate if x]
    comm = [x for x in comm if x]

    msgs = []
    if dom:
        msgs.append(f"{head}\n\n🇰🇷 국내\n" + "\n".join(dom))
    second = []
    if ovs:
        second.append("🌍 해외\n" + "\n".join(ovs))
    if rate:
        second.append("💵 금리\n" + "\n".join(rate))
    if comm:
        second.append("🛢 원자재\n" + "\n".join(comm))
    if second:
        msgs.append("\n\n".join(second))

    if not msgs:
        msgs = [f"{head}\n\n데이터 수집에 실패했습니다. 실행 로그를 확인해주세요."]
    return msgs
