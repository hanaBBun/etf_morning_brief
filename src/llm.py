"""Gemini(무료 티어) 기반 요약·해석 생성.

제공자를 config.yaml 의 AI.제공자 로 바꾸면 claude / openai 로도 전환된다.
출력은 항상 정해진 JSON 스키마를 따른다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import ROOT, env, now_kst

log = logging.getLogger(__name__)
DAILY_AI_CACHE = ROOT / "brief_daily_cache.json"

SYSTEM = """당신은 한국의 ETF 전문 유튜브 채널 'ETF 아는형'의 작가를 위해
매일 오전 7시 브리핑을 작성하는 리서치 어시스턴트입니다.

이 브리핑의 목적은 단 하나입니다.
"오늘 ETF 아는형에서 무엇을 알아야 하고, 무엇을 물어봐야 하는지를 3분 안에 파악한다."

"오늘 경제뉴스를 많이 읽었다"는 느낌을 주는 브리핑은 실패입니다. 짧고 결정적이어야 합니다.

독자는 주식 관련 서적 3~4권을 읽은 수준의 기초 지식을 갖췄지만 전문가는 아닙니다.
전문 용어는 써도 되지만 왜 중요한지를 함께 설명하세요.

■ 규칙 1 — 한 이슈는 딱 한 번만 자세히 설명합니다
같은 이슈(예: 미 장기금리 급등)를 TOP 5와 시장브리핑에서 두 번 자세히 쓰지 마세요.
TOP 5에는 이슈명·숫자·ETF 영향만, 상세 해설은 '오늘 시장은 왜 움직였나'에서만 합니다.
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
ETF 레이더와 시장브리핑은 입력 데이터에 표시된 실제 기준시각을 따릅니다.
오전 실행이면 직전 마감, 장중 실행이면 당일 장중 수치를 `장중`이라고 명시해 반영합니다.
전쟁·제재·관세·정치 충돌·자연재해처럼 경제 뉴스가 아니어도 금리·환율·유가·공급망·증시에
영향을 줄 사건은 핵심 이슈에 포함합니다. 단, 경제로 전달되는 경로를 설명할 수 있어야 합니다.
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

■ 규칙 10 — 시장 해설은 숫자 나열이 아니라 인과의 흐름으로 씁니다
시장브리핑의 원인은 '확인된 사건·발표 → 금리·환율·업종·수급 중 전달 경로
→ 지수 결과' 순서로 연결하세요. 국내 증시는 외국인·기관 수급, 지수 기여 대형주,
상승·하락 업종 중 입력에 실제로 있는 근거를 우선합니다. 미국 기사만으로 한국 시장
원인을 대신하지 마세요. 기사에 없는 그럴듯한 이유를 채우지 마세요.

■ 규칙 11 — '현재 시장 국면'은 변화가 있을 때만 씁니다
매일 비슷한 위험선호·금리부담 설명을 반복하지 마세요. 전일 대비 국면 전환, 한·미의
뚜렷한 디커플링, 금리·달러·유가 등 자산 간 이례적 동행처럼 새로 설명할 변화가 있고
최근 기사 근거가 2건 이상일 때만 `시장국면`을 작성합니다. 아니면 null 로 답하세요.
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
  "시장국면": null,
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
              {"이름": "원인을 다룬 매체명", "id": "입력 기사 id"}]},
    {"시장": "글로벌", "제목": "시장에 영향을 줄 핵심 사건. 없으면 이 항목 자체를 생략",
     "결과": "확인된 사건", "원인": "경제·시장에 전달되는 경로", "ETF연결": "봐야 할 자산군",
     "출처": [{"이름": "매체명", "id": "입력 기사 id"}]}
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
     "이유": "이 주제를 제안하는 이유 한 줄. 최근 데이터 근거와 3~5일 뒤 업로드해도 의미가 남는지 반영.",
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

`시장국면`을 쓸 때만 다음 형식으로 바꾸세요.
{"제목":"국면 변화 12~24자", "설명":"이전과 달라진 점과 함께 볼 자산을 2~3문장",
 "출처":[{"이름":"매체명","id":"n1"},{"이름":"매체명","id":"n2"}]}

■ 분량 예산 — 이 브리핑 전체가 공백 포함 2,200자를 넘으면 실패입니다.
3분 안에 읽히는 것이 다른 무엇보다 우선입니다. 아래 글자 수를 지키세요.

| 항목 | 개수 | 글자 수 |
|---|---|---|
| 시장브리핑 | 미국·국내 각 1개 + 글로벌 0~1개 | 각 전체 350자 이내 |
| 시장브리핑.결과 | | 100자 이내 |
| 시장브리핑.원인 | | 160자 이내 |
| 시장브리핑.ETF연결 | | 100자 이내 |
| 오늘관전 | 3개 | 각 60자 이내 |
| top5.제목 | 5개 고정 | 8~16자 |
| top5.숫자 | | 30자 이내 |
| top5.영향 | | 30자 이내 |
| etf_레이더 | 0~8개 | 핵심도 순. 1~3번은 본문, 4~8번은 추가 뉴스 제목으로 표시 |
| etf_레이더.사실 | | 80자 이내 |
| etf_레이더.관찰 | | 100자 이내 |
| 유튜브 | 0~5개 | 핵심주제·훅 각 30자 이내 |
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

★ 시장브리핑은 경제 전반을 이해하는 핵심 해설입니다.
  · 미국·국내를 각각 1개씩 쓰고, 중요한 지정학·정책 사건이 있으면 글로벌 1개를 추가합니다.
  · 가장 큰 지수 변동을 설명하는 재료를 먼저 고릅니다. 대형 소비주 실적, 중앙은행 발언,
    국채 수급, 유가·지정학 등이 지수에 영향을 줬다면 누락하지 않습니다.
  · 원인은 나열하지 말고 '금리 상승 → 성장주 할인율 부담 → 나스닥 약세'처럼 전달 경로를 설명합니다.
  · ETF연결은 매수 추천이 아니라, 작가가 어떤 ETF군·테마·질문을 이어서 봐야 하는지 설명합니다.
  · 하루 등락만으로 추세 전환을 단정하지 마세요.

★ ETF 레이더는 KRX 수치뿐 아니라 입력에 포함된 일반 언론사의 의미 있는 ETF 기사도 후보입니다.
  레버리지 투자행태, 원자재·가상자산·섹터 ETF 자금 이동처럼 ETF 시장이나 콘텐츠 기획에
  시사점이 큰 기사를 우선합니다. 같은 운용사의 상품 홍보성 기사를 여러 개 싣지 마세요.
  `사실`은 입력에 수치가 있다면 등락률·순매수·순자산·거래대금 중 최소 1개를 그대로 포함합니다.
  수치 없이 "관심 증가", "강세", "주목"만 쓴 항목은 만들지 마세요.

오늘의개념은 VKOSPI, 듀레이션, 할인율, 실질금리, 환헤지, 베이시스포인트,
멀티플, 변동성 잠식, 괴리율, 커버드콜 같은 것 중 그날 뉴스와 실제로 연결되는 것을 고릅니다.

★ 카톡은 후처리에서 TOP 1~5를 한 메시지로 자동 생성합니다.
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
    if mode == "weekly" and data.get("주간_대표흐름"):
        d["주간_대표흐름"] = data["주간_대표흐름"]
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
        "세계정세": _slim_news(news.get("세계정세"), 8),
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
    trim_order = ("보도자료", "ETF", "지수", "레버리지", "ETF시장", "세계정세", "국제", "국내")
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


def _daily_key(data: dict) -> str:
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", str(data.get("날짜표시", "")))
    return (f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if m else now_kst().strftime("%Y-%m-%d"))


def _load_daily_ai(data: dict) -> dict:
    try:
        saved = json.loads(DAILY_AI_CACHE.read_text(encoding="utf-8"))
        return saved.get("ai") or {} if saved.get("날짜") == _daily_key(data) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_daily_ai(data: dict, result: dict) -> None:
    try:
        DAILY_AI_CACHE.write_text(json.dumps(
            {"날짜": _daily_key(data), "ai": result}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as e:
        log.warning("일간 브리핑 캐시 저장 실패: %s", e)


def _fallback_seed(data: dict) -> dict:
    """AI 없이도 검증된 숫자로 필수 골격을 만드는 최소 폴백."""
    rows = list(data.get("국내지수") or [])
    ind = data.get("지표") or {}
    for group in ("해외지수", "금리", "원자재", "변동성"):
        rows.extend(ind.get(group) or [])

    top = []
    for row in sorted(rows, key=lambda x: abs(float(x.get("등락률") or 0)), reverse=True):
        if row.get("종가") is None or len(top) >= 5:
            continue
        name = str(row.get("이름") or "시장 지표")
        pct = row.get("등락률")
        direction = "상승" if (pct or 0) > 0 else ("하락" if (pct or 0) < 0 else "변동")
        number = f"{row['종가']:,.2f}" + (f" ({pct:+.2f}%)" if pct is not None else "")
        top.append({"제목": f"{name} {direction}", "숫자": number,
                    "영향": f"{name} 연계 ETF 흐름 확인"})

    radar = []
    candidates = data.get("ETF_후보") or {}
    for r in candidates.get("거래량_급증") or []:
        radar.append({"구분": "거래량 급증", "제목": str(r.get("이름") or "ETF 거래량 변화"),
                      "사실": f"20일 평균 대비 거래량 {r.get('배수')}배",
                      "관찰": "거래량 증가가 다음 거래일까지 이어지는지 확인", "출처": []})
    for r in candidates.get("신규상장") or []:
        radar.append({"구분": "신규 상장", "제목": str(r.get("이름") or "신규 ETF"),
                      "사실": "KRX 신규 상장 목록에서 확인", "관찰": "초기 거래량과 괴리율 확인",
                      "출처": []})
    if not radar:
        for group in ("ETF시장", "ETF", "레버리지", "보도자료"):
            for art in ((data.get("뉴스") or {}).get(group) or []):
                if art.get("제목") and art.get("링크"):
                    radar.append({"구분": "시장 뉴스", "제목": str(art["제목"])[:24],
                                  "사실": str(art.get("요약") or art["제목"])[:80],
                                  "관찰": "관련 ETF의 거래량과 자금 흐름 확인",
                                  "출처": [{"이름": art.get("출처", "기사"),
                                           "url": art["링크"], "날짜": art.get("날짜", "")}]})
                if radar:
                    break
            if radar:
                break

    first = top[0] if top else {"제목": "오늘 시장", "숫자": ""}
    concept = ({"용어": "베이시스포인트(bp)",
                "연결": "국채금리 변화를 읽을 때 사용하는 단위",
                "설명": "1bp는 0.01%포인트입니다. 금리가 4.65%에서 4.70%로 오르면 5bp 상승한 것입니다. 채권·성장주 ETF의 금리 민감도를 비교할 때 쓰입니다."}
               if ind.get("금리") else
               {"용어": "변동성", "연결": f"{first['제목']} 흐름을 해석하는 기준",
                "설명": "가격이 일정 기간 얼마나 크게 오르내리는지를 나타냅니다. 같은 수익률이라도 변동성이 크면 손실 회복에 더 큰 상승률이 필요하므로 ETF 비교 때 함께 봐야 합니다."})
    return {
        "top5": top,
        "시장브리핑": [_fallback_market_brief("국내", data), _fallback_market_brief("미국", data)],
        "오늘관전": [f"{first['제목']} 흐름이 다음 거래일까지 이어지는지 확인"],
        "etf_레이더": radar[:5],
        "유튜브": [],
        "콘텐츠후보": [{"코너": "ETF 처방전", "제목": f"{first['제목']}, ETF에는 어떤 영향?",
                         "이유": f"오늘 핵심 수치 {first['숫자']}를 ETF 관점에서 설명할 필요",
                         "관련ETF": first["제목"], "차별점": "수치와 ETF 전달 경로 중심",
                         "질문": "오늘의 시장 변동이 ETF 투자자에게 중요한 이유는 무엇인가요?"}],
        "오늘의개념": concept,
        "체크포인트": [{"유형": "확인", "때": "다음 거래일",
                         "내용": f"{first['제목']} 흐름의 지속 여부"}],
    }


def _merge_unique(groups: list[list], key, limit: int) -> list:
    out, seen = [], set()
    for group in groups:
        for item in group or []:
            marker = key(item)
            if not marker or marker in seen:
                continue
            seen.add(marker)
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def _stabilize_daily(fresh: dict, cached: dict, data: dict, cfg: dict) -> dict:
    """부분 응답이 이전의 완성된 당일 결과를 지우지 않게 필수 섹션을 합친다."""
    fallback = _postprocess(_fallback_seed(data), cfg, data)
    result = dict(fresh or {})
    # 장중 재실행이면 현재 숫자 3개를 먼저 반영하고, 아침에 잡힌 주요 뉴스도
    # 남은 자리에 유지한다. 이전 결과만 통째로 재사용해 시세가 낡는 것을 막는다.
    current_top = fallback.get("top5", [])
    result["top5"] = _merge_unique(
        [fresh.get("top5", []), current_top[:3], cached.get("top5", []), current_top[3:]],
        lambda x: str(x.get("제목") or ""), 5)
    for i, item in enumerate(result["top5"], 1):
        item["순위"] = i
    fresh_markets = {x.get("시장"): x for x in fresh.get("시장브리핑", [])}
    cached_markets = {x.get("시장"): x for x in cached.get("시장브리핑", [])}
    fallback_markets = {x.get("시장"): x for x in fallback["시장브리핑"]}
    market_briefs = []
    for market_name in ("국내", "미국"):
        if market_name in fresh_markets:
            market_briefs.append(fresh_markets[market_name])
        elif market_name in cached_markets:
            item = dict(cached_markets[market_name])
            # 설명은 당일 정상 결과에서 유지하되 결과 숫자는 이번 호출 값으로 교체.
            item["결과"] = fallback_markets[market_name]["결과"]
            market_briefs.append(item)
        else:
            market_briefs.append(fallback_markets[market_name])
    global_story = fresh_markets.get("글로벌") or cached_markets.get("글로벌")
    if global_story:
        market_briefs.append(global_story)
    result["시장브리핑"] = market_briefs
    result["etf_레이더"] = _merge_unique(
        [fresh.get("etf_레이더", []), cached.get("etf_레이더", []), fallback.get("etf_레이더", [])],
        lambda x: str(x.get("제목") or ""), int((cfg.get("ETF_레이더") or {}).get("최대_항목수", 3)))
    result["유튜브"] = _merge_unique(
        [fresh.get("유튜브", []), cached.get("유튜브", [])],
        lambda x: str(x.get("영상ID") or ""), 5)
    result["콘텐츠후보"] = _merge_unique(
        [fresh.get("콘텐츠후보", []), cached.get("콘텐츠후보", []), fallback["콘텐츠후보"]],
        lambda x: str(x.get("제목") or ""), 2)
    result["오늘관전"] = _merge_unique(
        [fresh.get("오늘관전", []), cached.get("오늘관전", []), fallback["오늘관전"]],
        lambda x: str(x), 3)
    result["체크포인트"] = _merge_unique(
        [fresh.get("체크포인트", []), cached.get("체크포인트", []), fallback["체크포인트"]],
        lambda x: f"{x.get('때')}|{x.get('내용')}", 4)
    result["오늘의개념"] = (fresh.get("오늘의개념") or cached.get("오늘의개념")
                            or fallback["오늘의개념"])
    # 국면 카드는 '오늘 새로 감지된 변화'만 허용한다. 전날 캐시를 재사용하면
    # 매일 같은 문구가 반복되므로 fresh 결과만 쓴다.
    result["시장국면"] = fresh.get("시장국면")
    result["댓글키워드"] = fresh.get("댓글키워드") or cached.get("댓글키워드") or ""
    result["카톡"] = _build_daily_kakao(result, data,
                                      int((cfg.get("카카오") or {}).get("글자수_제한", 195)))
    return result


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
                "\n이 브리핑의 역할은 '지난 한 주 복기'입니다. 국내 투자자 해외주식 주간 수급,\n"
                "ETF 주간 수익률·자금흐름, 순자산 순위 변동, 한 주 지수 흐름을 중심으로\n"
                "무엇이 움직였고 왜 움직였는지 정리한 뒤 다음 주 일정을 연결하세요.\n"
                "입력의 `주간_대표흐름`은 대표 자산 1일·1주·1개월 비교표로 화면에 따로 표시됩니다.\n"
                "본문에서 표의 숫자를 전부 반복하지 말고 기간별 방향이 달라진 핵심만 해설하세요.\n"
                "ETF 뉴스 6선은 목요일 전달문으로 분리했으므로 여기서는 빈 배열로 두세요.\n"
            )
        elif data.get("브리핑역할") == "이번 주 준비":
            label = "월요일 오전 브리핑"
            extra = (
                "\n이 브리핑의 역할은 토요일 주간 복기를 반복하는 것이 아니라 '이번 주 준비'입니다.\n"
                "금요일 종가는 짧게만 확인하고, 주말 동안 새로 나온 뉴스·정책·지정학 변수와\n"
                "이번 주 경제 일정, 월요일 개장 후 확인할 수급·ETF군을 우선하세요.\n"
                "주말 동안 달라진 것이 없는 재료는 억지로 새 이야기처럼 반복하지 마세요.\n"
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
                result = _postprocess(parsed, cfg, data, mode)
                if mode == "daily":
                    result = _stabilize_daily(result, _load_daily_ai(data), data, cfg)
                    _save_daily_ai(data, result)
                return result
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
                        result = _postprocess(parsed, cfg, data, mode)
                        if mode == "daily":
                            result = _stabilize_daily(result, _load_daily_ai(data), data, cfg)
                            _save_daily_ai(data, result)
                        return result
                except Exception as e2:  # noqa: BLE001
                    log.warning("재시도도 실패: %s", str(e2)[:200])
    log.error("AI 생성 전부 실패 — 당일 캐시와 검증된 원자료로 완성본을 복구합니다.")
    if mode == "daily":
        result = _stabilize_daily({}, _load_daily_ai(data), data, cfg)
        _save_daily_ai(data, result)
        return result
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
ASSET_MGRS = ("자산운용", "투신운용", "아문디", "한투운용", "미래에셋", "삼성운용",
              "타임폴리오", "트러스톤", "마이다스에셋")
BOAST = ("돌파", "1위", "최대", "사상 최", "임박", "넘었다", "쏠림", "급증")


def _is_product_item(item: dict) -> bool:
    """운용사가 자기 상품을 알리는 기사인지.

    ① 제목·본문에 운용사 이름이 있으면 대개 그 회사 보도자료다.
    ② 운용사 이름이 없어도 상품 브랜드(KODEX·TIGER…) + 실적 자랑이면 마찬가지.
    """
    text = f"{item.get('제목', '')} {item.get('사실', '')}"
    if any(w in text for w in ASSET_MGRS):
        return True
    return (any(b in text for b in ETF_BRANDS)
            and any(w in text for w in BOAST))


def _limit_product_items(radar: list[dict], keep: int = 1) -> list[dict]:
    """단일 상품 자랑 항목을 keep 개까지만 남긴다.

    운용사 보도자료가 검색에 많이 잡혀서, 그냥 두면 레이더 세 칸이
    전부 '○○ETF 순자산 N억 돌파'로 채워진다. 그건 시장 정보가 아니다.
    """
    out, used = [], 0
    for r in radar:
        if _is_product_item(r):
            if used >= keep:
                log.warning("단일 상품 홍보성 항목 제외: %s", str(r.get("제목"))[:40])
                continue
            used += 1
        out.append(r)
    return out


def _warn_pr(item: dict) -> None:
    """홍보 표현이 남아 있으면 로그로만 알린다.

    단어를 지우면 "업계 최초로 차별화된 서비스를 선보였습니다"가
    "로 된 서비스를 습니다"가 되어 문장이 망가진다.
    생성 단계(규칙 5-1)에서 막는 것이 맞고, 여기서는 감시만 한다.
    """
    text = f"{item.get('제목', '')} {item.get('사실', '')} {item.get('관찰', '')}"
    hit = [w for w in PR_WORDS if w in text]
    if hit:
        log.warning("홍보성 표현이 남아 있습니다 (%s): %s",
                    ", ".join(hit), str(item.get("제목"))[:40])


def _has_hype(item: dict) -> bool:
    text = " ".join(str(item.get(k, ""))
                    for k in ("제목", "사실", "해석", "관찰", "이유"))
    return any(w in text for w in PR_WORDS + UNSUPPORTED_HYPE)


def _mixes_turnover_and_flow(item: dict) -> bool:
    """거래대금을 순유입처럼 바꿔 쓴 항목인지 검사한다."""
    evidence = " ".join(str(item.get(k, "")) for k in ("사실", "이유", "관찰"))
    claim = " ".join(str(item.get(k, "")) for k in ("제목", "이유", "관련ETF"))
    return any(w in evidence for w in TURNOVER_WORDS) and any(w in claim for w in FLOW_WORDS)


def _has_single_stock_leverage_news(data: dict) -> bool:
    for group in (data.get("뉴스") or {}).values():
        for art in group or []:
            title = str(art.get("제목", ""))
            if ("레버리지" in title
                    and any(w in title for w in ("단일종목", "삼전닉스", "삼성전자", "SK하이닉스"))):
                return True
    return False


def _fix_etf_classification(item: dict, single_stock_context: bool) -> dict:
    """단일종목 상품을 업종형 반도체 ETF로 부르는 오류를 교정한다."""
    if not single_stock_context:
        return item
    for key in ("제목", "사실", "관찰", "이유", "관련ETF", "질문", "차별점"):
        text = str(item.get(key, ""))
        text = text.replace("반도체 레버리지 ETF", "삼성전자·SK하이닉스 단일종목 레버리지 ETF")
        text = text.replace("반도체 레버리지", "삼성전자·SK하이닉스 단일종목 레버리지")
        if "1조" in text:
            text = text.replace("KODEX 레버리지", "삼성전자·SK하이닉스 단일종목 레버리지 ETF")
        item[key] = text
    return item


def _strip_stock_flow_claim(text: Any, data: dict) -> str:
    """종목별 수급 데이터 없이 시장 전체 수급을 개별주에 붙인 문장을 제거한다."""
    value = str(text or "").strip()
    names = {str(s.get("종목명") or "").strip()
             for s in (data.get("종목_후보_국내") or [])}
    names.update(("삼성전자", "SK하이닉스", "하이닉스"))
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", value) if p.strip()]
    kept = [p for p in parts
            if not (any(n and n in p for n in names)
                    and any(w in p for w in STOCK_FLOW_WORDS))]
    return " ".join(kept)


def _is_current_intraday(item: dict, data: dict) -> bool:
    """오전 7시용 브리핑에 실행 당일 장중 속보가 섞였는지 확인한다."""
    text = " ".join(str(item.get(k, "")) for k in ("제목", "숫자", "영향"))
    if not any(w in text for w in ("장중", "사이드카")):
        return False
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", str(data.get("날짜표시", "")))
    if not m:
        return False
    mmdd = f"{int(m.group(2))}/{int(m.group(3))}"
    return mmdd in text


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


def _normalize_market_sources(items: list[dict], ages: dict[str, int]) -> list[dict]:
    """미국·국내 출처를 바로잡고 기사로 확인되지 않은 원인은 조용히 숨긴다."""
    out = []
    for it in items:
        market_name = it.get("시장")
        srcs = [s for s in (it.get("출처") or []) if isinstance(s, dict)]
        if market_name == "글로벌":
            # 글로벌 사건은 기사 자체가 근거다. 기사 링크가 없으면 카드를 싣지 않는다.
            if not any(s.get("url") in ages for s in srcs):
                log.warning("기사 근거 없는 글로벌 변수 생략: %s", it.get("제목", ""))
                continue
            fixed = None
        elif market_name == "미국":
            srcs = [s for s in srcs if "krx.co.kr" not in str(s.get("url", ""))]
            fixed = {"이름": "Yahoo Finance", "url": "https://finance.yahoo.com"}
        else:
            srcs = [s for s in srcs if "finance.yahoo.com" not in str(s.get("url", ""))]
            fixed = {"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"}
        if fixed and not any(s.get("url") == fixed["url"] for s in srcs):
            srcs.insert(0, fixed)
        # 숫자 출처만으로 '왜 움직였나'를 쓰지 않는다.
        if not any(s.get("url") in ages for s in srcs):
            log.warning("원인 기사 근거가 없어 인과 설명 생략: %s", it.get("제목", ""))
            it["원인"] = ""
        it["출처"] = srcs
        out.append(it)
    return out


def _quote_summary(rows: list[dict], names: tuple[str, ...]) -> str:
    parts = []
    for row in rows or []:
        if str(row.get("이름")) not in names or row.get("종가") is None:
            continue
        pct = row.get("등락률")
        pct_text = f"{float(pct):+.2f}%" if pct is not None else ""
        parts.append(f"{row.get('이름')} {pct_text}".strip())
    return " · ".join(parts)


def _fallback_market_brief(market_name: str, data: dict) -> dict:
    """모델이 한국·미국 중 하나를 빼도 양쪽 시장을 반드시 표시한다."""
    if market_name == "국내":
        result = _quote_summary(data.get("국내지수") or [], ("코스피", "코스닥"))
        return {"시장": "국내", "제목": "국내 증시 마감 흐름",
                "결과": result or "국내 지수 데이터를 확인해야 합니다.",
                "원인": "",
                "ETF연결": "코스피200·코스닥150 ETF의 등락과 대형주 기여도를 함께 확인할 필요가 있습니다.",
                "출처": [{"이름": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr"}]}
    rows = []
    for group in (data.get("지표") or {}).values():
        rows.extend(group or [])
    result = _quote_summary(rows, ("S&P 500", "나스닥 종합", "다우 30"))
    return {"시장": "미국", "제목": "미국 증시 마감 흐름", "결과": result or "미국 지수 데이터를 확인해야 합니다.",
            "원인": "",
            "ETF연결": "S&P500·나스닥100 ETF와 금리 민감 성장주 흐름을 함께 확인할 필요가 있습니다.",
            "출처": [{"이름": "Yahoo Finance", "url": "https://finance.yahoo.com"}]}


def _clip(text: Any, n: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= n else value[:max(n - 1, 1)].rstrip() + "…"


def _build_daily_kakao(d: dict, data: dict, limit: int) -> dict:
    date = str(data.get("날짜표시") or "")
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", date)
    stamp = f"{int(m.group(1))}/{int(m.group(2))}" if m else "오늘"
    top = d.get("top5") or []

    def item_line(item: dict, include_number: bool = True) -> str:
        title = str(item.get("제목") or "").strip()
        number = str(item.get("숫자") or "").strip()
        return f"{item.get('순위')}. {title}" + (f" | {number}" if number and include_number else "")

    # TOP 1~5 제목은 반드시 모두 보존한다. 195자를 넘을 때만 중요도가 낮은
    # 5번부터 숫자 설명을 통째로 빼며, 문장을 중간에서 자르지 않는다.
    include_numbers = [True] * len(top[:5])
    while True:
        lines = [f"☀️ {stamp} 브리핑"] + [
            item_line(item, include_numbers[i]) for i, item in enumerate(top[:5])]
        message = "\n".join(lines)
        if len(message) <= limit:
            return {"1": message}
        idx = next((i for i in range(len(include_numbers) - 1, -1, -1)
                    if include_numbers[i]), None)
        if idx is None:
            # 스키마의 제목 제한을 지키면 도달하지 않는다. 그래도 항목 삭제보다는
            # 제목만 온전히 남긴 메시지를 반환한다.
            return {"1": "\n".join(lines)}
        include_numbers[idx] = False


def _topup_top5(d: dict) -> None:
    """안전 필터로 항목이 빠져도 검증된 ETF 레이더에서 TOP 5를 보충한다."""
    top = d.get("top5") or []
    seen = {str(x.get("제목") or "").strip() for x in top}
    for radar in d.get("etf_레이더") or []:
        title = str(radar.get("제목") or "").strip()
        if not title or title in seen or len(top) >= 5:
            continue
        fact = str(radar.get("사실") or "").strip()
        top.append({"제목": title, "숫자": fact[:30],
                    "영향": str(radar.get("관찰") or "")[:30]})
        seen.add(title)
    for i, item in enumerate(top[:5], 1):
        item["순위"] = i
    d["top5"] = top[:5]


def _topic_words(value: Any) -> set[str]:
    """제목의 브랜드·조사 차이를 무시하고 같은 뉴스 주제를 찾는다."""
    stop = {"etf", "상승", "하락", "급등", "급락", "관련", "주목", "전망",
            "국내", "해외", "시장", "상품", "출시", "상장", "뉴스", "자금",
            "유입", "유출", "억원"}
    return {w.lower() for w in re.findall(r"[가-힣A-Za-z0-9]+", str(value or ""))
            if len(w) >= 2 and w.lower() not in stop}


def _dedupe_topics(items: list[dict]) -> list[dict]:
    """같은 링크 또는 핵심어가 겹치는 레이더·일정을 한 번만 남긴다."""
    out: list[dict] = []
    links: set[str] = set()
    wordsets: list[set[str]] = []
    for item in items:
        item_links = {str(s.get("url")) for s in (item.get("출처") or [])
                      if isinstance(s, dict) and s.get("url")}
        words = _topic_words(f"{item.get('제목', '')} {item.get('사실', '')}")
        duplicate_words = any(len(words & old) >= 2 and
                              len(words & old) / max(min(len(words), len(old)), 1) >= .55
                              for old in wordsets)
        if (item_links and item_links & links) or duplicate_words:
            continue
        out.append(item)
        links |= item_links
        wordsets.append(words)
    return out


def _qualify_retirement_claim(text: Any) -> str:
    """상품별 공식 확인 없는 퇴직연금 가능 여부를 단정하지 않는다."""
    value = str(text or "")
    risky = ("비위험자산", "비위험 자산", "100% 편입", "100% 투자",
             "퇴직연금에서 전액", "연금계좌에서 전액")
    if any(x in value for x in risky):
        return "퇴직연금 편입 한도와 위험자산 분류는 상품별 투자설명서·판매사 기준 확인이 필요합니다."
    return value


def _postprocess(d: Any, cfg: dict, data: dict | None = None, mode: str = "daily") -> dict:
    d = _as_dict(d) or {}
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

    # TOP 5 — 과장 표현이 남은 항목은 표시하지 않는다.
    top5 = [r for r in (d.get("top5") or []) if isinstance(r, dict)]
    top5 = [r for r in top5 if not _has_hype(r)]
    for i, r in enumerate(top5, 1):
        r["순위"] = i
    d["top5"] = top5[:5]

    # 결과 → 원인 → ETF 연결로 읽히는 미국·국내 시장 해설
    briefs = _drop_filler(d.get("시장브리핑"), ("제목", "결과"))
    out_briefs = []
    seen_markets = set()
    for b in briefs:
        market_name = str(b.get("시장") or "").strip()
        if market_name not in ("미국", "국내", "글로벌") or market_name in seen_markets or _has_hype(b):
            continue
        for key, limit_n in (("제목", 70), ("결과", 140), ("원인", 220), ("ETF연결", 140)):
            b[key] = _strip_stock_flow_claim(b.get(key), data)[:limit_n]
        if not b["결과"] or not b["원인"]:
            continue
        seen_markets.add(market_name)
        out_briefs.append(b)
    d["시장브리핑"] = out_briefs[:3]
    d["오늘관전"] = [_strip_stock_flow_claim(x, data)[:90]
                         for x in (d.get("오늘관전") or [])
                         if _strip_stock_flow_claim(x, data)][:3]
    d.pop("시장요약", None)
    d.pop("핵심이슈", None)

    # 출처를 번호에서 실제 링크로 되돌린다 (모델이 URL 을 옮겨 적지 않게 한 대가)
    idx = _link_index(data)
    for key in ("시장브리핑", "etf_레이더"):
        for c in d.get(key) or []:
            if isinstance(c, dict):
                c["출처"] = _resolve_srcs(c.get("출처"), idx)
    regime = d.get("시장국면")
    if isinstance(regime, dict):
        regime["출처"] = _resolve_srcs(regime.get("출처"), idx)
        fresh = [s for s in regime["출처"] if s.get("url")]
        if not regime.get("제목") or not regime.get("설명") or len(fresh) < 2:
            d["시장국면"] = None
        else:
            regime["제목"] = _clip(regime["제목"], 40)
            regime["설명"] = _clip(regime["설명"], 240)
    else:
        d["시장국면"] = None

    # ETF 레이더 — 빈 항목·필러 제거, 오래된 근거 제거, 관찰 면책 문장 정리
    ages = _article_age(data)
    if d.get("시장국면"):
        fresh_regime_sources = [s for s in d["시장국면"].get("출처", [])
                                if s.get("url") in ages and ages[s["url"]] <= STALE_HOURS]
        if len(fresh_regime_sources) < 2:
            d["시장국면"] = None
    d["시장브리핑"] = _normalize_market_sources(
        _strip_stale_sources(d["시장브리핑"], ages), ages)
    present = {b.get("시장") for b in d["시장브리핑"]}
    for market_name in ("국내", "미국"):
        if market_name not in present:
            log.warning("모델이 %s 시장을 누락해 구조화 데이터로 보충합니다", market_name)
            d["시장브리핑"].append(_fallback_market_brief(market_name, data))
    order = {"국내": 0, "미국": 1, "글로벌": 2}
    d["시장브리핑"].sort(key=lambda b: order.get(b.get("시장"), 9))
    radar_max = int((cfg.get("ETF_레이더") or {}).get("최대_항목수", 3))
    radar = _drop_filler(d.get("etf_레이더"), ("제목", "사실"))
    radar = _strip_stale_sources(radar, ages)
    radar = _limit_product_items(radar, keep=1)
    single_stock_context = _has_single_stock_leverage_news(data)
    radar = [_fix_etf_classification(r, single_stock_context) for r in radar]
    radar = [r for r in radar if not _has_hype(r) and not _mixes_turnover_and_flow(r)]
    radar = _dedupe_topics(radar)[:radar_max]
    for r in radar:
        r["사실"] = _qualify_retirement_claim(r.get("사실"))
        r["관찰"] = _trim_hedge(r.get("관찰", ""))
        _warn_pr(r)
    d["etf_레이더"] = radar
    log.info("ETF 레이더 %d개 (구분: %s)", len(radar),
             ", ".join(str(r.get("구분", "")) for r in radar) or "-")

    valid_video_ids = {str(v.get("영상ID"))
                       for v in ((data.get("유튜브") or {}).get("급상승") or [])}
    d["유튜브"] = [v for v in (d.get("유튜브") or [])
                    if isinstance(v, dict)
                    and str(v.get("영상ID")) in valid_video_ids][:5]
    plans = _drop_filler(d.get("콘텐츠후보"), ("제목", "이유"))
    plans = [_fix_etf_classification(p, single_stock_context) for p in plans]
    d["콘텐츠후보"] = [p for p in plans
                        if not _has_hype(p) and not _mixes_turnover_and_flow(p)][:2]
    for p in d["콘텐츠후보"]:
        p["이유"] = _qualify_retirement_claim(p.get("이유"))

    # 체크포인트 — 일정/확인 두 유형. 구버전 키('일정')도 받아준다.
    cps = d.get("체크포인트") or d.get("일정") or []
    cps = _drop_filler(cps, ("내용",))
    for c in cps:
        if c.get("유형") not in ("일정", "확인"):
            c["유형"] = "일정" if any(ch.isdigit() for ch in str(c.get("때", ""))) else "확인"
    seen_cp = set()
    d["체크포인트"] = []
    for c in cps:
        key = re.sub(r"\s+", "", str(c.get("내용") or "")).lower()
        if key and key not in seen_cp:
            seen_cp.add(key)
            d["체크포인트"].append(c)
        if len(d["체크포인트"]) == 4:
            break
    d.pop("일정", None)

    concept = d.get("오늘의개념")
    if not isinstance(concept, dict) or not concept.get("용어"):
        d["오늘의개념"] = None

    if not str(d.get("댓글키워드") or "").strip():
        d["댓글키워드"] = ""

    _topup_top5(d)

    # 같은 링크를 두 번 보내지 않는, 항목 중간 절단 없는 TOP 1~5 요약.
    d["카톡"] = _build_daily_kakao(d, data, limit)

    return d


def _news_row(art: dict, theme: str = "", line: str = "") -> dict:
    """기사 원본에서 6선 한 줄을 만든다. 제목·매체·날짜·링크는 전부 원본 값."""
    return {
        "제목": art.get("제목", ""),
        "매체": art.get("출처", ""),
        "날짜": _mmdd(art.get("날짜", "")),
        "url": art.get("링크", ""),
        "주제": theme or _GROUP_THEME.get(art.get("_그룹", ""), "테마"),
        "한줄": line or str(art.get("요약") or "")[:40],
    }


_GROUP_THEME = {"보도자료": "신규 상장", "레버리지": "레버리지·인버스",
                "지수": "지수 변경", "ETF": "수급", "국내": "시장 규모",
                "ETF시장": "시장 구조", "증권": "시장 규모"}

# 6선을 채울 때 어느 그룹부터 볼지
_TOPUP_ORDER = ("ETF시장", "증권", "국내", "ETF", "레버리지", "지수", "보도자료")

# 전달문은 이번 주 기사만 다룬다. 이보다 오래된 기사는 채우기에도 쓰지 않는다.
_TOPUP_MAX_HOURS = 168


def _fresh(art: dict) -> bool:
    h = art.get("경과시간")
    return h is not None and int(h) <= _TOPUP_MAX_HOURS


def _postprocess_handoff(d: dict, limit: int, data: dict) -> dict:
    """목요일 전달문 후처리 — 환각 링크 차단 + 6선 채우기."""
    idx = _link_index(data)

    # 뉴스 6선: 모델은 id 만 고른다. 제목·링크는 원본에서 붙인다.
    news, seen = [], set()
    raw = d.get("etf_뉴스6선") or []
    for n in raw:
        if not isinstance(n, dict):
            continue
        art = idx.get(str(n.get("id") or "").strip())
        if not art:
            log.warning("모르는 기사 번호라 제외: %s", str(n.get("id"))[:20])
            continue
        if art.get("링크") in seen:
            continue
        seen.add(art.get("링크"))
        news.append(_news_row(art, str(n.get("주제") or ""), str(n.get("한줄") or "")))

    # 6건이 안 되면 수집한 기사에서 직접 채운다. (지어내는 게 아니라 고르는 것)
    if len(news) < 6:
        picked_by_model = len(news)
        pools = {g: list((data.get("뉴스") or {}).get(g) or []) for g in _TOPUP_ORDER}
        # 한 그룹에서 몰아 뽑지 않도록 그룹을 돌아가며 한 건씩 가져온다
        while len(news) < 6 and any(pools.values()):
            for g in _TOPUP_ORDER:
                if len(news) >= 6:
                    break
                while pools[g]:
                    art = pools[g].pop(0)
                    if art.get("링크") and art["링크"] not in seen and _fresh(art):
                        seen.add(art["링크"])
                        news.append(_news_row(art))
                        break
        log.warning("모델이 6선 중 %d건만 골라 %d건을 수집 기사에서 보충했습니다",
                    picked_by_model, len(news) - picked_by_model)
    # 운용사 홍보성 기사는 6건 중 2건까지만. 빠진 자리는 시장 기사로 다시 채운다.
    kept = _limit_product_items(news, keep=2)
    dropped = len(news) - len(kept)
    news = kept
    if dropped:
        for group in _TOPUP_ORDER:
            for art in (data.get("뉴스") or {}).get(group) or []:
                if len(news) >= 6:
                    break
                if art.get("링크") in seen or not _fresh(art):
                    continue
                row = _news_row(art)
                if _is_product_item(row):
                    continue
                seen.add(art["링크"])
                news.append(row)
            if len(news) >= 6:
                break
    d["etf_뉴스6선"] = news[:6]
    log.info("뉴스 6선 확정 %d건 (홍보성 %d건 교체)", len(d["etf_뉴스6선"]), dropped)

    # 발언: 이름·소속·발언이 모두 있어야 한다. 출처는 번호로 해석한다.
    quotes = []
    for q in d.get("발언정리") or []:
        if not isinstance(q, dict):
            continue
        if not (q.get("이름") and q.get("소속") and q.get("발언")):
            continue
        src = q.get("출처") if isinstance(q.get("출처"), dict) else {}
        art = idx.get(str(src.get("id") or "").strip())
        if art:
            q["출처"] = {"이름": src.get("이름") or art.get("출처", ""),
                         "url": art.get("링크", ""),
                         "날짜": _mmdd(art.get("날짜", ""))}
        else:
            log.warning("원문 기사 번호가 없는 발언 제외: %s", q.get("이름"))
            continue
        for bad in BANNED:
            q["발언"] = str(q["발언"]).replace(bad, "")
        quotes.append(q)
    d["발언정리"] = quotes[:6]
    log.info("발언정리 %d명 (모델 응답 %d명)",
             len(quotes), len(d.get("발언정리") or []) or len(quotes))
    if not quotes:
        bodies = sum(1 for g in (data.get("뉴스") or {}).values()
                     for it in (g or []) if it.get("본문"))
        log.warning("발언정리가 비었습니다 — 본문을 확보한 기사가 %d건입니다. "
                    "본문이 0건이면 기사 원문 수집이 막힌 것입니다.", bodies)

    # 출연자 추천: 발언정리에 없는 사람은 근거가 없으므로 뺀다
    named = {str(q.get("이름", "")).strip() for q in quotes}
    guests = []
    for g in d.get("출연자추천") or []:
        if not isinstance(g, dict) or not g.get("이름") or not g.get("이유"):
            continue
        if str(g["이름"]).strip() not in named:
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
