import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src import krx, llm


class _FakeStock:
    @staticmethod
    def get_nearest_business_day_in_a_week(date, prev=True):
        return date


class SafetyTests(unittest.TestCase):
    def test_krx_before_close_uses_previous_day(self):
        morning = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)
        with patch.object(krx, "_stock", return_value=_FakeStock()), \
             patch.object(krx, "now_kst", return_value=morning):
            self.assertEqual(krx.last_business_day(), "20260819")

    def test_payload_is_valid_json_under_budget(self):
        news = [{"제목": "x" * 400, "링크": f"https://example.com/{i}",
                 "출처": "테스트", "날짜": "2026-08-20", "경과시간": 1}
                for i in range(100)]
        payload = llm._payload({"뉴스": {"국내": news}}, "daily")
        json.loads(payload)
        self.assertLessEqual(len(payload), llm.MAX_PAYLOAD_CHARS)

    def test_unknown_video_and_hype_are_removed(self):
        raw = {
            "top5": [{"제목": "수급 폭발", "숫자": "", "영향": ""},
                     {"제목": "금리 하락", "숫자": "-5bp", "영향": "채권 ETF"}],
            "유튜브": [{"영상ID": "invented"}],
            "콘텐츠후보": [{"제목": "반도체", "이유": "ETF 수급이 다시 폭발"}],
        }
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}},
                               {"유튜브": {"급상승": [{"영상ID": "real"}]}})
        self.assertEqual([x["제목"] for x in out["top5"]], ["금리 하락"])
        self.assertEqual(out["유튜브"], [])
        self.assertEqual(out["콘텐츠후보"], [])

    def test_handoff_quote_without_source_id_is_removed(self):
        raw = {"발언정리": [{"이름": "홍길동", "소속": "A", "발언": "전망",
                            "출처": {"이름": "가짜"}}],
               "출연자추천": [{"이름": "홍길동", "이유": "전문가"}]}
        out = llm._postprocess_handoff(raw, 195, {"뉴스": {}})
        self.assertEqual(out["발언정리"], [])
        self.assertEqual(out["출연자추천"], [])

    def test_turnover_is_not_rewritten_as_inflow(self):
        raw = {"etf_레이더": [{"제목": "1조원 돈이 몰렸다", "사실": "거래대금 1조원",
                              "관찰": "", "구분": "수급", "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertEqual(out["etf_레이더"], [])

    def test_market_flow_is_not_assigned_to_a_stock(self):
        raw = {"시장브리핑": [{"시장": "국내", "제목": "반도체 반등",
                                 "결과": "코스피가 상승했습니다.",
                                 "원인": "외국인이 SK하이닉스를 집중 순매수했습니다. 자사주 발표가 있었습니다.",
                                 "ETF연결": "반도체 ETF 변동성을 봅니다.", "출처": []}]}
        data = {"뉴스": {"국내": [{"링크": "https://example.com/a", "경과시간": 1}]},
                "종목_후보_국내": [{"종목명": "SK하이닉스"}]}
        raw["시장브리핑"][0]["출처"] = [{"id": "n1", "이름": "테스트"}]
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertNotIn("외국인", out["시장브리핑"][0]["원인"])
        self.assertIn("자사주", out["시장브리핑"][0]["원인"])

    def test_single_stock_leverage_is_not_called_sector_etf(self):
        raw = {"etf_레이더": [{"제목": "반도체 레버리지 ETF", "사실": "상품 출시",
                              "관찰": "", "구분": "상품", "출처": []}]}
        data = {"뉴스": {"ETF": [{"제목": "삼성전자·SK하이닉스 단일종목 레버리지 ETF"}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertIn("단일종목", out["etf_레이더"][0]["제목"])

    def test_kodex_leverage_is_not_substituted_for_single_stock_turnover(self):
        raw = {"etf_레이더": [{"제목": "레버리지 거래대금 1조", "사실": "KODEX 레버리지 1조원",
                              "관찰": "", "구분": "레버리지", "출처": []}]}
        data = {"뉴스": {"ETF": [{"제목": "삼성전자·SK하이닉스 단일종목 레버리지 ETF"}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertNotIn("KODEX 레버리지", out["etf_레이더"][0]["사실"])

    def test_both_markets_and_one_complete_kakao_top3_are_always_present(self):
        raw = {"시장브리핑": [{"시장": "미국", "제목": "미 증시 하락", "결과": "나스닥 -1%",
                                 "원인": "금리 상승", "ETF연결": "나스닥100 확인", "출처": []}],
               "etf_레이더": [{"제목": "ETF 제도 변경", "사실": "제도가 변경됐습니다.", "관찰": "", "출처": []}],
               "오늘관전": ["금리 흐름", "외국인 수급"]}
        data = {"날짜표시": "2026년 8월 21일 (금)", "뉴스": {},
                "국내지수": [{"이름": "코스피", "종가": 6852.58, "등락률": 5.89},
                          {"이름": "코스닥", "종가": 840.89, "등락률": 1.99}],
                "지표": {}}
        out = llm._postprocess(raw, {"카카오": {"글자수_제한": 195}, "ETF_레이더": {}}, data)
        self.assertEqual([b["시장"] for b in out["시장브리핑"]], ["국내", "미국"])
        self.assertEqual(list(out["카톡"]), ["1"])
        self.assertIn("1. 국내", out["카톡"]["1"])
        self.assertIn("2. 미국", out["카톡"]["1"])
        self.assertIn("3. ETF", out["카톡"]["1"])
        self.assertLessEqual(len(out["카톡"]["1"]), 195)

    def test_unsourced_market_cause_is_hidden_not_replaced_with_notice(self):
        raw = {"시장브리핑": [{"시장": "국내", "제목": "국내 증시", "결과": "코스피 상승",
                                 "원인": "확인되지 않은 추정", "ETF연결": "코스피200 확인",
                                 "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        kr = next(b for b in out["시장브리핑"] if b["시장"] == "국내")
        self.assertEqual(kr["원인"], "")
        self.assertNotIn("근거", json.dumps(out, ensure_ascii=False))

    def test_current_day_intraday_is_removed_from_top3(self):
        raw = {"top5": [{"제목": "코스닥 매도사이드카", "숫자": "8/21 장중 -4%", "영향": ""},
                        {"제목": "미 금리 상승", "숫자": "+4bp", "영향": "나스닥 부담"}]}
        data = {"날짜표시": "2026년 8월 21일 (금)", "뉴스": {}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual([x["제목"] for x in out["top5"]], ["미 금리 상승"])

    def test_us_story_cannot_use_krx_as_market_source(self):
        raw = {"시장브리핑": [{"시장": "미국", "제목": "미 증시 하락", "결과": "나스닥 하락",
                                 "원인": "금리 상승", "ETF연결": "성장주 ETF 부담",
                                 "출처": [{"url": "https://data.krx.co.kr", "이름": "KRX"},
                                        {"id": "n1", "이름": "테스트"}]}]}
        data = {"뉴스": {"국제": [{"링크": "https://example.com/us", "출처": "테스트",
                                  "경과시간": 1}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        us = next(b for b in out["시장브리핑"] if b["시장"] == "미국")
        urls = [s["url"] for s in us["출처"]]
        self.assertIn("https://finance.yahoo.com", urls)
        self.assertNotIn("https://data.krx.co.kr", urls)


if __name__ == "__main__":
    unittest.main()
