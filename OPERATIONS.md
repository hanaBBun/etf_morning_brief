# 개인용 v1 운영 기준

이 문서는 기능을 반복 수정해도 자동·수동 발행 규칙이 흔들리지 않도록 하는
단일 운영 기준입니다. 콘텐츠 기준은 `README.md`, 실행·장애·이전 기준은 이 문서를 봅니다.

## 1. 파이프라인 경계

1. `collect` — 시세·뉴스·일정·YouTube 수집
2. `llm.generate` — 수집 근거 안에서 요약·해석
3. `render.validate_daily` — 국내·미국 카드, TOP5, 카톡 1~5 등 공개본 검증
4. `render.render` — 실행마다 고유 HTML 생성, 공식 첫 발행본 보호
5. `delivery` — 개인용은 Kakao, 회사용은 향후 Slack 어댑터 사용

Slack 도입 때 수집·분석·검증·HTML 코드는 바꾸지 않는다. `src/delivery.py`에
Slack 발송 구현을 연결하고 `config.yaml`의 `발행.채널`만 바꾸는 것을 원칙으로 한다.

## 2. 실행 종류

| 실행 | 결과 | 발송 | 기존 공식본 |
|---|---|---:|---|
| GitHub 예약 | 공식본 | 1회 | 있으면 건너뜀 |
| 외부 예약 | 공식본 | 1회 | 있으면 건너뜀 |
| 수동 기본 실행 | 고유 update 링크 | 실행 옵션에 따름 | 덮어쓰지 않음 |
| `no_send` | 고유 HTML | 안 함 | 덮어쓰지 않음 |
| `replace_published` | 공식본 교체 | 실행 옵션에 따름 | 명시적으로만 교체 |

같은 날짜의 과거 카톡 `전문 보기` 링크는 이후 테스트 때문에 바뀌면 안 된다.

## 3. 외부 스케줄러 연결

추천 서비스는 `cron-job.org`다. GitHub 코드는 바꾸지 않고 `workflow_dispatch`를
호출하므로 회사 저장소로 이전할 때 URL과 토큰만 교체할 수 있다.

### GitHub 토큰

- Fine-grained personal access token
- Repository access: `hanaBBun/etf_morning_brief` 하나만
- Repository permissions: `Actions: Read and write`
- 토큰은 GitHub 파일이나 로그에 저장하지 않고 외부 스케줄러의 비공개 헤더에만 입력

### 공통 HTTP 설정

- Method: `POST`
- Header `Accept`: `application/vnd.github+json`
- Header `Authorization`: `Bearer <외부예약용 토큰>`
- Header `X-GitHub-Api-Version`: `2022-11-28`
- Header `Content-Type`: `application/json`

### 호출 목록

| 작업 | 외부 실행(KST) | GitHub 기본 예약 | Workflow URL 끝부분 | Body |
|---|---:|---:|---|---|
| 평일 아침 | 월~금 06:30 | 06:55·07:15·08:05 | `daily.yml/dispatches` | 아래 daily |
| 토요일 주간 | 토 06:30 | 06:55·07:15·08:05 | `weekly.yml/dispatches` | 아래 weekly |
| 목요일 자료 | 목 09:40 | 10:00 | `thursday.yml/dispatches` | 아래 thursday |
| ETF 마감 저장 | 월~금 16:00 | 16:10·17:10·18:10 | `etf-close-snapshot.yml/dispatches` | `{"ref":"main"}` |

기본 URL:

`https://api.github.com/repos/hanaBBun/etf_morning_brief/actions/workflows/<파일명>/dispatches`

daily:

```json
{"ref":"main","inputs":{"skip_if_existing":"true","no_send":"false","replace_published":"false"}}
```

weekly:

```json
{"ref":"main","inputs":{"skip_if_existing":"true","no_send":"false"}}
```

thursday:

```json
{"ref":"main","inputs":{"skip_if_existing":"true","no_send":"false"}}
```

외부 예약은 1차 실행, GitHub 기본 예약은 백업이다. 모든 발행 예약은
`skip_if_existing=true`이므로 먼저 성공한 실행 뒤의 예약은 카톡을 다시 보내지 않는다.

## 4. 변경 규칙

- 기능 변경은 기존 테스트와 새 회귀 테스트를 함께 통과해야 한다.
- 공개 섹션이 비었다는 이유로 조용히 정상 처리하지 않는다. 운영자 알림으로만 보고한다.
- 독자용 HTML에는 수집 실패 문구를 넣지 않는다.
- 생성 결과(`docs`, 일일 캐시)와 코드 변경은 가능한 한 별도 커밋으로 유지한다.
- 운영 기준을 바꾸면 코드뿐 아니라 이 문서와 테스트를 함께 수정한다.

## 5. 회사 이전 체크리스트

- 회사 GitHub 조직 저장소로 이전
- 회사 소유 Gemini·YouTube 등 API 키를 GitHub Secrets에 등록
- 외부 스케줄러의 저장소 URL과 토큰 교체
- 회사 도메인 또는 회사 GitHub Pages 주소 적용
- `src/delivery.py`에 Slack Incoming Webhook 어댑터 추가
- 공개 브리핑과 운영자 오류 알림을 서로 다른 Slack 채널로 분리
- 개인 Kakao와 개인 `GH_PAT` 제거
