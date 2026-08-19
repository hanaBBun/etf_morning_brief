#!/usr/bin/env python3
"""카카오 인증 코드 → 리프레시 토큰 교환 (최초 1회용).

GitHub Actions의 '🔑 카카오 토큰 발급' 워크플로가 이 파일을 실행합니다.
로컬에서 직접 돌리려면 아래 환경변수를 설정한 뒤 실행하세요.

  KAKAO_REST_API_KEY   카카오 앱의 REST API 키
  KAKAO_AUTH_CODE      브라우저 주소창의 code= 값
  KAKAO_REDIRECT_URI   카카오 앱에 등록한 Redirect URI
  KAKAO_CLIENT_SECRET  (선택) client_secret 을 켰다면
"""
import json
import os
import sys
import urllib.parse
import urllib.request

LINE = "=" * 66
TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def main() -> int:
    key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    code = os.environ.get("KAKAO_AUTH_CODE", "").strip()
    redirect = os.environ.get("KAKAO_REDIRECT_URI", "https://localhost:3000").strip()
    secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()

    if not key:
        print("::error::KAKAO_REST_API_KEY Secret 이 먼저 등록되어 있어야 합니다.")
        print("SETUP.md 4단계에서 KAKAO_REST_API_KEY 를 등록한 뒤 다시 실행하세요.")
        return 1
    if not code:
        print("::error::code 값이 비어 있습니다.")
        return 1

    # 사용자가 URL 전체를 붙여넣은 경우 code 파라미터만 뽑아낸다.
    if code.startswith("http") or "code=" in code:
        parsed = urllib.parse.urlparse(code)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("code"):
            code = qs["code"][0]
            print("주소 전체가 입력되어 code 값만 추출했습니다.")

    payload = {
        "grant_type": "authorization_code",
        "client_id": key,
        "redirect_uri": redirect,
        "code": code,
    }
    if secret:
        payload["client_secret"] = secret

    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            body = json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        print(f"::error::요청 실패: {e}")
        return 1

    if "refresh_token" not in body:
        print("실패했습니다. 카카오 응답:")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print()
        print("가장 흔한 원인:")
        print(" 1. code 를 이미 한 번 사용했다 — code 는 1회용입니다. 인증 주소부터 다시 하세요.")
        print(" 2. code 발급 후 10분이 지났다 — 다시 발급받으세요.")
        print(" 3. Redirect URI 가 카카오 앱에 등록한 값과 정확히 다르다 — 슬래시 하나까지 같아야 합니다.")
        print(" 4. 동의항목에서 '카카오톡 메시지 전송'을 켜지 않았다 — SETUP.md 3-4단계.")
        return 1

    days = int(body.get("refresh_token_expires_in", 0)) // 86400
    print()
    print(LINE)
    print("  성공했습니다. 아래 한 줄을 통째로 복사하세요.")
    print("  GitHub 저장소 > Settings > Secrets and variables > Actions")
    print("  > New repository secret > 이름: KAKAO_REFRESH_TOKEN")
    print(LINE)
    print()
    print(body["refresh_token"])
    print()
    print(LINE)
    print(f"  이 토큰의 유효기간: 약 {days}일")
    print("  등록을 마친 뒤 이 실행 기록은 삭제하시는 것을 권합니다.")
    print("  (실행 기록 오른쪽 위 ⋯ > Delete workflow run)")
    print(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
