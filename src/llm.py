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
"오늘 ETF 아는형에서 무엇을 알아야 하고, 무엇을 물어봐야 하는지를 3분 안에 파악한다."

"오늘 경제뉴스를 많이 읽었다"는 느낌을 주는 브리핑은 실패입니다. 짧고 결정적이어야 합니다.

독자는 주식 관련 서적 3~4권을 읽은 수준의 기초 지식을 갖췄지만 전문가는 아닙니다.
전문 용어는 써도 되지만 왜 중요한지를 함께 설명하세요.

■ 규칙 1 — 한 이슈는 딱 한 번만 자세히 설명합니다
같은 이슈(예: 미 장기금리 급등)를 TOP 3와 시장브리핑에서 두 번 자세히 쓰지 마세요.
TOP 3에는 이슈명·숫자·ETF 영향만, 상세 해설은 '오늘 시장은 왜 움직였나'에서만 합니다.
새로운 내용이 없다면 다른 섹션에서 그 이슈를 다시 꺼내지 마세요.

■ 규칙 2 — 개별 종목은 기본적으로 싣지 않습니다
"등락률이 컸다"는 이유만으로 종목을 등장시키지 마세요.
그 종목의 움직임이 지수 또는 ETF의 움직임을 설명하는 데 꼭 필요할 때만,
해당 시장브리핑 안에 넣습니다. 브리핑 전체에서 최대 3종목입니다.
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

■ 규칙 4 — 투자 권유·근거 없는 과장 표현은 금지입니다
특정 종목·ETF의 매수·매도를 권하거나 목표가를 제시하지 않습니다.
금지: "~를 사세요", "~가 유리합니다", "지금이 기회입니다", "추천합니다", "~해야 합니다"
근거 수치가 입력에 없으면 "폭발", "쏠림", "집중", "주도", "견인", "급증"도 쓰지 마세요.

■ 규칙 4-1 — 거래대금·순매수·자금유입을 절대 바꿔 쓰지 않습니다
세 지표는 전혀 다른 뜻입니다.
  · 거래대금: 매수와 매도가 오간 총 거래 규모
  · 순매수: 매수 금액에서 매도 금액을 뺀 값
  · 자금유입: 설정액·순자산 또는 펀드 플로 데이터로 확인된 유입
입력에 거래대금만 있으면 "돈이 몰렸다", "1조원이 유입됐다", "순매수했다"고 쓰지 마세요.
거래대금은 반드시 "거래대금이 1조원을 기록했다"처럼 그대로 표현합니다.

■ 규칙 4-2 — ETF의 종류를 정확히 구분합니다
삼성전자·SK하이닉스 각각을 기초자산으로 하는 상품은 "단일종목 레버리지 ETF"입니다.
이를 업종 전체를 담는 "반도체 ETF"나 "반도체 레버리지 ETF"로 바꿔 부르지 마세요.
업종형·지수형·단일종목형·레버리지·인버스를 입력 기사에 적힌 분류 그대로 씁니다.

■ 규칙 4-3 — 시장 전체 수급을 개별 종목 수급으로 확대하지 않습니다
코스피 외국인 순매수 데이터만으로 "외국인이 SK하이닉스에 집중 매수했다"고 쓰지 마세요.
종목별 투자주체 데이터가 입력에 없으면 종목 설명에는 등락률과 지수 기여만 씁니다.

■ 규칙 5 — 출처를 붙입니다
각 사실에는 그 숫자가 어디서 나왔는지 출처를 답니다.
기사가 근거면 그 기사의 id("n7" 같은 번호)를 넣습니다. url 을 옮겨 적지 마세요.
입력에서 같은 지표의 수치가 출처별로 다르면 섞어서 하나의 수치로 만들지 마세요.
구조화된 KRX 데이터가 있으면 그 값을 우선하고, 기사만 있으면 "출처별 수치 차이" 또는 범위를 명시합니다.
미국 지수·금리·원자재의 데이터 출처는 Yahoo Finance이며 KRX를 붙이지 않습니다.
국내 지수·수급의 데이터 출처는 KRX이며 Yahoo Finance를 붙이지 않습니다.
시장이 움직인 '원인'을 설명하려면 반드시 그 원인을 다룬 최근 기사 id를 함께 붙입니다.
특히 투자주체별 수급, ETF 순매수, 국채금리, 환율·원자재, 신규 ETF,
그리고 "무엇이 주가를 움직였다"고 말하는 내용은 출처가 중요합니다.

★ ETF 레이더는 출처를 반드시 두 종류로 답니다.
  ① 데이터 출처 (KRX 등) — 숫자의 근거
  ② 관련 뉴스 링크 최소 1건 — 사람이 읽을 수 있는 기사
  ②는 입력 데이터의 뉴스 목록에서 주제가 맞는 기사를 골라 id 로 넣습니다.
  주제가 맞는 기사가 없으면 국내 뉴스 그룹에서 가장 가까운 것을 고르고,
  그것도 없으면 ①만 넣습니다. 없는 링크를 지어내지 마세요.

■ 규칙 5-1 — ETF 레이더는 '상품'이 아니라 '시장'을 봅니다 (중요)
이 섹션은 특정 상품의 실적을 옮겨 적는 자리가 아닙니다.
ETF 시장 전체에서 지금 무슨 일이 벌어지는지를 보여주는 자리입니다.

  고를 때의 우선순위 (위쪽이 먼저):
  ① 시장 구조 — 거래대금 증감, 규제·제도 변경, 상장폐지, 세제
  ② 판매·접근 채널 — 퇴직연금·ISA·연금저축에서의 ETF 매매 조건 변화
  ③ 자금 흐름 — 유형별(채권형·커버드콜·해외주식형) 자금 이동, 국내→해외 이동
  ④ 지수 편입·정기변경, 레버리지·인버스 규제
  ⑤ 유형·테마 쏠림 — "어떤 성격의 ETF로 돈이 몰리는가"
  ⑥ 개별 상품 소식 — 신규 상장·보수 인하처럼 시장에 의미가 있을 때만

  ⑥은 세 항목 중 **최대 1개**까지만 쓸 수 있습니다.

  "○○운용 ○○ETF 순자산 4,000억 돌파", "△△ETF 개인 순매수 1위" 같은
  단일 상품 실적은 그 자체로는 레이더 항목이 아닙니다. 운용사 홍보 자료를
  그대로 옮긴 것이기 때문입니다. 같은 흐름이 같은 유형의 다른 상품에서도
  확인될 때만 '유형' 단위로 묶어서 쓰세요.
  ✗ "ACE 고배당주커버드콜 개인 순매수 1,000억 돌파"
  ✓ "커버드콜 ETF로 개인 자금 유입 지속 — 상위 3종 합산 순매수 …"

  운용사 보도자료의 홍보 문구는 옮기지 마세요.
  금지: "업계 최초", "차별화된", "주목받고 있다", "인기를 끌고 있다", "돌풍"
  상품명·보수율·상장일·순자산 같은 검증 가능한 숫자만 씁니다.

  입력 뉴스 그룹 중 'ETF시장'·'레버리지'·'지수' 가 ①~⑤에 해당하는 재료이고,
  '보도자료'는 ⑥ 재료입니다. 보도자료 그룹만으로 세 항목을 채우지 마세요.

■ 규칙 6 — 시점을 섞지 않습니다 (가장 중요)
지수 레벨·등락폭·등락률은 반드시 **같은 시점의 한 세트**로만 씁니다.
입력 데이터의 각 지표에는 `기준일`, `비교일`, `상태`("마감"/"장중"/"스냅샷")가 붙어 있습니다.
  · 레벨은 8/17 종가인데 등락률은 8/18 장중치를 쓰는 식의 혼합은 절대 금지입니다.
  · 상태가 "장중"이면 문장에 "장중 기준"임을 명시하세요.
  · 상태가 "마감"이면 확정 종가이므로 그대로 쓰되, 장중 수치와 섞지 마세요.
  · 서로 다른 기준일의 값을 한 문장에서 비교할 때는 각각의 날짜를 밝히세요.
데이터에 없는 시점의 수치를 추정해서 채우지 마세요.

■ 규칙 7 — 항상 최신 데이터만 씁니다
ETF 레이더와 시장브리핑은 **직전 거래일(입력 데이터의 기준일)** 을 다룹니다.
오늘 국내장의 장중 속보·사이드카·등락률은 TOP 3·시장브리핑·카톡에 넣지 마세요.
이 문서는 오전 7시 발송용이므로 직전 마감 데이터만 일관되게 설명합니다.
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

■ 규칙 8-1 — 유튜브 '겹침'은 후하게 주지 않습니다
겹침은 "이 영상이 오늘 우리가 다루려는 주제를 이미 선점했는가"를 뜻합니다.
같은 경제 유튜브라는 이유로, 또는 둘 다 증시를 다룬다는 이유로 '높음'을 주지 마세요.

  높음 — 오늘 브리핑의 TOP5 또는 ETF 레이더에 있는 **바로 그 이슈**를
         정면으로 다뤘고, ETF·자산배분 관점까지 겹치는 경우.
  보통 — 같은 자산군·테마를 다루지만 각도가 다른 경우.
         (예: 우리는 반도체 ETF 수급, 그 영상은 개별 반도체주 실적)
  낮음 — 개별 종목 분석, 부동산, 재테크 일반, 일일 증시 브리핑 등
         우리 주제와 직접 닿지 않는 경우. **대부분은 여기에 해당합니다.**

  판단이 애매하면 낮은 쪽을 고르세요. 겹침을 과대평가하면
  멀쩡한 기획을 접게 되므로, 놓치는 것보다 나쁩니다.

  ★ 고를 때는 ETF를 다룬 영상을 먼저 고릅니다.
    입력 영상에는 `ETF관련`(true/false)과 `ETF점수`가 붙어 있습니다.
    `ETF관련: true` 인 영상이 있으면 그것부터 채우고,
    하나도 없으면 일반 경제 영상 중 조회수 상위 1~2개만 담으세요.
    ETF와 무관한 영상을 세 칸 다 채우지 마세요.

■ 규칙 9 — 없으면 비웁니다
그날 의미 있는 내용이 없는 섹션은 빈 배열로 두세요.
"해당 없음", "특이사항 없음" 같은 문구를 만들어 채우지 마세요.
그런 섹션은 브리핑에서 자동으로 사라집니다.

데이터에 없는 수치를 지어내지 마세요. 모르면 그 항목을 비웁니다.
"""

SCHEMA_GUIDE = """반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 넣지 마세요.

■ 출처는 URL 이 아니라 '번호'로 답합니다
입력 데이터의 기사에는 "id": "n7" 같은 번호가 붙어 있습니다.
기사를 근거로 쓸 때는 URL 을 옮겨 적지 말고 그 번호를 그대로 쓰세요.
  ✓ {"이름": "매일경제", "id": "n7"}
  ✗ {"이름": "매일경제", "url": "https://news.google.com/rss/articles/CBMi..."}
링크는 우리가 번호로 찾아 붙입니다. URL 을 지어내면 그 항목은 버려집니다.
(KRX·야후파이낸스처럼 기사가 아닌 출처만 url 을 직접 써도 됩니다.)

{
  "시장브리핑": [
    {"시장": "미국",
     "제목": "결과와 핵심 원인이 함께 드러나는 제목",
     "결과": "주요 지수·금리·원자재·특징주의 핵심 숫자로 무슨 일이 있었는지 1~2문장",
     "원인": "가장 중요한 원인 2~3개와 각 원인이 시장에 전달된 경로를 2~3문장",
     "ETF연결": "국내 ETF 투자자와 ETF 아는형 작가가 오늘 어떤 자산군·테마를 연결해 봐야 하는지 1~2문장",
     "출처": [{"이름": "Yahoo Finance", "url": "https://finance.yahoo.com"},
              {"이름": "원인을 다룬 매체명", "id": "입력 기사 id"}]},
    {"시장": "국내", "제목": "", "결과": "", "원인": "", "ETF연결": "",
     "출처": [{"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"},
              {"이름": "원인을 다룬 매체명", "id": "입력 기사 id"}]}
  ],

  "오늘관전": ["지표·수급·이벤트 중 확인할 포인트 1", "포인트 2", "포인트 3"],

  "top5": [
    {"순위": 1,
     "제목": "8~16자 이슈명",
     "숫자": "핵심 수치만. 예: 30년물 5.31% · 10년물 4.72%",
     "영향": "ETF 영향 한 줄. 예: 나스닥·AI·반도체 ETF 밸류에이션 부담"}
  ],

  "etf_레이더": [
    {"구분": "시장 구조|제도·채널|자금 흐름|지수 변경|레버리지|유형 쏠림|신규 상장",
     "제목": "12~24자",
     "사실": "1~2문장",
     "출처": [{"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"},
              {"이름": "기사 제목이 아니라 매체명", "id": "입력 기사의 id. 예: n12"}],
     "관찰": "해석 어미 규칙 준수. 1~2문장."}
  ],

  "유튜브": [
    {"영상ID": "데이터에 있는 영상ID 그대로",
     "핵심주제": "이 영상이 다루는 주제 한 줄",
     "훅": "제목이 쓰는 후킹 방식. 예: 숫자 제시형 / 공포 자극형 / 질문형",
     "겹침": "높음|보통|낮음",
     "겹침근거": "왜 그 등급인지 15자 이내. 예: TOP2와 같은 이슈 / 종목 얘기라 무관"}
  ],
  "댓글키워드": "댓글에서 반복된 시청자 질문·불만 1~3개를 한 문장으로. 없으면 빈 문자열.",

  "콘텐츠후보": [
    {"코너": "ETF 반응형|ETF 처방전|요즘하태형|신상탐구형|ETF 어깨형|아는형의 아는형",
     "제목": "실제 쓸 수 있는 영상 제목안",
     "이유": "오늘 해야 하는 이유 한 줄. 반드시 오늘 데이터 근거.",
     "관련ETF": "나스닥100 · 반도체 · AI 처럼 유형으로",
     "질문": "출연자에게 물어볼 핵심 질문 하나. 물음표로 끝낼 것.",
     "차별점": "경쟁 채널과 소재가 겹치면 우리가 달리 볼 각도. 없으면 빈 문자열."}
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
    "1": "빈 문자열. 국내·미국·ETF 핵심 1·2·3은 후처리에서 자동 생성."
  }
}

■ 분량 예산 — 이 브리핑 전체가 공백 포함 2,200자를 넘으면 실패입니다.
3분 안에 읽히는 것이 다른 무엇보다 우선입니다. 아래 글자 수를 지키세요.

| 항목 | 개수 | 글자 수 |
|---|---|---|
| 시장브리핑 | 미국·국내 2개 | 각 전체 350자 이내 |
| 시장브리핑.결과 | | 100자 이내 |
| 시장브리핑.원인 | | 160자 이내 |
| 시장브리핑.ETF연결 | | 100자 이내 |
| 오늘관전 | 3개 | 각 60자 이내 |
| top5.제목 | 3개 고정 | 8~16자 |
| top5.숫자 | | 30자 이내 |
| top5.영향 | | 30자 이내 |
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
  뉴스에 날짜가 명시된 경제지표·중앙은행·실적·ETF 상장 일정이 있으면
  `일정`을 최소 1개 우선 편성합니다. 확인되지 않은 날짜는 만들지 않습니다.

★ 시장브리핑은 전사 공유용이자 ETF 아는형 작가용 핵심 해설입니다.
  · 미국·국내를 각각 1개씩 쓰고, '결과 → 원인 → ETF연결' 순서를 지킵니다.
  · 가장 큰 지수 변동을 설명하는 재료를 먼저 고릅니다. 대형 소비주 실적, 중앙은행 발언,
    국채 수급, 유가·지정학 등이 지수에 영향을 줬다면 누락하지 않습니다.
  · 원인은 나열하지 말고 '금리 상승 → 성장주 할인율 부담 → 나스닥 약세'처럼 전달 경로를 설명합니다.
  · ETF연결은 매수 추천이 아니라, 작가가 어떤 ETF군·테마·질문을 이어서 봐야 하는지 설명합니다.
  · 하루 등락만으로 추세 전환을 단정하지 마세요.

★ ETF 레이더의 `사실`은 입력에 수치가 있다면 등락률·순매수·순자산·거래대금 중
  최소 1개를 그대로 포함합니다. 수치 없이 "관심 증가", "강세", "주목"만 쓴 항목은 만들지 마세요.

오늘의개념은 VKOSPI, 듀레이션, 할인율, 실질금리, 환헤지, 베이시스포인트,
멀티플, 변동성 잠식, 괴리율, 커버드콜 같은 것 중 그날 뉴스와 실제로 연결되는 것을 고릅니다.

★ 카톡은 후처리에서 국내·미국·ETF 핵심을 1·2·3 한 메시지로 자동 생성합니다.
  모델은 카톡 문안을 따로 작문하지 마세요.
  - 각 메시지 195자를 절대 넘기지 마세요. 넘칠 것 같으면 숫자 줄을 줄이세요.

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
인용된 발언, 발언자 이름과 소속은 모두 입력 데이터에 있는 것만 씁니다.
기사를 가리킬 때는 URL 이나 제목을 옮겨 적지 말고 입력에 붙은 id("n7")만 씁니다.
제목·매체·날짜·링크는 그 id 로 우리가 원본에서 가져다 붙입니다.
발언은 확실하지 않으면 넣지 마세요.

■ 규칙 1-0 — 6선은 '시장' 기사부터 고릅니다
운용사가 배포한 홍보성 기사("○○운용 △△ETF 순자산 4,000억 돌파",
"개인 순매수 1,000억 돌파")로 6선을 채우면 출연자에게 아무 쓸모가 없습니다.
출연자는 이미 그런 자료를 매일 받습니다.

  고르는 순서:
  ① 시장 구조 — 거래대금 증감, 규제, 상장폐지, 제도 변경
  ② 판매·접근 채널 — 퇴직연금·ISA·연금저축에서의 ETF 매매 조건
  ③ 자금 흐름 — 유형별·국가별 자금 이동 ("국내→미국 ETF" 같은)
  ④ 지수 편입·정기변경, 레버리지·인버스 규제
  ⑤ 유형·테마 쏠림 — 어떤 성격의 ETF로 돈이 몰리는가
  ⑥ 개별 상품 소식 — 신규 상장·보수 인하

  ⑥은 6건 중 **최대 2건**까지만. 나머지 4건은 ①~⑤에서 채웁니다.
  입력의 'ETF시장'·'증권'·'국내'·'레버리지'·'지수' 그룹이 ①~⑤ 재료이고,
  '보도자료' 그룹은 ⑥ 재료입니다.

■ 규칙 1-1 — 뉴스 6선은 반드시 6건입니다
입력에는 스무 건이 넘는 기사가 들어옵니다. 그중 ETF 관점에서 쓸 만한 것을
**6건 골라서 반드시 6건을 채우세요.** 5건이나 3건으로 끝내지 마세요.
'딱 맞는 기사가 없다'는 이유로 비우지 말고, 관련도가 높은 순서대로 6번째까지 채웁니다.
(발언정리·출연자추천과 달리 6선은 '고르는' 일이라 지어낼 위험이 없습니다.)

■ 규칙 2 — 발언은 실명 인용만
기사에 이름과 소속이 함께 명시된 발언만 싣습니다.
"한 증권사 관계자", "업계에서는" 같은 익명 코멘트는 제외합니다.
발언 요지는 기사 원문의 뜻을 바꾸지 말고 압축하세요. 없는 뉘앙스를 더하지 마세요.

■ 규칙 3 — ETF·자산배분 중심
ETF 상품·수급·전략, 자산배분, 운용사 리서치 발언을 우선합니다.
개별 종목 목표주가나 섹터 전망만 있는 발언은 제외합니다.
금리·환율 발언은 그것이 ETF 선택으로 이어지는 경우에만 넣습니다.

■ 규칙 3-1 — 입력 뉴스 그룹별 성격
입력의 '뉴스'는 여러 그룹으로 나뉘어 들어옵니다. 그룹마다 쓰임이 다릅니다.
- ETF: 일반 ETF 기사. 6선의 기본 재료입니다.
- 보도자료: 운용사가 배포한 신규 상장·보수 인하·순자산 발표가 원출처인 기사입니다.
  "무엇이 새로 나왔는가"를 확인하는 데 씁니다. 다만 홍보 문구("업계 최초",
  "차별화된", "주목받고 있다")는 옮기지 말고 상품명·보수율·상장일 같은
  검증 가능한 사실만 뽑으세요. 운용사 주장을 시장의 평가처럼 쓰지 마세요.
- 레버리지: 레버리지·인버스 ETF 수급과 규제 기사입니다. 개인 수급 쏠림을 볼 때 씁니다.
- 지수: 지수 편입·제외·정기변경·리밸런싱 기사입니다. ETF 구성 종목이 바뀌는 이벤트라
  "언제 무엇이 바뀌는지"가 명시된 경우에만 6선에 넣으세요.
- 국내: 증시 전반 기사입니다. ETF 이슈의 배경을 설명할 때만 씁니다.
6선은 한 그룹에 몰리지 않게 고르되, 억지로 그룹을 채우지는 마세요.

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
    {"id": "입력 기사의 id 를 그대로. 예: n7",
     "주제": "수급|신규 상장|보수·비용|지수 변경|레버리지·인버스|규제·제도|해외 동향|상품 구조|시장 규모|테마",
     "한줄": "이 기사에서 뽑을 만한 ETF 키워드 한 줄 (40자 이내)"}
  ],

  "발언정리": [
    {"이름": "홍길동",
     "소속": "○○증권",
     "직함": "리서치센터장",
     "주제": "ETF 수급|자산배분|커버드콜|해외 ETF|채권 ETF 등 짧게",
     "발언": "발언 요지 2~3문장. 기사 원문의 뜻을 바꾸지 말 것.",
     "출처": {"이름": "매체명", "id": "그 발언이 실린 기사의 id. 예: n12"}}
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
    "1": "아래 형식 그대로. 195자 이내."
  }
}

분량 지침:
- etf_뉴스6선: **정확히 6건.** 주제가 서로 겹치지 않게 분산할 것.
- 발언정리: 3~6명. 실명 인용이 없으면 빈 배열.
- 출연자추천: 1~3명. 근거가 약하면 1명 또는 빈 배열.
- 발언 요지는 각 120자 이내, 추천 이유는 70자 이내, 질문은 60자 이내.

★ 카톡 문구는 줄글로 쓰지 마세요. 아래처럼 번호와 줄바꿈으로 씁니다.
  한 줄이 길면 휴대폰에서 읽히지 않습니다. 각 줄 20자 안쪽으로 끊으세요.

📋 8/20(목) ETF 처방전 전달문

1. 커버드콜 수급
2. TDF 순자산 사상 최대
3. 반도체 ETF 수익률 격차
4. 레버리지 규제 논의
5. 미 장기채 베팅
6. 글로벌 AI ETF

전문 보기에서 복사용 텍스트를 열 수 있습니다.

  - 첫 줄은 📋 + 날짜 + "ETF 처방전 전달문".
  - 번호 줄은 6선의 '한줄' 키워드를 그대로 짧게. 한 줄에 하나씩.
  - 마지막 줄 한 문장. 195자를 넘기지 마세요.
"""


MAX_PAYLOAD_CHARS = 16_000  # 무료 티어 분당 입력 토큰 한도 안에 들어가도록


def _slim_quote(r: dict) -> dict:
    """지표 한 줄에서 AI가 실제로 쓰는 값만 남긴다."""
    out = {"이름": r.get("이름"), "종가": r.get("종가"), "등락률": r.get("등락률")}
    for k in ("변화bp", "기준일", "상태", "고가등락률"):
        if r.get(k) is not None:
            out[k] = r[k]
    return {k: v for k, v in out.items() if v is not None}


# 발언이 실린 문장을 알아보는 표지. 기사 본문을 줄일 때 이 문장들을 먼저 남긴다.
_QUOTE_MARKS = ("said", "밝혔다", "말했다", "설명했다", "전망했다", "분석했다",
                "진단했다", "강조했다", "지적했다", "예상했다", "평가했다",
                "조언했다", "내다봤다", "덧붙였다")


def _condense_body(text: str, limit: int) -> str:
    """본문을 limit 자로 줄이되, 앞에서부터 자르지 않고 발언 문장을 우선 남긴다.

    전달문의 핵심은 '누가 무슨 말을 했나'인데, 발언은 보통 기사 중후반에 나온다.
    그래서 그냥 앞부분만 자르면 정작 필요한 대목이 통째로 날아간다.
    """
    import re

    text = str(text)
    if len(text) <= limit:
        return text

    sents = [s.strip() for s in re.split(r"(?<=[.!?다])\s+", text) if s.strip()]
    if not sents:
        return text[:limit]

    lead_budget = min(limit // 3, 300)
    lead, used = [], 0
    for s in sents:
        if used + len(s) > lead_budget:
            break
        lead.append(s)
        used += len(s) + 1

    picked = list(lead)
    for s in sents[len(lead):]:
        if used + len(s) > limit:
            continue
        if any(m in s for m in _QUOTE_MARKS) or '"' in s or "“" in s:
            picked.append(s)
            used += len(s) + 1

    # 발언 문장이 없으면 그냥 앞에서부터 채운다
    if len(picked) == len(lead):
        return text[:limit]
    return " ".join(picked)[:limit]


def _link_index(data: dict) -> dict[str, dict]:
    """수집한 기사에 n1, n2 … 짧은 번호를 붙이고 '번호 → 기사' 표를 돌려준다.

    구글뉴스 링크는 한 건에 250자가 넘는다. 그대로 넘기면 입력 예산을 링크가
    다 잡아먹고, 게다가 모델이 그 긴 주소를 한 글자라도 틀리게 옮기면
    '수집 목록에 없는 링크'로 걸러져 항목이 통째로 사라진다.
    그래서 모델에게는 번호만 주고, 제목·매체·링크는 우리가 원본에서 되찾아 붙인다.
    같은 data 로 여러 번 불러도 같은 번호가 나온다.
    """
    idx: dict[str, dict] = {}
    n = 0
    news = data.get("뉴스") or {}
    for group in sorted(news.keys()):
        for it in (news.get(group) or []):
            if not it.get("링크"):
                continue
            n += 1
            it["_id"] = f"n{n}"
            it["_그룹"] = group
            idx[it["_id"]] = it
    return idx


def _mmdd(iso: str) -> str:
    """2026-08-19 → 8/19. 값이 이상하면 빈 문자열."""
    iso = str(iso or "")
    if len(iso) >= 10 and iso[4] == "-":
        return f"{int(iso[5:7])}/{int(iso[8:10])}"
    return ""


# KRX·야후처럼 기사가 아닌 고정 출처는 모델이 URL 을 직접 써도 통과시킨다.
SAFE_URL_HOSTS = ("data.krx.co.kr", "krx.co.kr", "finance.yahoo.com")


def _resolve_srcs(srcs: Any, idx: dict[str, dict]) -> list[dict]:
    """모델이 준 출처 목록을 번호로 해석해 실제 링크를 붙인다."""
    out: list[dict] = []
    for s in srcs or []:
        if not isinstance(s, dict):
            continue
        art = idx.get(str(s.get("id") or "").strip())
        if art:
            # 발행일을 함께 넘긴다. 읽는 사람이 클릭하지 않고도
            # 이 근거가 언제 기사인지 알 수 있어야 한다.
            out.append({"이름": art.get("출처", ""),
                        "url": art.get("링크", ""),
                        "날짜": _mmdd(art.get("날짜", ""))})
            continue
        url = str(s.get("url") or "")
        if url and any(h in url for h in SAFE_URL_HOSTS):
            out.append({"이름": s.get("이름", ""), "url": url})
        elif s.get("이름"):
            # 번호도 없고 아는 주소도 아니면 링크 없이 매체명만 남긴다
            out.append({"이름": s["이름"], "url": ""})
    return out


def _slim_news(items: list[dict], n: int, summary_len: int = 110,
               body_len: int = 1200) -> list[dict]:
    out = []
    for it in (items or [])[:n]:
        row = {
            "id": it.get("_id"),
            "제목": it.get("제목"),
            "출처": it.get("출처"),
            "날짜": it.get("날짜"),
            "경과시간": it.get("경과시간"),
        }
        if it.get("본문"):
            row["본문"] = _condense_body(it["본문"], body_len)
        elif it.get("요약"):
            row["요약"] = str(it["요약"])[:summary_len]
        out.append({k: v for k, v in row.items() if v})
    return out


# 목요일 전달문에서 AI에 넘길 뉴스 그룹별 기본 건수.
# config.yaml 의 목요일_전달문.수집배분 으로 덮어쓸 수 있다.
HANDOFF_MIX = {"ETF시장": 6, "증권": 5, "국내": 4, "ETF": 4,
               "레버리지": 2, "지수": 2, "보도자료": 2}


def _balanced_news(news: dict, mix: dict, budget: int) -> dict[str, list[dict]]:
    """그룹별 기사 수를 배분하고, 예산을 넘으면 본문 길이를 줄여 맞춘다.

    무료 티어는 분당 입력 토큰이 빠듯해서, 그룹을 늘리면 뒤쪽 그룹이 통째로
    잘려나가기 쉽다. 그래서 ① 기사가 없는 그룹의 몫을 남은 그룹에 넘기고
    ② 그래도 크면 본문 길이를 단계적으로 줄인다.
    """
    groups = [(g, int(n)) for g, n in mix.items() if int(n) > 0]
    avail = {g: (news.get(g) or []) for g, _ in groups}

    # ① 비어 있는 그룹의 몫을 기사가 남아 있는 그룹에 넘긴다
    quota = {g: min(n, len(avail[g])) for g, n in groups}
    spare = sum(n for _, n in groups) - sum(quota.values())
    for g, _ in groups:
        if spare <= 0:
            break
        extra = min(spare, len(avail[g]) - quota[g])
        if extra > 0:
            quota[g] += extra
            spare -= extra

    empty = [g for g, _ in groups if not avail[g]]
    if empty:
        log.info("전달문: 기사 없는 그룹 %s — 몫을 다른 그룹에 넘겼습니다", ", ".join(empty))

    # ② 본문 길이를 줄여가며 예산 안에 맞춘다
    out: dict[str, list[dict]] = {}
    size = 0
    for body_len in (1200, 900, 700, 500, 350):
        out = {g: _slim_news(avail[g], quota[g], body_len=body_len)
               for g, _ in groups if quota[g]}
        size = len(json.dumps(out, ensure_ascii=False, default=str))
        if size <= budget:
            log.info("전달문 뉴스 %d건 (%s) · 본문 %d자 · 총 %d자",
                     sum(quota.values()),
                     " ".join(f"{g}{quota[g]}" for g, _ in groups if quota[g]),
                     body_len, size)
            return out

    log.warning("전달문 뉴스가 예산(%d자)을 넘어 본문을 350자로 줄였습니다 (%d자)",
                budget, size)
    return out


def _compact(data: dict[str, Any], mode: str) -> dict[str, Any]:
    """AI에 넘길 데이터를 꼭 필요한 것만 남겨 압축한다.

    무료 티어는 분당 입력 토큰이 1만으로 제한되어 있어, 원본을 그대로 보내면
    429(RESOURCE_EXHAUSTED)가 난다.
    """
    _link_index(data)  # 기사마다 n1, n2 … 번호를 매긴다

    d: dict[str, Any] = {
        k: data.get(k)
        for k in ("날짜표시", "기준설명", "국내기준일_표시", "해외기준일_표시", "수집범위")
        if data.get(k)
    }

    if mode == "thursday":
        news = data.get("뉴스") or {}
        mix = data.get("수집배분") or HANDOFF_MIX
        # 날짜·수집범위 등 머리말 몫으로 2,000자를 남겨둔다
        d["뉴스"] = _balanced_news(news, mix, budget=MAX_PAYLOAD_CHARS - 2_000)
        return d

    d["국내지수"] = [_slim_quote(r) for r in (data.get("국내지수") or [])]
    d["지표"] = {
        g: [_slim_quote(r) for r in rows if r.get("종가") is not None]
        for g, rows in (data.get("지표") or {}).items()
    }
    d["수급"] = data.get("수급") or []

    d["종목_후보_국내"] = [
        {"종목명": s.get("종목명"), "등락률": s.get("등락률"), "사유": s.get("이유_표시")}
        for s in (data.get("종목_후보_국내") or [])[:10]
    ]
    d["종목_후보_미국"] = [
        {"이름": s.get("이름"), "티커": s.get("티커"),
         "업종": s.get("업종"), "등락률": s.get("등락률")}
        for s in (data.get("종목_후보_미국") or [])[:8]
    ]

    etf = data.get("ETF_후보") or {}
    d["ETF_후보"] = {
        k: (v[:5] if isinstance(v, list) else v)
        for k, v in etf.items() if v
    }

    news = data.get("뉴스") or {}
    # ETF시장(시장 구조·제도·자금 이동)을 ETF(상품 소식)보다 많이 넣는다.
    # 레이더가 운용사 홍보 기사로 채워지던 문제의 직접적인 원인이 입력 편중이었다.
    d["뉴스"] = {
        "ETF시장": _slim_news(news.get("ETF시장"), 9),
        "ETF": _slim_news(news.get("ETF"), 6),
        "레버리지": _slim_news(news.get("레버리지"), 3),
        "지수": _slim_news(news.get("지수"), 3),
        "일정": _slim_news(news.get("일정"), 5),
        "보도자료": _slim_news(news.get("보도자료"), 3),
        "국내": _slim_news(news.get("국내"), 8),
        "국제": _slim_news(news.get("국제"), 10),
    }
    d["뉴스"] = {k: v for k, v in d["뉴스"].items() if v}

    yt = data.get("유튜브") or {}
    if yt.get("급상승"):
        d["유튜브"] = {
            "급상승": [
                {"영상ID": v.get("영상ID"), "제목": v.get("제목"),
                 "채널": v.get("채널"), "조회수": v.get("조회수"),
                 "ETF관련": v.get("ETF관련"), "ETF점수": v.get("ETF점수")}
                for v in yt["급상승"][:6]
            ],
            "댓글샘플": [str(c)[:100] for c in (yt.get("댓글샘플") or [])[:12]],
        }
    return d


def _payload(data: dict[str, Any], mode: str = "daily") -> str:
    compact = _compact(data, mode)
    text = json.dumps(compact, ensure_ascii=False, default=str)
    # JSON 중간을 자르면 깨진 입력이 된다. 예산을 넘으면 우선순위가 낮은
    # 뉴스부터 기사 한 건 단위로 제거해 항상 유효한 JSON을 유지한다.
    trim_order = ("보도자료", "ETF", "지수", "레버리지", "국제", "국내", "ETF시장")
    removed = 0
    while len(text) > MAX_PAYLOAD_CHARS:
        news = compact.get("뉴스") or {}
        target = next((g for g in trim_order if news.get(g)), None)
        if not target:
            log.error("구조를 보존한 채 AI 입력 예산을 맞출 수 없습니다 (%d자)", len(text))
            break
        news[target].pop()
        if not news[target]:
            news.pop(target, None)
        removed += 1
        text = json.dumps(compact, ensure_ascii=False, default=str)
    if removed:
        log.warning("AI 입력 예산을 위해 기사 %d건을 항목 단위로 제외했습니다", removed)
    log.info("AI 입력 크기: %d자 (약 %d토큰)", len(text), len(text) // 2)
    return text


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
{_payload(data, mode)}
</데이터>
"""
    candidates = [ai.get("모델") or "gemini-3.6-flash"]
    if ai.get("대체모델"):
        candidates.append(ai["대체모델"])
    # 구글이 모델 이름을 바꿔 404가 나는 경우를 대비해, 실제 사용 가능한 모델을 뒤에 붙인다.
    if provider == "gemini":
        candidates += [m for m in _discover_gemini_models() if m not in candidates]
    log.info("AI 모델 후보: %s", candidates)

    for attempt, model in enumerate(candidates):
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
            head = (raw or "")[:300].replace("\n", " ")
            log.warning("모델 %s: JSON 파싱 실패. 응답 앞부분: %s", model, head or "(빈 응답)")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            short = msg[:200].replace("\n", " ")
            log.warning("모델 %s 실패(시도 %d): %s", model, attempt + 1, short)
            # 분당 할당량 초과면 잠깐 쉬었다가 같은 모델로 한 번 더
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                import time
                log.info("할당량 초과 — 20초 대기 후 재시도합니다")
                time.sleep(20)
                try:
                    raw = _call_gemini(model, user, ai, system) if provider == "gemini" else ""
                    parsed = _parse_json(raw)
                    if parsed:
                        return _postprocess(parsed, cfg, data, mode)
                except Exception as e2:  # noqa: BLE001
                    log.warning("재시도도 실패: %s", str(e2)[:200])
    log.error("AI 생성 전부 실패 — 데이터만으로 브리핑을 만듭니다.")
    return {}


# ─────────────────────────────────────────────
# 제공자별 호출
# ─────────────────────────────────────────────
def _discover_gemini_models(limit: int = 3) -> list[str]:
    """지금 이 API 키로 실제 쓸 수 있는 Gemini 모델 목록을 조회한다.

    구글이 모델 이름을 바꿔도(예: 2.5-flash → 3.6-flash) 자동으로 따라가기 위함이다.
    조회 실패해도 예외를 던지지 않고 빈 목록을 돌려준다.
    """
    import requests

    key = env("GEMINI_API_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key, "pageSize": 200}, timeout=20,
        )
        r.raise_for_status()
        names = []
        for m in r.json().get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            name = str(m.get("name", "")).replace("models/", "")
            if not name.startswith("gemini-"):
                continue
            # 텍스트 생성이 아닌 모델(음성·이미지·임베딩)과 실험판을 제외한다.
            bad = ("tts", "audio", "image", "vision", "embedding",
                   "live", "native", "preview", "exp", "thinking", "learnlm")
            if any(b in name for b in bad):
                continue
            names.append(name)
        # 빠르고 저렴한 flash 계열을 우선한다.
        flash = [n for n in names if "flash" in n and "lite" not in n]
        lite = [n for n in names if "lite" in n]
        rest = [n for n in names if n not in flash and n not in lite]
        ordered = flash + lite + rest
        if ordered:
            log.info("사용 가능한 Gemini 모델 %d개 확인", len(ordered))
        return ordered[:limit]
    except Exception as e:  # noqa: BLE001
        log.warning("모델 목록 조회 실패: %s", e)
        return []


def _gemini_text(res) -> str:
    """응답에서 실제 텍스트만 뽑는다.

    Gemini 3.x 는 '생각(thought)' 파트를 함께 돌려줄 수 있어 res.text 가
    비거나 JSON 앞에 잡음이 섞이는 경우가 있다. 파트를 직접 훑어 안전하게 모은다.
    """
    # 1) 파트를 직접 순회 (thought 파트는 건너뛴다)
    try:
        chunks = []
        for cand in (getattr(res, "candidates", None) or []):
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "thought", False):
                    continue
                t = getattr(part, "text", None)
                if t:
                    chunks.append(t)
        if chunks:
            return "".join(chunks)
    except Exception as e:  # noqa: BLE001
        log.debug("파트 추출 실패: %s", e)

    # 2) 그래도 없으면 SDK 기본 속성
    try:
        return res.text or ""
    except Exception:  # noqa: BLE001
        return ""


def _finish_reason(res) -> str:
    try:
        cands = getattr(res, "candidates", None) or []
        if cands:
            return str(getattr(cands[0], "finish_reason", "") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _call_gemini(model: str, user: str, ai: dict, system: str = SYSTEM) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=env("GEMINI_API_KEY", required=True))
    base = dict(
        system_instruction=system,
        temperature=float(ai.get("온도", 0.4)),
        max_output_tokens=int(ai.get("최대_출력토큰", 32768)),
        response_mime_type="application/json",
    )

    # 최신 모델은 '생각'에 출력 토큰을 크게 쓴다. 끌 수 있으면 꺼서 본문에 몰아준다.
    res = None
    try:
        res = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(
                **base,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        # 모델마다 thinking 옵션 형식이 달라 400(INVALID_ARGUMENT)이 날 수 있다.
        # 그 경우 옵션 없이 한 번 더 시도한다.
        retriable = ("thinking" in msg or "invalid_argument" in msg
                     or "invalid argument" in msg or "400" in msg)
        if not retriable:
            raise
        log.info("thinking 옵션 미지원 — 기본 설정으로 재호출합니다")
    if res is None:
        res = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(**base),
        )

    text = _gemini_text(res)
    reason = _finish_reason(res)
    log.info("모델 %s 응답: %d자, finish_reason=%s", model, len(text), reason or "?")
    if not text:
        log.warning("응답 본문이 비었습니다 (finish_reason=%s)", reason)
    elif "MAX_TOKENS" in reason.upper():
        log.warning("출력 토큰 한도에 걸려 응답이 잘렸습니다. "
                    "config.yaml 의 AI > 최대_출력토큰 을 늘려주세요.")
    return text


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
def _as_dict(obj: Any) -> dict | None:
    """모델이 객체 대신 배열로 감싸서 주는 경우를 흡수한다.

    Gemini 가 response_mime_type=application/json 으로 [{...}] 를 돌려주면
    그대로 dict 로 다루다 'list' object has no attribute 'get' 로 터진다.
    """
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                log.info("모델이 배열로 응답 — 첫 객체를 사용합니다")
                return item
    return None


def _parse_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return _as_dict(json.loads(raw))
    except Exception:  # noqa: BLE001
        pass
    # 앞뒤에 설명이 붙은 경우 JSON 덩어리만 추출
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, raw, re.S)
        if m:
            try:
                d = _as_dict(json.loads(m.group(0)))
                if d:
                    return d
            except Exception:  # noqa: BLE001
                continue
    return None


BANNED = [
    "추천합니다", "매수하세요", "매도하세요", "사야 합니다", "팔아야 합니다",
    "지금이 기회", "반드시 오를", "확실합니다", "보장", "유리합니다",
]
UNSUPPORTED_HYPE = ("폭발", "매수세 집중", "수급 집중", "돈이 몰렸", "자금이 몰렸")
FLOW_WORDS = ("몰렸다", "몰린", "유입", "순매수")
TURNOVER_WORDS = ("거래대금", "거래액", "거래 규모")
STOCK_FLOW_WORDS = ("외국인", "기관", "순매수", "수급", "매수세")
FILLER = ["해당 없음", "특이사항 없음", "없음", "생략", "특이 종목 없음", "기준 미달"]

# 국내 ETF 브랜드. 제목에 이게 들어가면 '단일 상품 기사'로 본다.
ETF_BRANDS = ("KODEX", "TIGER", "ACE", "RISE", "PLUS", "SOL", "HANARO",
              "KOSEF", "ARIRANG", "TIMEFOLIO", "KIWOOM", "WON", "BNK",
              "코덱스", "타이거")
# 운용사 보도자료 특유의 홍보 표현
PR_WORDS = ("업계 최초", "차별화", "주목받고", "인기를 끌", "돌풍", "호평",
            "각광", "선보였", "출시했다")


# 운용사 이름이 제목에 있으면 대개 그 회사가 낸 보도자료다.
# "자산운용"만으로 삼성자산운용·KB자산운용·NH아문디자산운용이 모두 걸린다.
ASSET_MGRS ߮��G����ƭy�;"�;'�H;(%z��:� ;%a:��:���������]\�YH�K��܈�[��Y\���Y��\����X��][J�N��Y�\�Y�H�Y\���˝�\��[�����;'o; �{d�;fcz��;!,H;ekz�H;(';&n�	\ȋ�����]
�(':�H�JV΍JB��۝[�YB�\�Y
�HB��]�\[�
�B��]\���]���Y���\����][N�X�
HO��ۙN�����fcz��;dg;f!;'m:�;%a;'�;'/:�m:�g:��:�g:��;%c:�:�����:��;%�:�o;)�;&�:�m�%�z��;-g;-":�g;,*:��;fe:�';!':�a;"�:�o;!(:��;& ;"�z��:���� ���g:�';!':�a;"�:�o;"�z��:���� :�&;%�:�.;'�{'m:��z� ;)�:����; �{!,H:��:��
:��;.fHKLJ{%�;!':��z�:���'m:�纬�;%�:�,;!':�:�$;"�:��;eg:��������^H���][K��]
	�(':�I�	��_H�][K��]
	� �;"�	�	��_H�][K��]
	�� ;,,	�	��_H��]H���܈�[����ԑ�Y��[�^B�Y�]���˝�\��[���fcz��;!,H;dg;f!;'m:�;%a;'�;"�z��:��
	\�N�	\ȋ������[�]
K��][K��]
�(':�H�JV΍JB���Y��\��\J][N�X�
HO������^H�����[���][K��]
���JB��܈�[�
�(':�H�� �;"���em;!'H��� ;,,��'m;'(�JB��]\��[�J�[�^�܈�[����ԑ�
�S��TԕQ�TJB���Y��Z^\��\��ݙ\��[�ٛ��][N�X�
HO����������l:�:� :�";'a;"';'(;'�{,�:��:�%:��;$�;ekz�{'n;)�:��; �;eg:�������]�Y[��HH�����[���][K��]
���JH�܈�[�
� �;"���'m;'(��� ;,,�JB��Z[HH�����[���][K��]
���JH�܈�[�
�(':�H��'m;'(��� :�*U��JB��]\��[�J�[�]�Y[��H�܈�[�T��ՑT���ԑ�H[�[�J�[��Z[H�܈�[������ԑ�B���Y��\���[��W������]�\�Y�Wۙ]��]N�X�
HO�������܈ܛ�\[�
]K��]
��m;"��H܈�JK��[Y\�
N���܈\�[�ܛ�\܈�N��]HH��\���]
�(':�H���JB�Y�
��":�:�;)��[�]B�[�[�J�[�]H�܈�[�
���;'o;(�z�H�� �;(!:��{"��� �;!,{(!;'�����ef;'m:��{"��JJN���]\���YB��]\���[�B���Y�ٚ^�]���\��Y�X�][ۊ][N�X��[��W�������۝^����
HO�X��������;'o;(�z�H; �{d�;'a;%�{(�{f%H:�&:��;,�U��g:��:�m:�;&):�f:�o:�d;(%{eg:�������Y����[��W�������۝^���]\��][B��܈�^H[�
�(':�H�� �;"���� ;,,��'m;'(��� :�*U���)�:�.��,*:��;($�N��^H��][K��]
�^K��JB�^H^��\X�J��&:��;,�:�":�:�;)�U��� �;!,{(!;'�0����ef;'m:��{"�:��;'o;(�z�H:�":�:�;)�U��B�^H^��\X�J��&:��;,�:�":�:�;)��� �;!,{(!;'�0����ef;'m:��{"�:��;'o;(�z�H:�":�:�;)��B�Y��{(l�[�^��^H^��\X�J���V:�":�:�;)��� �;!,{(!;'�0����ef;'m:��{"�:��;'o;(�z�H:�":�:�;)�U��B�][V��^WHH^��]\��][B���Y����\�����ٛ����Z[J^�[�K]N�X�
HO��������(�z�z��;"&:�"H:�l;'m;a,;%�'m;"�;'�H;(!;,�;"&:�"{'a:�':��;(�;%�:��{'n:�.;'�{'a;(':�l;eg:��������[YHH��^܈��K���\

B��[Y\�H���˙�]
�(�z�z�H�H܈��K���\

B��܈�[�
]K��]
�(�z�W�f�:����kz��H܈�J_B��[Y\˝\]J
� �;!,{(!;'�����ef;'m:��{"���ef;'m:��{"��JB�\��H����\

H�܈[��K��]
���VˈO�JW�ȋ�[YJHY����\

WB��\H��܈[�\�Y���
[�J�[��[��܈�[��[Y\�B�[�[�J�[��܈�[�����ѓ����ԑ�JWB��]\�������[��\
B���Y��\���\��[��[��Y^J][N�X�]N�X�
HO���������&);(!�"�;&�H:�#:�;ed{%�;"�;e�H:��{'o;'�{)$H;!�z��:� ;!'�& :�;)�;fe{'n;eg:�������^H�����[���][K��]
���JH�܈�[�
�(':�H��"*�'���& {e�H�JB�Y���[�J�[�^�܈�[�
�'�{)$H�� �;'m:��;.m�JN���]\���[�B�HH�K��X\��
���Jz�aʊ�K�J{&�ʊ�K�J{'o���]K��]
��;)�;dg;"����JJB�Y���N���]\���[�B�[YH���[�
K�ܛ�\
�J_K��[�
K�ܛ�\
�J_H���]\��[Y[�^���Y����ٚ[\�][\Έ\��^\Έ\V������JHO�\�������em:��H;%�'c	�:�f:�g;,a;&�;)�;ekz�{'a;(':�l������]H�B��܈][�][\�܈�N��Y���\�[��[��J]X�
N���۝[�YB�^H�����[���]��]
���JH�܈�[��^\�B�Y�[�J�[�^�܈�[��ST�H[�[�^
H���۝[�YB��]�\[�
]
B��]\���]����;em;!'H;%�:��:� ;'m:��;'�:�:�l:�;%�:��z�; �{b+;( H;eg:��:�.;'�H8�%;)$z��{'m:��:�g;(':�l;eg:����Q�W�S�S��H�K���\[J���:� :�{!,{'m;'�;%�;)�:� ;'�:��;"&;'�;"&;'�;"�z��:��;'o;"&;'�;& ;'a;"&;'�;'o{g�;"&;'�
H��B���ST�UW��VT�H�K���\[J���;fe{(%{eh;"&;%�;fe{(%{eh;"&:�;%�;fe{(%{ef:�,:�;%�:�-_:��;(%{eh;"&;%�:��;(%{ef:�,:�;%�:�-_����c$:��;eh;"&;%�;%c;"&;%�"�z��:��:�k:��:�&;)�;%b�"�z��:��
H��B��:�k;,�;( H:�&:�%H:��:�l:� :��:�-:�.;'�{'`:�:�-:���;%a:�:�.;'m:�o:�&;'/:�m	��k;,�;( I�'/:�g:��:�����P�Q�P��S�H����Y���[W�Y�J^���HO��������%g�:�.;'�{'m;'m:��:��;fe{"�;%�:��:�o;#o:��:�m:�;%�:��{'`; �{b+;( H:�m;,aH:�.;'�{'a;)�;&�:�����:�k;,�;( {'n:�&:� :��:�l:� :��:�-:�.;'�J:�.;'m�P�Q�P��S�;-":��
{'`;(%z��;'m:��:�g:�:�-:��������H
^܈��K���\

B�Y������]\����܈�[��[��J�N��\��H����\

H�܈[��K��]
���W�W�ȋ
HY����\

WB�Y�[�\��H�����XZ\�H\���LWB�XYH�����[�\��΋LWJK���\

B�Y�
���ST�UW��VT˜�X\��
\�
B�[�[�\�
HH�P�Q�P��S��[�Q�W�S�S�˜�X\��
XY
B�
N��HXY�[�N����XZ�]\�����Y�ݘ[Y�\��]N�X�
HO��]���N��\��H�]

B��܈ܛ�\[�
]K��]
��m;"��H܈�JK��[Y\�
N���܈][�ܛ�\܈�N��Y�]��]
���{`k�N��\�˘Y
]Ⱥ��{`k�JB��]\��\���Y��\�X�W�Y�J]N�X�
HO�X����[�N�����\�8���:��:��;f�:��z��;"�:�!
;"�:�!
K�;&):�:�':��:�l:�o:�n:��:�:�:�l;$�:�������Y�\ΈX����[�HH�B��܈ܛ�\[�
]K��]
��m;"��H܈�JK��[Y\�
N���܈][�ܛ�\܈�N��Y�]��]
���{`k�H[�]��]
���z��;"�:�!�H\����ۙN��Y�\��]Ⱥ��{`k�WHH[�
]Ⱥ��z��;"�:�!�JB��]\��Y�\���SW��T��H���Y����\��[W���\��\�][\Έ\��X�KY�\ΈX����[�JHO�\��X�N�������:�l:�,; �:� ;"�:�!;'a:�&:��;'/:�m:��;ekz�{'a;a�{)�:�g:�:�����	�%�;(';'m:��;'o;'m;'�;%�:��	���;#o:�:�l;"�;(':�g:�; �;gf;(!:�,; �;'n:��{&�:�o:��z�:����:�l;'m;a,;-�;,�
Ԗ:��H:��{`k;%��;ekz�Jz��:�:�:��{&�:�:��:� :�g;a�z��;"�;`�:���������]H�B��܈][�][\�܈�N��ܘ��H]��]
�-�;,��H܈�B��]���ܘ��H���܈�[�ܘ��Y�\�[��[��J�X�
H[�˙�]
�\��H[�Y�\�B�Y��]���ܘ��[�[
Y�\���ȝ\��WH��SW��T���܈�[��]���ܘ��N���\�HX^
Y�\���ȝ\��WH�܈�[��]���ܘ��B��˝�\��[�����:�l:�,; �:� 	Y;"�:�!;(!;'m:�o;ekz�H;(';&n�	\ȋ��\���]��]
�(':�H���JV΍JB��۝[�YB��;ekz�{'`:�:�,:�&;&):�:�':��{`k:��:�;%�:�:���Y��]���ܘ�΂�]Ȼ-�;,��HH���܈�[�ܘ�Y���
\�[��[��J�X�
H[�˙�]
�\��H[�Y�\[�Y�\���ȝ\��WH��SW��T��WB��]�\[�
]
B��]\���]���Y�ۛܛX[^�W�X\��]���\��\�][\Έ\��X�KY�\ΈX����[�JHO�\��X�N�������:�kp���kz�;-�;,�:�o:�%:�g;'�z��:�,; �:�g;fe{'n:�&;)�;%b�'`;&�;'n;'`;(l;&�{g�;"*:�-:��������]H�B��܈][�][\΂�X\��]ۘ[YHH]��]
�"�;'�H�B�ܘ��H���܈�[�
]��]
�-�;,��H܈�JHY�\�[��[��J�X�
WB�Y�X\��]ۘ[YHOH���:�kH���ܘ��H���܈�[�ܘ��Y��ܞ��˚܈���[���˙�]
�\����JWB��^YHȻ'm:����XZ���[�[��H��\����΋�ٚ[�[��K�XZ�˘��H�B�[�N��ܘ��H���܈�[�ܘ��Y���[�[��K�XZ�˘��H���[���˙�]
�\����JWB��^YHȻ'm:����Ԗ;(%z��:�l;'m;a,;"�;"�;ag��\����΋��]K�ܞ��˚܈�B�Y���[�J˙�]
�\��HOH�^Yȝ\��H�܈�[�ܘ��N��ܘ�˚[��\�
�^Y
B��;"*�'�;-�;,�:��;'/:�g	�&g;&�;)�{& :�	��o;$�;)�;%b��:����Y���[�J˙�]
�\��H[�Y�\��܈�[�ܘ��N���˝�\��[���&�;'n:�,; �:��:�l:� ;%�%�;'n:��;!):�H; �z�N�	\ȋ]��]
�(':�H���JB�]Ȼ&�;'n�HH���]Ȼ-�;,��HHܘ��]�\[�
]
B��]\���]���Y��][�W��[[X\�J���Έ\��X�K�[Y\Έ\V������JHO�����\��H�B��܈���[�����܈�N��Y�����˙�]
�'m:��JH��[��[Y\�܈��˙�]
�(�z� �H\��ۙN���۝[�YB��H��˙�]
���z�oz�h�B���^H��ٛ�]
�
N�ˌ��IH�Y��\����ۙH[�H���\�˘\[�
��ܛ�˙�]
	�'m:�	�_H���^H����\

JB��]\���0������[�\��B���Y�٘[�X���X\��]؜�YY�X\��]ۘ[YN���]N�X�
HO�X�������:�n;'m;eg:�kp����:�kH;)$H;ef:�:�o:�o:��;%�{*�H;"�;'�{'a:�&:��;"�;dg;"�;eg:�������Y�X\��]ۘ[YHOH��kz�����\�[H�][�W��[[X\�J]K��]
��kz�;)�;"&�H܈�K
�/e;"�;e/��/e;"�:��H�JB��]\��Ȼ"�;'�H����kz���(':�H����kz�;)�{"�:��:�$;gd:������:�����\�[܈��kz�;)�;"&:�l;'m;a,:�o;fe{'n;em;%o;ejz��:������&�;'n������U�%�:�����/e;"�;e/�0��/e;"�:��LMLU�'f:��z�oz��:� ;f%{(�:�,;%�:��:�o;ej:��;fe{'n;eh;ea;&�:� ;'�;"�z��:������-�;,����Ȼ'm:����Ԗ;(%z��:�l;'m;a,;"�;"�;ag��\����΋��]K�ܞ��˚܈�W_B�����H�B��܈ܛ�\[�
]K��]
�)�;dg�H܈�JK��[Y\�
N�����˙^[�
ܛ�\܈�JB��\�[H�][�W��[[X\�J����
�ɔL���;"�:��H;(�{ejH����;&���JB��]\��Ȼ"�;'�H�����:�kH��(':�H�����:�kH;)�{"�:��:�$;gd:�����:�����\�[܈���:�kH;)�;"&:�l;'m;a,:�o;fe{'n;em;%o;ejz��:������&�;'n������U�%�:�����ɔL0���;"�:��LLU�&`:�":�:��:�$;!,{'�{(�;gd:�;'a;ej:��;fe{'n;eh;ea;&�:� ;'�;"�z��:������-�;,����Ȼ'm:����XZ���[�[��H��\����΋�ٚ[�[��K�XZ�˘��H�W_B���Y���\
^�[�K��[�
HO������[YHH��^܈��K���\

B��]\���[YHY�[��[YJHH�[�H�[YVΛX^
�HKJWK����\

H
���)�����Y�؝Z[�Z[W��Z�[��X�]N�X�[Z]�[�
HO�X����W�X\��]H؋��]
�"�;'�H�N���܈�[���]
�"�;'�z�#:�;edH�H܈�_B�܋\�H�W�X\��]��]
��kz���JK�W�X\��]��]
���:�kH��JB�]HH��]K��]
��;)�;dg;"��H܈��B�HH�K��X\��
���K�J{&�ʊ�K�J{'o�]JB��[\H���[�
K�ܛ�\
JJ_K��[�
K�ܛ�\
�J_H�Y�H[�H�&):����Y\�H
��]
�]���";'m:�e�H܈��WJV�B�[�\�Hو�� ;�#���[\H;%a;.j:�#:�;edH����K�:�kz����\
܋��]
	���:��	�K�_H������:��:�kH���\
\˙�]
	���:��	�K�_H�B�]�H���Y\���]
�(':�H�H܈�Y\���]
� �;"��H܈�U�:�";'m:�e:�o;fe{'n;ef;!.;&��B�[�\˘\[�
��ˈU����\
]�
_H�B��]\��ȌH����\
������[�[�\�K[Z]
_B���Y�������\���[�KٙΈX�]N�X��ۙHH�ۙK[�N���H�Z[H�HO�X���H�\��X�

H܈�B�]HH]H܈�B�[Z]H[�

ٙ˙�]
�.m;.m;&)�H܈�JK��]
�� ;'�;"&�(';eg�NMJJB��Y�[�HOH�\��^H����]\��������\���[�ٙ�[Z]]JB���;.m;a�N�:�";)�;dg;f!;(':�l
�:�.;'m:�%{('��Z�[�H��]
�.m;a�H�H܈�B��܈�[�\�
�Z�[˚�^\�
JN��^H���Z�[���H܈��K���\

B��܈�Y[��S��Q��^H^��\X�J�Y��B�Y�[�^
H�[Z]���]H^Λ[Z]B��H�]���[�
���B�^H
�]Λ�HY���[Z]
���[�H�]
K����\

B��Z�[���HH^�Ȼ.m;a�H�HH�Z�[����8�%:��;'�H;dg;f!;'m:�;'`;ekz�{'`;dg;"�;ef;)�;%b��:�����HH܈�܈�[�
��]
��H�H܈�JHY�\�[��[��J�X�
WB��HH܈�܈�[��HY����\��\J�H[����\���\��[��[��Y^J�]JWB��܈K�[�[�[Y\�]J�KJN�����]Y�][
�"';'!�JB�ȝ�H�HH�VΌ�B���:��:��8���;&�;'n8���U�;%�:��:�g;'o{g�:�:��:�kp���kz�;"�;'�H;em;!)���YY��H���ٚ[\���]
�"�;'�z�#:�;edH�K
�(':�H����:���JB��]؜�YY��H�B��Y[��X\��]�H�]

B��܈�[���YY�΂�X\��]ۘ[YHH�����]
�"�;'�H�H܈��K���\

B�Y�X\��]ۘ[YH��[�
���:�kH���kz��H܈X\��]ۘ[YH[��Y[��X\��]�܈�\��\J�N���۝[�YB��܈�^K[Z]ۈ[�

�(':�H��
K
���:���M
K
�&�;'n���
K
�U�%�:���M
JN�����^WHH���\�����ٛ����Z[J���]
�^JK]JVΛ[Z]ۗB�Y����Ⱥ��:���H܈���Ȼ&�;'n�N���۝[�YB��Y[��X\��]˘Y
X\��]ۘ[YJB��]؜�YY�˘\[�
�B�Ȼ"�;'�z�#:�;edH�HH�]؜�YY��Ό�B�Ȼ&):�:� ;(!�HH����\�����ٛ����Z[J]JVΎLB��܈[�
��]
�&):�:� ;(!�H܈�JB�Y����\�����ٛ����Z[J]JWVΌ�B���
�"�;'�{&�;%oH��ۙJB���
�em{"�;'m;"���ۙJB���;-�;,�:�o:�;f.;%�;!';"�;(':��{`k:�g:�&:��:�:��
:�:�n;'mT�;'a;&+���;( {)�;%b���;eg:� :� 
B�YH�[���[�^
]JB��܈�^H[�
�"�;'�z�#:�;edH��]���";'m:�e�N���܈�[���]
�^JH܈�N��Y�\�[��[��J�X�
N���Ȼ-�;,��HHܙ\���W�ܘ��˙�]
�-�;,��KY
B���U�:�";'m:�e8�%:�b;ekz�p��ea:��;(':�l;&):�:�':��:�l;(':�l:� ;,,:�m;,aH:�.;'�H;(%z��Y�\�H�\�X�W�Y�J]JB�Ȼ"�;'�z�#:�;edH�HHۛܛX[^�W�X\��]���\��\�����\��[W���\��\�Ȼ"�;'�z�#:�;edH�KY�\�KY�\�B��\�[�H؋��]
�"�;'�H�H�܈�[�Ȼ"�;'�z�#:�;edH�_B��܈X\��]ۘ[YH[�
��kz�����:�kH�N��Y�X\��]ۘ[YH��[��\�[����˝�\��[����:�n;'m	\�;"�;'�{'a:�!:�o{em:�k;(l;fe:�l;'m;a,:�g:��;-�{ejz��:���X\��]ۘ[YJB�Ȼ"�;'�z�#:�;edH�K�\[�
٘[�X���X\��]؜�YY�X\��]ۘ[YK]JJB�ܙ\�HȺ�kz������:�kH��_B�Ȼ"�;'�z�#:�;edH�K��ܝ
�^O[[X�H��ܙ\���]
���]
�"�;'�H�KJJB��Y\��X^H[�

ٙ˙�]
�U���";'m:�e�H܈�JK��]
�-g:� �ekz�{"&��JB��Y\�H���ٚ[\���]
�]���";'m:�e�K
�(':�H�� �;"��JB��Y\�H���\��[W���\��\��Y\�Y�\�B��Y\�H�[Z]���X��][\��Y\��Y\LJB��[��W�������۝^H�\���[��W������]�\�Y�Wۙ]��]JB��Y\�H�ٚ^�]���\��Y�X�][ۊ��[��W�������۝^
H�܈�[��Y\�B��Y\�H܈�܈�[��Y\�Y����\��\J�H[����Z^\��\��ݙ\��[�ٛ���WVΜ�Y\��X^B��܈�[��Y\����Ⱥ� ;,,�HH��[W�Y�J���]
�� ;,,���JB���\�����B�ș]���";'m:�e�HH�Y\���˚[����U�:�";'m:�e	Y:�'
:�k:���	\�H�[��Y\�K������[������]
��k:�����JH�܈�[��Y\�H܈�H�B���[YݚY[��Y�H������]
�& { �RQ�JB��܈�[�

]K��]
�'(;b�:�#�H܈�JK��]
��"{ �{"�H�H܈�J_B�Ȼ'(;b�:�#�HH݈�܈�[�
��]
�'(;b�:�#�H܈�JB�Y�\�[��[��J�X�
B�[������]
�& { �RQ�JH[��[YݚY[��Y�VΌ�B�[��H���ٚ[\���]
�/f;ad;.(;f�:���K
�(':�H��'m;'(�JB�[��H�ٚ^�]���\��Y�X�][ۊ�[��W�������۝^
H�܈[�[��B�Ȼ/f;ad;.(;f�:���HH��܈[�[�Y����\��\J
H[����Z^\��\��ݙ\��[�ٛ��
WVΌ�B���;,�;`k;c�;'n;b�8�%;'o;(%K�fe{'n:�d;'(;f%K�:�k:�;(!;`�
	�'o;(%I�z��:�&�%a;) :������H��]
�,�;`k;c�;'n;b��H܈��]
�'o;(%H�H܈�B���H���ٚ[\���
��;&�H�
JB��܈�[��΂�Y�˙�]
�'(;f%H�H��[�
�'o;(%H��fe{'n�N���Ȼ'(;f%H�HH�'o;(%H�Y�[�J��\�Y�]

H�܈�[���˙�]
��c���JJH[�H�fe{'n��Ȼ,�;`k;c�;'n;b��HH��΍B���
�'o;(%H��ۙJB���ۘ�\H��]
�&):�;'f:�':�d�B�Y���\�[��[��J�ۘ�\X�
H܈���ۘ�\��]
�&�{%��N��Ȼ&):�;'f:�':�d�HH�ۙB��Y�������]
��$�� ;`�;&�:���H܈��K���\

N��Ⱥ�$�� ;`�;&�:���HH�����:��{`k:�o:�!:�m;)�;%b�%a:��;%�z�kH;"�;'�z��U�;em{"�;'a;'oz�p�̰���;&�;%oK��Ȼ.m;a�H�HH؝Z[�Z[W��Z�[�]K[Z]
B���]\�����Y�ۙ]��ܛ��\��X�[YN���H��[�N���H��HO�X�������,; �;&�:��;%�;!'�!(;eg;)!;'a:��:��:���;(':�p����;,�0���;)�0����{`k:�;(!:��;&�:��:�$�������]\���(':�H��\���]
�(':�H���K����;,���\���]
�-�;,����K���;)����[Y
\���]
��;)����JK��\���\���]
���{`k���K��(�;('��[YH܈�ԓ�T�SQK��]
\���]
����:��H���K�ac:���K��eg;)!��[�H܈��\���]
�&�;%oH�H܈��V΍K�B����ԓ�T�SQHHȺ��:��;'�:�����"�:��; �{'�H���":�:�;)�����":�:�;)�0��'n:�;"����)�;"&���)�;"&:��:��H��U����"&:�"H���kz����"�;'�H:��:����U�"�;'�H���"�;'�H:�k;(l��)�z�����"�;'�H:��:��B����!(;'a;,a;&�:�c;%�:�:��:��z��;a,:��;)����T�ԑT�H
�U�"�;'�H��)�z�����kz���U����":�:�;)���)�;"&����:��;'�:���B���;(!:��:�.;'`;'m:�;(�:�,; �:��:��:��:���;'m:��:��;&):�:�':�,; �:�;,a;&�:�,;%�:��;$�;)�;%b��:������T�PV��T��HM����Y�ٜ�\�
\��X�
HO������H\���]
���z��;"�:�!�B��]\��\����ۙH[�[�

HH��T�PV��T���Y�������\���[�ٙ��X�[Z]�[�]N�X�
HO�X�������{&�;'o;(!:��:�.;f�;,�:�8�%;ff:� H:��{`k;,*:��
��!(;,a;&�:�,�����YH�[���[�^
]JB���:�m;"��!(�:�:�n;'`Y:��:��:�n:���;(':�p����{`k:�;&�:��;%�;!':��{'n:�����]���Y[�H�K�]

B��]�H��]
�]���m;"��!(�H܈�B��܈�[��]΂�Y���\�[��[��J�X�
N���۝[�YB�\�HY��]
�����]
�Y�H܈��K���\

JB�Y���\����˝�\��[����:�m:�:�,; �:�;f.:�o;(';&n�	\ȋ�����]
�Y�JVΌ�JB��۝[�YB�Y�\���]
���{`k�H[��Y[����۝[�YB��Y[��Y
\���]
���{`k�JB��]�˘\[�
ۙ]��ܛ��\������]
�(�;('�H܈��K�����]
�eg;)!�H܈��JJB�����m;'m;%b:�&:�m;"&;)�{eg:�,; �;%�;!';)�{($H;,a;&�:���
;)�;%�:�:�:��;%a:��:�o:��:�m:�:���B�Y�[��]��H���X��Y؞W�[�[H[��]��B����H�Έ\�

]K��]
��m;"��H܈�JK��]
�H܈�JH�܈�[���T�ԑT�B��;eg:��:��{%�;!':�;%a:�d{)�;%b���:�gH:��:��{'a:��;%a:� :�l;eg:�m;%*H:� ;(.;&*:����[H[��]��H�[�[�J��˝�[Y\�
JN���܈�[���T�ԑT���Y�[��]��H�H�����XZ�[H�����N��\�H�����K��

B�Y�\���]
���{`k�H[�\�Ⱥ��{`k�H��[��Y[�[�ٜ�\�
\�
N���Y[��Y
\�Ⱥ��{`k�JB��]�˘\[�
ۙ]��ܛ��\�
JB���XZ�˝�\��[����:�n;'m�!(;)$H	Y:�m:��:��:�o	Y:�m;'a;"&;)�H:�,; �;%�;!':��;-�{e�;"�z��:����X��Y؞W�[�[[��]��HHX��Y؞W�[�[
B��;&�;&�{ �;fcz��;!,H:�,; �:���m;)$H��m:�c;)�:���:�h;)�;'�:�:�;"�;'�H:�,; �:�g:��;"�;,a;&�:�����\H�[Z]���X��][\��]���Y\L�B���YH[��]��HH[��\
B��]��H�\�Y���Y���܈ܛ�\[���T�ԑT����܈\�[�
]K��]
��m;"��H܈�JK��]
ܛ�\
H܈�N��Y�[��]��H�H�����XZY�\���]
���{`k�H[��Y[�܈��ٜ�\�
\�
N���۝[�YB����Hۙ]��ܛ��\�
B�Y��\����X��][J���N���۝[�YB��Y[��Y
\�Ⱥ��{`k�JB��]�˘\[�
���B�Y�[��]��H�H�����XZș]���m;"��!(�HH�]��΍�B��˚[�����m;"��!(;fe{(%H	Y:�m
;fcz��;!,H	Y:�m:�d;,�
H�[�ș]���m;"��!(�JK��Y
B���:�';%��;'m:�0��!�;!�p���';%�;'m:�:�d;'�;%�;%o;eg:���;-�;,�:�:�;f.:�g;em;!'{eg:����][�\�H�B��܈H[���]
��';%�;(%z��H܈�N��Y���\�[��[��JKX�
N���۝[�YB�Y���
K��]
�'m:��H[�K��]
�!�;!�H�H[�K��]
��';%��JN���۝[�YB�ܘ�HK��]
�-�;,��HY�\�[��[��JK��]
�-�;,��KX�
H[�H�B�\�HY��]
��ܘ˙�]
�Y�H܈��K���\

JB�Y�\���VȻ-�;,��HHȻ'm:���ܘ˙�]
�'m:��H܈\���]
�-�;,����K��\���\���]
���{`k���K���;)����[Y
\���]
��;)����J_B�[�N���˝�\��[���&�:�.:�,; �:�;f.:� ;%��:�';%�;(';&n�	\ȋK��]
�'m:��JB��۝[�YB��܈�Y[��S��Q��VȺ�';%��HH��VȺ�';%��JK��\X�J�Y��B�][�\˘\[�
JB�Ⱥ�';%�;(%z��HH][�\�΍�B��˚[�����';%�;(%z�	Y:�H
:�:�n;'dz��H	Y:�JH��[�][�\�K[���]
��';%�;(%z��H܈�JH܈[�][�\�JB�Y���][�\΂���Y\�H�[JH�܈�[�
]K��]
��m;"��H܈�JK��[Y\�
B��܈][�
�܈�JHY�]��]
���:�.�JB��˝�\��[����';%�;(%z�:� :�a;%�;"�z��:��8�%:��:�.;'a;fez��;eg:�,; �:� 	Y:�m;'�z��:��������:�.;'m:�m;'m:�m:�,; �;&�:�.;"&;)�{'m:��{g�:���'�z��:������Y\�B���;-�;%�;'�;-�;,��:�';%�;(%z�;%�;%��; �:�;'`:��:�l:� ;%�'/:��:�g:�:����[YYH���K��]
�'m:����JK���\

H�܈H[�][�\�B��Y\��H�B��܈�[���]
�-�;%�;'�;-�;,��H܈�N��Y���\�[��[��J�X�
H܈��˙�]
�'m:��H܈��˙�]
�'m;'(�N���۝[�YB�Y����Ȼ'm:��JK���\

H��[��[YY���˝�\��[����';%�:��:�l:� ;%��;-�;%�;'�;-�;,�;(';&n�	\ȋ˙�]
�'m:��JB��۝[�YB��Y\�˘\[�
�B�Ȼ-�;%�;'�;-�;,��HH�Y\��Ό�B���Z�[�H��]
�.m;a�H�H܈�B��܈�[�\�
�Z�[˚�^\�
JN��H���Z�[���H܈��K���\

B��܈�Y[��S��Q��H��\X�J�Y��B��Z�[���HHΛ[Z]K����\

B�Ȼ.m;a�H�HH�Z�[�]\��