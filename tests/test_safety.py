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
        raw = {"핵심이슈": [{"제목": "반도체", "사실": "코스피 상승", "해석": "",
                              "출처": [], "종목": [{"이름": "SK하이닉스", "등락": "+12.7%",
                                                "이유": "외국인 순매수 집중"}]}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertNotIn("외국인", out["핵심이슈"][0]["종목"][0]["이유"])

    def test_single_stock_leverage_is_not_called_sector_etf(self):
        raw = {"etf_레이더": [{"제목": "반도체 레버리지 ETF", "사실": "상품 출시",
                              "관찰": "", "구분": "상품", "출처": []}]}
        data = {"뉴스": {"ETF": [{"제목": "삼성전자·SK하이닉스 단일종목 레버리지 ETF"}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertIn("단일종목", out["etf_레이더"][0]["제목"])


if __name__ == "__main__":
    unittest.main()
