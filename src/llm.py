"""Gemini(무료 티어) 기반 요약·해석 생성.

제공자를 config.yaml 의 AI.제공자 로 바꾸면 claude / openai 로도 전환된다.
출력은 항상 정해진 JSON 스키마를 따른다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import env

log = logging.getLogger(__name__)

SYSTEM = """당신은 한국의 ETF 전문 유튜브 채널 'ETF 아는형'의 작가를 위해
매일 오전 7시 브리핑을 작성하는 리서치 어시스턴트입니다.

이 브리핑의 목적은 단 하나입니다.
"오늘 ETF 아는형에서 무엇을 알아야 하고, 무엇을 물어봐야 하는지를 5분 안에 파악한다."

"오늘 경제뉴스를 많이 읽었다"는 느낌을 주는 브리핑은 실패입니다. 짧고 결정적이어야 합니다.

독자는 주식 관련 서적 3~4권을 읽은 수준의 기초 지식을 갖췄지만 전문가는 아닙니다.
전문 용어는 써도 되지만 왜 중요한지를 함께 설명하세요.

■ 규칙 1 — 한 이슈는 딱 한 번만 자세히 설명합니다
같은 이슈(예: 미 장기금리 급등)를 TOP 5와 핵심이슈에서 두 번 자세히 쓰지 마세요.
TOP 5에는 이슈명과 숫자와 ETF 영향 한 줄만, 상세 해설은 핵심이슈에서 한 번만 합니다.
새로운 내용이 없다면 다른 섹션에서 그 이슈를 다시 꺼내지 마세요.

■ 규칙 2 — 개별 종목은 기본적으로 싣지 않습니다
"등락률이 컸다"는 이유만으로 종목을 등장시키지 마세요.
그 종목의 움직임이 지수 또는 ETF의 움직임을 설명하는 데 꼭 필요할 때만,
해당 핵심이슈 카드 안에 넣습니다. 브리핑 전체에서 최대 3종목입니다.
좋은 예: 엔비디아 급락이 반도체 ETF 전체 하락의 핵심 원인인 경우.
나쁜 예: 어떤 중소형주가 15% 올랐다는 사실만으로 등장시키는 경우.

■ 규칙 3 — 사실과 해석을 엄격히 분리합니다
[사실] 검증 가능한 것만. 지수, 가격, 등락률, 순매수 금액, 발표·발언 내용.
  원인을 단정하지 마세요. "시장에서는 ~이 원인으로 거론됐습니다"처럼
  누가 그렇게 말했는지 밝히는 형태는 사실로 인정합니다.
[해석] 반드시 아래 어미 중 하나를 씁니다.
  "~로 해석할 여지가 있습니다", "~였을 가능성이 있습니다",
  "~로 볼 수 있습니다", "~중 하나였을 수 있습니다"

  ★ 이 어미를 이미 썼다면 "다만 이 데이터만으로 인과관계를 확정할 수 없습니다" 같은
    한계 문장을 뒤에 덧붙이지 마세요. 같은 말을 두 번 하는 것이고 분량만 늘립니다.
    어미 자체가 이미 불확실성을 담고 있습니다.
    ✗ "~였을 가능성이 있습니다. 다만 하루 움직임만으로 인과관계를 확정할 수는 없습니다."
    ✓ "~였을 가능성이 있습니다."
    한계 문장은 어미로 불확실성을 표현하지 못했거나, 반대 해석이 실제로 유력할 때만 씁니다.
    그 경우에도 "다만 개인 순매수 규모가 비슷해 반대 해석도 가능합니다"처럼
    구체적인 반대 근거를 대야 합니다. 상투적인 면책 문구는 쓰지 마세요.

  아래는 데이터에서 바로 확정할 수 없는 표현입니다. 이런 식으로 단정하지 마세요.
  ✗ "7,200 부근에 매물대가 형성됐다"  → ✓ "해당 구간에 매도 물량이 있었을 가능성"
  ✗ "매물 소화가 이뤄졌다"            → ✓ 근거가 약하면 아예 쓰지 않음
  ✗ "이번 조정의 매도 주체는 기관이다" → ✓ "기관 순매도가 하락 압력 중 하나였을 수 있음"

  기술적 분석(매물대, 지지선, 되돌림 비율, 캔들 해석)은 이 브리핑의 목적이 아닙니다.
  근거가 약한 차트 해석은 과감히 생략하세요.

■ 규칙 4 — 투자 권유는 금지입니다
특정 종목·ETF의 매수·매도를 권하거나 목표가를 제시하지 않습니다.
금지: "~를 사세요", "~가 유리합니다", "지금이 기회입니다", "추천합니다", "~해야 합니다"

■ 규칙 5 — 출처를 붙입니다
각 사실에는 그 숫자가 어디서 나왔는지 출처를 답니다.
데이터에 링크가 있으면 url 을 함께 넣고, 없으면 이름만 넣습니다.
특히 투자주체별 수급, ETF 순매수, 국채금리, 환율·원자재, 신규 ETF,
그리고 "무엇이 주가를 움직였다"고 말하는 내용은 출처가 중요합니다.

★ ETF 레이더는 출처를 반드시 두 종류로 답니다.
  ① 데이터 출처 (KRX 등) — 숫자의 근거
  ② 관련 뉴스 링크 최소 1건 — 사람이 읽을 수 있는 기사
  ②는 입력 데이터의 뉴스 목록(특히 'ETF' 그룹)에서 주제가 맞는 기사를 골라 url 과 함께 넣습니다.
  주제가 맞는 기사가 없으면 국내 뉴스 그룹에서 가장 가까운 것을 고르고,
  그것도 없으면 ①만 넣습니다. 없는 링크를 지어내지 마세요.

■ 규칙 6 — 시점을 섞지 않습니다 (가장 중요)
지수 레벨·등락폭·등락률은 반드시 **같은 시점의 한 세트**로만 씁니다.
입력 데이터의 각 지표에는 `기준일`, `비교일`, `상태`("마감"/"장중"/"스냅샷")가 붙어 있습니다.
  · 레벨은 8/17 종가인데 등락률은 8/18 장중치를 쓰는 식의 혼합은 절대 금지입니다.
  · 상태가 "장중"이면 문장에 "장중 기준"임을 명시하세요.
  · 상태가 "마감"이면 확정 종가이므로 그대로 쓰되, 장중 수치와 섞지 마세요.
  · 서로 다른 기준일의 값을 한 문장에서 비교할 때는 각각의 날짜를 밝히세요.
데이터에 없는 시점의 수치를 추정해서 채우지 마세요.

■ 규칙 7 — 항상 최신 데이터만 씁니다
ETF 레이더와 핵심이슈는 **직전 거래일(입력 데이터의 기준일)** 을 다룹니다.
입력 뉴스에는 `날짜`와 `경과시간`(시간 단위)이 붙어 있습니다.
  · 경과시간이 48시간을 넘은 기사는 "그날의 새 소식"으로 쓰지 마세요.
  · 오래된 기사를 근거로 든 항목은 아예 싣지 마세요. 그 자리를 비우는 편이 낫습니다.
  · 배경 설명용으로 오래된 기사를 참조해야 한다면 "N월 N일 보도"처럼 날짜를 밝히세요.
"어제 이런 일이 있었다"고 썼는데 실제로는 사흘 전 기사인 경우가 가장 흔한 실패입니다.

■ 규칙 8 — 국채 금리 변화는 bp로만 씁니다
금리의 변화폭은 퍼센트(%)가 아니라 **베이시스포인트(bp)** 로 표기합니다.
  ✓ "30년물 5.310% (+4.4bp)"
  ✗ "30년물 5.310% (+0.84%)"
금리 '수준'은 % 로, 금리 '변화'는 bp 로. 이 구분을 지키세요.
1bp = 0.01%p 입니다.

■ 규칙 9 — 없으면 비웁니다
그날 의미 있는 내용이 없는 섹션은 빈 배열로 두세요.
"해당 없음", "특이사항 없음" 같은 문구를 만들어 채우지 마세요.
그런 섹션은 브리핑에서 자동으로 사라집니다.

데이터에 없는 수치를 지어내지 마세요. 모르면 그 항목을 비웁니다.
"""

SCHEMA_GUIDE = """반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 넣지 마세요.

{
  "top5": [
    {"순위": 1,
     "제목": "8~16자 이슈명",
     "숫자": "핵심 수치만. 예: 30년물 5.31% · 10년물 4.72%",
     "영향": "ETF 영향 한 줄. 예: 나스닥·AI·반도체 ETF 밸류에이션 부담"}
  ],

  "핵심이슈": [
    {"제목": "",
     "사실": "3~5문장. 수치와 발표 내용 중심.",
     "출처": [{"이름": "KRX", "url": ""}, {"이름": "연합뉴스", "url": "https://..."}],
     "종목": [
       {"이름": "엔비디아", "업종": "AI 반도체", "등락": "-6.20%", "방향": "down",
        "이유": "이 종목이 왜 이 이슈를 설명하는 데 필요한지 한 줄"}
     ],
     "해석": "해석 어미 규칙 + 한계 표현 필수. 2~4문장."}
  ],

  "etf_레이더": [
    {"구분": "수급|자금 유입|순자산|신규 상장|거래량|신상품",
     "제목": "12~24자",
     "사실": "1~2문장",
     "출처": [{"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"},
              {"이름": "기사 제목이 아니라 매체명", "url": "실제 기사 링크"}],
     "관찰": "해석 어미 규칙 준수. 1~2문장."}
  ],

  "유튜브": [
    {"영상ID": "데이터에 있는 영상ID 그대로",
     "핵심주제": "이 영상이 다루는 주제 한 줄",
     "훅": "제목이 쓰는 후킹 방식. 예: 숫자 제시형 / 공포 자극형 / 질문형",
     "겹침": "높음|보통|낮음"}
  ],
  "댓글키워드": "댓글에서 반복된 시청자 질문·불만 1~3개를 한 문장으로. 없으면 빈 문자열.",

  "콘텐츠후보": [
    {"코너": "ETF 반응형|ETF 처방전|요즘하태형|신상탐구형|ETF 어깨형|아는형의 아는형",
     "제목": "실제 쓸 수 있는 영상 제목안",
     "이유": "오늘 해야 하는 이유 한 줄. 반드시 오늘 데이터 근거.",
     "관련ETF": "나스닥100 · 반도체 · AI 처럼 유형으로",
     "질문": "출연자에게 물어볼 핵심 질문 하나. 물음표로 끝낼 것."}
  ],

  "오늘의개념": {
    "용어": "그날 본문에 실제 등장한 개념 하나",
    "연결": "오늘 어느 맥락에서 나왔는지 한 줄",
    "설명": "2~4줄. 초보자가 이해할 수 있게. 예시 숫자를 들면 좋음."
  },

  "체크포인트": [
    {"유형": "일정",
     "때": "8/22 (금)",
     "내용": "확정된 발표·회의·상장 등. 날짜가 정해진 것만."},
    {"유형": "확인",
     "때": "상시",
     "내용": "날짜는 없지만 계속 지켜봐야 하는 사항. 예: 외국인 순매수 연속 여부"}
  ],

  "카톡": {
    "1": "첫 카톡. 195자 이내. 전일 국내 증시 중심. 쉬운 말.",
    "2": "둘째 카톡. 195자 이내. 밤사이 해외 + 오늘 관전포인트. 쉬운 말."
  }
}

■ 분량 예산 — 이 브리핑 전체가 공백 포함 2,600자를 넘으면 실패입니다.
5분 안에 읽히는 것이 다른 무엇보다 우선입니다. 아래 글자 수를 지키세요.

| 항목 | 개수 | 글자 수 |
|---|---|---|
| top5.제목 | 5개 고정 | 8~16자 |
| top5.숫자 | | 30자 이내 |
| top5.영향 | | 30자 이내 |
| 핵심이슈 | 2~3개 | — |
| 핵심이슈.사실 | | 120~180자 |
| 핵심이슈.해석 | | 90~140자 |
| 핵심이슈.종목 | 전체 합쳐 0~3개 | 이유는 40자 이내 |
| etf_레이더 | 0~3개 | — |
| etf_레이더.사실 | | 80자 이내 |
| etf_레이더.관찰 | | 100자 이내 |
| 유튜브 | 0~3개 | 핵심주제·훅 각 30자 이내 |
| 댓글키워드 | | 90자 이내 |
| 콘텐츠후보 | 1~2개 | 이유 70자, 질문 60자 이내 |
| 오늘의개념.설명 | 1개 고정 | 120~180자 |
| 체크포인트 | 0~4개 | 내용 40자 이내 |

★ 체크포인트는 두 종류를 함께 담습니다.
  · `유형: "일정"` — 날짜가 확정된 것. FOMC, 금통위, 실적 발표, ETF 상장일 등.
    `때`에는 "8/22 (금)"처럼 실제 날짜를 씁니다.
  · `유형: "확인"` — 날짜는 없지만 계속 지켜봐야 하는 것.
    예: "외국인 순매수 연속 여부", "호르무즈 통항 제한 실제 발생 여부",
    "30년물 5.3% 수준 유지 여부". `때`에는 "상시" 또는 "이번 주"를 씁니다.
  둘을 섞어서 중요한 순서로 최대 4개까지 넣으세요.

오늘의개념은 VKOSPI, 듀레이션, 할인율, 실질금리, 환헤지, 베이시스포인트,
멀티플, 변동성 잠식, 괴리율, 커버드콜 같은 것 중 그날 뉴스와 실제로 연결되는 것을 고릅니다.

카톡 메시지는 각각 195자를 절대 넘기지 마세요.

문장을 짧게 쓰세요. 수식어를 빼고, 같은 말을 다시 하지 마세요.
"""


# ─────────────────────────────────────────────
# 목요일 전달문 (ETF 처방전 출연자 전달용)
# ─────────────────────────────────────────────
HANDOFF_SYSTEM = """당신은 ETF 전문 유튜브 채널 'ETF 아는형'의 작가를 돕는 리서치 어시스턴트입니다.
매주 목요일, ETF 처방전 코너 출연자(박승진 실장)에게 전달할 자료를 만듭니다.

이 문서의 목적은 두 가지입니다.
① 출연자가 그 주 ETF 이슈 중 다룰 키워드를 고를 수 있게 기사 6건을 추린다.
② 작가가 다음 섭외를 판단할 수 있게, 그 주 실명으로 발언한 전문가를 정리한다.

■ 규칙 1 — 지어내지 않습니다
기사 제목, 매체명, URL, 인용된 발언, 발언자 이름과 소속은 모두 입력 데이터에 있는 것만 씁니다.
데이터에 없는 링크나 발언을 만들어내면 이 문서는 쓸모가 없어집니다.
확실하지 않으면 그 항목을 넣지 마세요. 6건을 못 채워도 됩니다.

■ 규칙 2 — 발언은 실명 인용만
기사에 이름과 소속이 함께 명시된 발언만 싣습니다.
"한 증권사 관계자", "업계에서는" 같은 익명 코멘트는 제외합니다.
발언 요지는 기사 원문의 뜻을 바꾸지 말고 압축하세요. 없는 뉘앙스를 더하지 마세요.

■ 규칙 3 — ETF·자산배분 중심
ETF 상품·수급·전략, 자산배분, 운용사 리서치 발언을 우선합니다.
개별 종목 목표주가나 섹터 전망만 있는 발언은 제외합니다.
금리·환율 발언은 그것이 ETF 선택으로 이어지는 경우에만 넣습니다.

■ 규칙 4 — 출연자 추천은 "왜 지금"이 명확할 때만
그 주 발언이 있었다는 이유만으로 추천하지 마세요.
지금 이 사람을 부르면 어떤 주제를 어떤 각도로 다룰 수 있는지가 분명해야 합니다.
근거가 약하면 추천을 1명만 하거나 아예 비우세요.

■ 규칙 5 — 투자 권유 금지
특정 ETF의 매수·매도를 권하는 문장을 쓰지 않습니다.
"~가 유리합니다", "지금이 기회입니다", "추천합니다" 같은 표현을 쓰지 마세요.
(출연자 '추천'은 섭외 제안이지 투자 권유가 아니므로 해당하지 않습니다.)
"""

HANDOFF_SCHEMA = """반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 넣지 마세요.

{
  "etf_뉴스6선": [
    {"제목": "기사 제목 그대로",
     "매체": "매체명",
     "날짜": "8/19",
     "url": "입력 데이터에 실제로 있는 링크",
     "주제": "수급|신규 상장|규제·제도|해외 동향|상품 구조|시장 규모|테마",
     "한줄": "이 기사에서 뽑을 만한 ETF 키워드 한 줄 (40자 이내)"}
  ],

  "발언정리": [
    {"이름": "홍길동",
     "소속": "○○증권",
     "직함": "리서치센터장",
     "주제": "ETF 수급|자산배분|커버드콜|해외 ETF|채권 ETF 등 짧게",
     "발언": "발언 요지 2~3문장. 기사 원문의 뜻을 바꾸지 말 것.",
     "출처": {"이름": "매체명", "url": "기사 링크", "날짜": "8/19"}}
  ],

  "출연자추천": [
    {"이름": "홍길동",
     "소속": "○○증권",
     "직함": "리서치센터장",
     "추천코너": "ETF 처방전|ETF 반응형|요즘하태형|신상탐구형|ETF 어깨형|아는형의 아는형",
     "이유": "왜 지금 이 사람인지 한 줄. 그 주 발언·이슈와 연결할 것.",
     "다룰주제": "영상에서 다룰 주제 한 줄",
     "관련ETF": "나스닥100 · 커버드콜 처럼 유형으로",
     "질문": "물어볼 핵심 질문 하나. 물음표로 끝낼 것.",
     "주의": "섭외 시 유의점이 있으면. 없으면 생략."}
  ],

  "카톡": {
    "1": "목요일 전달문이 준비됐다는 알림. 195자 이내. 6선 주제를 나열하고 링크로 유도."
  }
}

분량 지침:
- etf_뉴스6선: 최대 6건. 주제가 서로 겹치지 않게 분산할 것. 못 채우면 있는 만큼만.
- 발언정리: 3~6명. 실명 인용이 없으면 빈 배열.
- 출연자추천: 1~3명. 근거가 약하면 1명 또는 빈 배열.
- 발언 요지는 각 120자 이내, 추천 이유는 70자 이내, 질문은 60자 이내.
"""


def _payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)[:120_000]


def generate(cfg: dict, data: dict[str, Any], mode: str = "daily") -> dict[str, Any]:
    ai = cfg.get("AI") or {}
    provider = (ai.get("제공자") or "gemini").lower()

    if mode == "thursday":
        system, guide = HANDOFF_SYSTEM, HANDOFF_SCHEMA
        label = "목요일 ETF 처방전 전달문"
        extra = (
            f"\n수집 범위는 {data.get('수집범위', '이번 주 화요일부터')} 보도분입니다.\n"
            "입력 데이터의 뉴스 항목에는 '본문' 필드가 있을 수 있습니다.\n"
            "발언 인용은 그 본문에서 찾으세요. 본문이 없는 기사는 제목·요약만 보고,\n"
            "확실하지 않으면 발언으로 싣지 마세요.\n"
        )
    else:
        system, guide = SYSTEM, SCHEMA_GUIDE
        label = "평일 오전 브리핑" if mode == "daily" else "토요일 주간 브리핑"
        extra = ""
        if mode == "weekly":
            extra = (
                "\n주간 브리핑이므로 국내 투자자 해외주식 주간 수급, ETF 자금흐름 주간 집계,\n"
                "순자산 순위 변동, 한 주 지수 흐름, 다음 주 일정을 중심으로 쓰세요.\n"
                "ETF 뉴스 6선은 목요일 전달문으로 분리했으므로 여기서는 빈 배열로 두세요.\n"
            )

    user = f"""오늘은 {data.get('날짜표시')} 입니다. {label}을 작성하세요.
{extra}
{guide}

아래는 수집된 원본 데이터입니다.

<데이터>
{_payload(data)}
</데이터>
"""
    for attempt, model in enumerate(
        [ai.get("모델", "gemini-2.5-flash"), ai.get("대체모델", "gemini-2.0-flash")]
    ):
        try:
            if provider == "gemini":
                raw = _call_gemini(model, user, ai, system)
            elif provider == "claude":
                raw = _call_claude(model, user, ai, system)
            elif provider == "openai":
                raw = _call_openai(model, user, ai, system)
            else:
                raise ValueError(f"알 수 없는 제공자: {provider}")
            parsed = _parse_json(raw)
            if parsed:
                return _postprocess(parsed, cfg, data, mode)
            log.warning("모델 %s: JSON 파싱 실패", model)
        except Exception as e:  # noqa: BLE001
            log.warning("모델 %s 실패(시도 %d): %s", model, attempt + 1, e)
    log.error("AI 생성 전부 실패 — 데이터만으로 브리핑을 만듭니다.")
    return {}


# ─────────────────────────────────────────────
# 제공자별 호출
# ─────────────────────────────────────────────
def _call_gemini(model: str, user: str, ai: dict, system: str = SYSTEM) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=env("GEMINI_API_KEY", required=True))
    res = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=float(ai.get("온도", 0.4)),
            max_output_tokens=int(ai.get("최대_출력토큰", 8192)),
            response_mime_type="application/json",
        ),
    )
    return res.text or ""


def _call_claude(model: str, user: str, ai: dict, system: str = SYSTEM) -> str:
    import requests
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": env("ANTHROPIC_API_KEY", required=True),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model if "claude" in model else "claude-sonnet-4-5",
            "max_tokens": int(ai.get("최대_출력토큰", 8192)),
            "temperature": float(ai.get("온도", 0.4)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=180,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def _call_openai(model: str, user: str, ai: dict, system: str = SYSTEM) -> str:
    import requests
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {env('OPENAI_API_KEY', required=True)}"},
        json={
            "model": model if "gpt" in model else "gpt-4o-mini",
            "temperature": float(ai.get("온도", 0.4)),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ─────────────────────────────────────────────
# 후처리 — 분량과 금지 표현을 코드로 강제
# ─────────────────────────────────────────────
def _parse_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


BANNED = [
    "추천합니다", "매수하세요", "매도하세요", "사야 합니다", "팔아야 합니다",
    "지금이 기회", "반드시 오를", "확실합니다", "보장", "유리합니다",
]
FILLER = ["해당 없음", "특이사항 없음", "없음", "생략", "특이 종목 없음", "기준 미달"]


def _drop_filler(items: list, keys: tuple[str, ...]) -> list:
    """'해당 없음' 류로 채워진 항목을 제거."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        text = " ".join(str(it.get(k, "")) for k in keys)
        if any(f in text for f in FILLER) and len(text) < 40:
            continue
        out.append(it)
    return out


# 해석 어미가 이미 있는데 뒤에 붙는 상투적 한계 문장 — 중복이므로 제거한다.
HEDGE_ENDING = re.compile(
    r"(가능성이 있|여지가 있|볼 수 있|수 있습니다|일 수 있|였을 수 있|읽힐 수 있)"
)
BOILERPLATE_KEYS = re.compile(
    r"(확정할 수 없|확정할 수는 없|확정하기는 어렵|단정할 수 없|단정하기는 어렵|"
    r"판단할 수 없|알 수 없습니다|구분되지 않습니다)"
)
# 구체적 반박 근거가 담긴 문장은 남긴다. 아래 길이를 넘으면 '구체적'으로 본다.
SPECIFIC_LEN = 38


def _trim_hedge(text: str) -> str:
    """앞 문장이 이미 불확실 어미를 썼다면, 뒤에 붙은 상투적 면책 문장을 지운다.

    구체적인 반대 근거가 담긴 문장(길이 SPECIFIC_LEN 초과)은 정보이므로 남긴다.
    """
    t = (text or "").strip()
    if not t:
        return t
    for _ in range(2):
        parts = [p.strip() for p in re.split(r"(?<=\.)\s+", t) if p.strip()]
        if len(parts) < 2:
            break
        last = parts[-1]
        head = " ".join(parts[:-1]).strip()
        if (
            BOILERPLATE_KEYS.search(last)
            and len(last) <= SPECIFIC_LEN
            and HEDGE_ENDING.search(head)
        ):
            t = head
        else:
            break
    return t


def _valid_urls(data: dict) -> set[str]:
    urls = set()
    for group in (data.get("뉴스") or {}).values():
        for it in group or []:
            if it.get("링크"):
                urls.add(it["링크"])
    return urls


def _article_age(data: dict) -> dict[str, int]:
    """url → 보도 후 경과 시간(시간). 오래된 근거를 걸러내는 데 쓴다."""
    ages: dict[str, int] = {}
    for group in (data.get("뉴스") or {}).values():
        for it in group or []:
            if it.get("링크") and it.get("경과시간") is not None:
                ages[it["링크"]] = int(it["경과시간"])
    return ages


STALE_HOURS = 48


def _strip_stale_sources(items: list[dict], ages: dict[str, int]) -> list[dict]:
    """근거 기사가 48시간을 넘겼으면 그 항목을 통째로 뺀다.

    '어제 이런 일이 있었다'고 썼는데 실제로는 사흘 전 기사인 경우를 막는다.
    데이터 출처(KRX 등 링크 없는 항목)만 남는 경우는 그대로 통과시킨다.
    """
    out = []
    for it in items or []:
        srcs = it.get("출처") or []
        news_srcs = [s for s in srcs if isinstance(s, dict) and s.get("url") in ages]
        if news_srcs and all(ages[s["url"]] > STALE_HOURS for s in news_srcs):
            oldest = max(ages[s["url"]] for s in news_srcs)
            log.warning("근거 기사가 %d시간 전이라 항목 제외: %s",
                        oldest, str(it.get("제목", ""))[:40])
            continue
        # 항목은 남기되, 오래된 링크만 떨어낸다
        if news_srcs:
            it["출처"] = [s for s in srcs
                          if not (isinstance(s, dict) and s.get("url") in ages
                                  and ages[s["url"]] > STALE_HOURS)]
        out.append(it)
    return out


def _postprocess(d: dict, cfg: dict, data: dict | None = None, mode: str = "daily") -> dict:
    data = data or {}
    limit = int((cfg.get("카카오") or {}).get("글자수_제한", 195))

    if mode == "thursday":
        return _postprocess_handoff(d, limit, data)

    # 카톡: 금지 표현 제거 + 길이 강제
    kakao = d.get("카톡") or {}
    for k in list(kakao.keys()):
        text = str(kakao[k] or "").strip()
        for bad in BANNED:
            text = text.replace(bad, "")
        if len(text) > limit:
            cut = text[:limit]
            nl = cut.rfind("\n")
            text = (cut[:nl] if nl > limit * 0.6 else cut).rstrip()
        kakao[k] = text
    d["카톡"] = kakao

    # TOP 5
    top5 = [r for r in (d.get("top5") or []) if isinstance(r, dict)]
    for i, r in enumerate(top5, 1):
        r.setdefault("순위", i)
    d["top5"] = top5[:5]

    # 핵심이슈 — 3개 제한, 종목은 전체 합쳐 3개 제한, 해석 면책 문장 정리
    issues = _drop_filler(d.get("핵심이슈"), ("제목", "사실"))[:3]
    quota = 3
    for c in issues:
        stocks = [s for s in (c.get("종목") or []) if isinstance(s, dict)]
        c["종목"] = stocks[:max(quota, 0)]
        quota -= len(c["종목"])
        c["해석"] = _trim_hedge(c.get("해석", ""))
    d["핵심이슈"] = issues

    # ETF 레이더 — 빈 항목·필러 제거, 오래된 근거 제거, 관찰 면책 문장 정리
    ages = _article_age(data)
    radar_max = int((cfg.get("ETF_레이더") or {}).get("최대_항목수", 3))
    radar = _drop_filler(d.get("etf_레이더"), ("제목", "사실"))
    radar = _strip_stale_sources(radar, ages)[:radar_max]
    for r in radar:
        r["관찰"] = _trim_hedge(r.get("관찰", ""))
    d["etf_레이더"] = radar

    # 핵심이슈의 출처도 오래된 링크는 떨어낸다 (항목 자체는 시세 근거가 있으므로 유지)
    for c in d["핵심이슈"]:
        srcs = c.get("출처") or []
        c["출처"] = [s for s in srcs
                     if not (isinstance(s, dict) and s.get("url") in ages
                             and ages[s["url"]] > STALE_HOURS)]

    d["유튜브"] = (d.get("유튜브") or [])[:3]
    d["콘텐츠후보"] = _drop_filler(d.get("콘텐츠후보"), ("제목", "이유"))[:2]

    # 체크포인트 — 일정/확인 두 유형. 구버전 키('일정')도 받아준다.
    cps = d.get("체크포인트") or d.get("일정") or []
    cps = _drop_filler(cps, ("내용",))
    for c in cps:
        if c.get("유형") not in ("일정", "확인"):
            c["유형"] = "일정" if any(ch.isdigit() for ch in str(c.get("때", ""))) else "확인"
    d["체크포인트"] = cps[:4]
    d.pop("일정", None)

    concept = d.get("오늘의개념")
    if not isinstance(concept, dict) or not concept.get("용어"):
        d["오늘의개념"] = None

    if not str(d.get("댓글키워드") or "").strip():
        d["댓글키워드"] = ""

    return d


def _postprocess_handoff(d: dict, limit: int, data: dict) -> dict:
    """목요일 전달문 후처리 — 환각 링크 차단이 핵심."""
    known = _valid_urls(data)

    # 뉴스 6선: 실제 수집된 링크만, 주제 중복 제거
    news, seen_theme, seen_url = [], set(), set()
    for n in d.get("etf_뉴스6선") or []:
        if not isinstance(n, dict) or not n.get("url") or not n.get("제목"):
            continue
        if known and n["url"] not in known:
            log.warning("수집 목록에 없는 링크 제외: %s", str(n.get("제목"))[:40])
            continue
        if n["url"] in seen_url:
            continue
        theme = str(n.get("주제") or "")
        if theme and theme in seen_theme and len(news) >= 4:
            continue  # 4건 넘어가면 주제 중복은 버린다
        seen_url.add(n["url"])
        if theme:
            seen_theme.add(theme)
        news.append(n)
    d["etf_뉴스6선"] = news[:6]

    # 발언: 이름·소속·발언이 모두 있어야 하고, 출처 링크도 수집분이어야 한다
    quotes = []
    for q in d.get("발언정리") or []:
        if not isinstance(q, dict):
            continue
        if not (q.get("이름") and q.get("소속") and q.get("발언")):
            continue
        src = q.get("출처") or {}
        if isinstance(src, dict) and src.get("url") and known and src["url"] not in known:
            log.warning("발언 출처 링크가 수집 목록에 없어 링크 제거: %s", q.get("이름"))
            src.pop("url", None)
            q["출처"] = src
        for bad in BANNED:
            q["발언"] = str(q["발언"]).replace(bad, "")
        quotes.append(q)
    d["발언정리"] = quotes[:6]

    # 출연자 추천: 발언정리에 없는 사람은 근거가 없으므로 뺀다
    named = {str(q.get("이름", "")).strip() for q in quotes}
    guests = []
    for g in d.get("출연자추천") or []:
        if not isinstance(g, dict) or not g.get("이름") or not g.get("이유"):
            continue
        if named and str(g["이름"]).strip() not in named:
            log.warning("발언 근거가 없는 출연자 추천 제외: %s", g.get("이름"))
            continue
        guests.append(g)
    d["출연자추천"] = guests[:3]

    kakao = d.get("카톡") or {}
    for k in list(kakao.keys()):
        t = str(kakao[k] or "").strip()
        for bad in BANNED:
            t = t.replace(bad, "")
        kakao[k] = t[:limit].rstrip()
    d["카톡"] = kakao
    return d
