"""전 거래일 무결성·채널 관련성·ETF 뉴스 회귀 테스트."""
import unittest
from src.render import _channel_relevance, _daily_etf_news
class DailyIntegrityTests(unittest.TestCase):
    def test_etf_direct_video_is_highly_relevant_to_channel(self):
        grade, reason = _channel_relevance({"제목": "곧 은퇴인데 S&P500 ETF 월적립 매수해도 될까요?", "ETF점수": 6})
        self.assertEqual(grade, "높음")
        self.assertTrue(reason)
    def test_relevance_does_not_depend_on_today_top5(self):
        grade, _ = _channel_relevance({"제목": "연금저축과 ISA 절세 계좌 골든 루트", "ETF점수": 6})
        self.assertEqual(grade, "높음")
    def test_daily_etf_news_uses_domestic_market_date_only(self):
        data = {"국내기준일_ISO": "2026-08-27", "뉴스": {"ETF": [
            {"제목": "ETF 자금 이동", "링크": "https://example.com/a", "출처": "예시경제", "날짜": "2026-08-27T09:00:00+09:00"},
            {"제목": "다음날 ETF 기사", "링크": "https://example.com/b", "출처": "예시경제", "날짜": "2026-08-28T08:00:00+09:00"}]}}
        self.assertEqual([x["제목"] for x in _daily_etf_news(data)], ["ETF 자금 이동"])
if __name__ == "__main__":
    unittest.main()
