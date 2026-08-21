import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import krx, llm, news, render, youtube


class _FakeStock:
    @staticmethod
    def get_nearest_business_day_in_a_week(date, prev=True):
        return date


class SafetyTests(unittest.TestCase):
    def test_youtube_same_day_cache_survives_rerun_without_api(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "youtube_daily_cache.json"
            cache.write_text(json.dumps({
                "날짜": "2026-08-21",
                "급상승": [{"영상ID": "kept", "제목": "아침에 찾은 영상"}],
                "댓글샘플": [],
            }, ensure_ascii=False), encoding="utf-8")
            fixed = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
            with patch.object(youtube, "DAILY_CACHE", cache), \
                 patch.object(youtube, "now_kst", return_value=fixed), \
                 patch.object(youtube, "env", return_value=""):
                result = youtube.collect({"유튜브": {"사용": True}})
        self.assertEqual(result["상태"], "당일캐시")
        self.assertEqual(result["급상승"][0]["영상ID"], "kept")

    def test_youtube_render_keeps_videos_with_grounded_fallback_overlap(self):
        raw = [{"영상ID": str(i), "제목": f"영상 {i}", "채널": "채널",
                "조회수": i, "링크": f"https://youtu.be/{i}"} for i in range(4)]
        notes = [{"영상ID": "0", "겹침": "보통", "겹침근거": "같은 연금 주제"}]
        out = render._youtube({"유튜브": {"급상승": raw}},
                              {"유튜브": notes, "top5": [{"제목": "금리 상승"}]})
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["겹침"], "보통")
        self.assertEqual(out[1]["겹침"], "낮음")

    def test_cached_youtube_analysis_survives_partial_ai_response(self):
        raw = [{"영상ID": "kept", "제목": "연금 ETF", "채널": "채널",
                "조회수": 10, "링크": "https://youtu.be/kept"}]
        data = {"유튜브": {"급상승": raw, "분석": [
            {"영상ID": "kept", "겹침": "높음", "겹침근거": "같은 ETF 주제"}]}}
        out = render._youtube(data, {"유튜브": []})
        self.assertEqual(out[0]["겹침"], "높음")

    def test_empty_ai_is_stabilized_into_complete_daily_brief(self):
        data = {
            "날짜표시": "2026년 8월 21일 (금)",
            "국내지수": [
                {"이름": "코스피", "종가": 2900, "등락률": 1.2},
                {"이름": "코스닥", "종가": 800, "등락률": -1.5}],
            "지표": {"해외지수": [
                {"이름": "S&P 500", "종가": 6000, "등락률": -0.7},
                {"이름": "나스닥 종합", "종가": 20000, "등락률": -1.0},
                {"이름": "다우 30", "종가": 45000, "등락률": -0.5}],
                "금리": [{"이름": "미 10년물", "종가": 4.5, "등락률": 0.2}]},
            "ETF_후보": {"거래량_급증": [{"이름": "테스트 ETF", "배수": 4.2}]},
            "뉴스": {}, "유튜브": {},
        }
        out = llm._stabilize_daily({}, {}, data, {"카카오": {}, "ETF_레이더": {}})
        self.assertEqual(len(out["top5"]), 5)
        self.assertEqual({x["시장"] for x in out["시장브리핑"]}, {"국내", "미국"})
        self.assertTrue(out["etf_레이더"])
        self.assertTrue(out["콘텐츠후보"])
        self.assertTrue(out["오늘의개념"])
        self.assertTrue(out["체크포인트"])
        self.assertEqual(render.validate_daily({"카카오": {}}, data, out), [])

    def test_complete_daily_contract_renders_every_required_section(self):
        data = {
            "날짜표시": "2026년 8월 21일 (금)", "기준일태그": "08/21",
            "국내지수": [{"이름": "코스피", "종가": 2900, "등락률": 1.2,
                            "기준일": "2026-08-21"},
                           {"이름": "코스닥", "종가": 800, "등락률": -1.5,
                            "기준일": "2026-08-21"}],
            "지표": {"해외지수": [
                {"이름": "S&P 500", "종가": 6000, "등락률": -0.7,
                 "기준일": "2026-08-20"},
                {"이름": "나스닥 종합", "종가": 20000, "등락률": -1.0,
                 "기준일": "2026-08-20"},
                {"이름": "다우 30", "종가": 45000, "등락률": -0.5,
                 "기준일": "2026-08-20"}]},
            "ETF_후보": {"거래량_급증": [{"이름": "테스트 ETF", "배수": 4.2}]},
            "뉴스": {}, "유튜브": {"급상승": [{"영상ID": "v1", "제목": "코스피 ETF",
                "채널": "경쟁 채널", "조회수": 100, "링크": "https://youtu.be/v1"}]},
        }
        ai = llm._stabilize_daily({}, {}, data, {"카카오": {}, "ETF_레이더": {}})
        with tempfile.TemporaryDirectory() as td, patch.object(render, "DOCS", Path(td)):
            path, _ = render.render({"브리핑": {}}, data, ai, "daily")
            html = path.read_text(encoding="utf-8")
        for heading in ("오늘 알아야 할 것", "시장 한눈에", "한·미 시장과 글로벌 변수",
                        "ETF 레이더", "경쟁 채널 동향", "ETF 아는형 콘텐츠 후보",
                        "오늘의 개념", "체크포인트 · 주요 일정", "출처"):
            self.assertIn(heading, html)
        self.assertIn("겹침", html)

    def test_intraday_fallback_updates_numbers_without_losing_cached_story(self):
        data = {"날짜표시": "2026년 8월 21일 (금)",
                "국내지수": [{"이름": "코스피", "종가": 3000, "등락률": -2.0},
                             {"이름": "코스닥", "종가": 790, "등락률": -3.0}],
                "지표": {"해외지수": [{"이름": "S&P 500", "종가": 6000,
                                          "등락률": -1.0},
                                         {"이름": "나스닥 종합", "종가": 20000,
                                          "등락률": -1.2},
                                         {"이름": "다우 30", "종가": 45000,
                                          "등락률": -0.8}]},
                "ETF_후보": {"거래량_급증": [{"이름": "ETF", "배수": 3.1}]},
                "뉴스": {}, "유튜브": {}}
        cached = {"시장브리핑": [{"시장": "국내", "제목": "아침 해설",
                                   "결과": "코스피 2900", "원인": "확인된 원인",
                                   "ETF연결": "ETF 연결", "출처": []}]}
        out = llm._stabilize_daily({}, cached, data, {"카카오": {}, "ETF_레이더": {}})
        kr = next(x for x in out["시장브리핑"] if x["시장"] == "국내")
        self.assertEqual(kr["제목"], "아침 해설")
        self.assertIn("코스피 -2.00%", kr["결과"])
        self.assertEqual(kr["원인"], "확인된 원인")

    def test_youtube_without_ai_note_gets_deterministic_overlap_tag(self):
        data = {"유튜브": {"급상승": [{"영상ID": "v1", "제목": "영상"}]}}
        ai = {"top5": [{"제목": str(i)} for i in range(5)],
              "시장브리핑": [{"시장": "국내"}, {"시장": "미국"}],
              "etf_레이더": [{"제목": "ETF"}], "콘텐츠후보": [{"제목": "기획"}],
              "오늘의개념": {"용어": "개념"}, "체크포인트": [{"내용": "확인"}],
              "카톡": {"1": "1. a\n2. b\n3. c\n4. d\n5. e"}}
        errors = render.validate_daily({}, data, ai)
        self.assertNotIn("경쟁 채널 겹침 분석 누락", errors)

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

    def test_both_markets_and_complete_kakao_items_are_always_present(self):
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
        self.assertTrue(all(len(x) <= 195 for x in out["카톡"].values()))

    def test_kakao_never_cuts_an_item_mid_sentence(self):
        raw = {"top5": [{"제목": f"핵심 이슈 {i}", "숫자": f"수치 {i}", "영향": ""}
                         for i in range(1, 6)],
               "오늘관전": ["외국인 수급과 금리 방향을 확인하세요"]}
        out = llm._postprocess(raw, {"카카오": {"글자수_제한": 195}, "ETF_레이더": {}},
                               {"날짜표시": "2026년 8월 21일 (금)", "뉴스": {}})
        self.assertIn("1. 핵심 이슈 1 | 수치 1", out["카톡"]["1"])
        self.assertIn("4. 핵심 이슈 4 | 수치 4", out["카톡"]["1"])
        self.assertIn("5. 핵심 이슈 5", out["카톡"]["1"])
        self.assertNotIn("…", out["카톡"]["1"])

    def test_filtered_top_items_are_renumbered_without_gap(self):
        raw = {"top5": [{"순위": 1, "제목": "첫째", "숫자": "1", "영향": ""},
                          {"순위": 4, "제목": "넷째", "숫자": "4", "영향": ""},
                          {"순위": 5, "제목": "다섯째", "숫자": "5", "영향": ""}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertEqual([x["순위"] for x in out["top5"]], [1, 2, 3])

    def test_top5_is_topped_up_from_verified_radar(self):
        raw = {"top5": [{"제목": f"시장 {i}", "숫자": str(i), "영향": ""}
                         for i in range(1, 5)],
               "etf_레이더": [{"제목": "ETF 제도 변화", "사실": "상관계수 기준 유지",
                               "관찰": "액티브 ETF 확인", "구분": "제도", "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertEqual(len(out["top5"]), 5)
        self.assertIn("5. ETF 제도 변화", out["카톡"]["1"])

    def test_daily_news_window_starts_at_previous_midnight(self):
        fixed = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
        with patch.object(news, "now_kst", return_value=fixed):
            hours, label = news.daily_window()
        self.assertEqual(hours, 31)
        self.assertIn("08/20 00:00", label)

    def test_unsourced_market_cause_is_hidden_not_replaced_with_notice(self):
        raw = {"시장브리핑": [{"시장": "국내", "제목": "국내 증시", "결과": "코스피 상승",
                                 "원인": "확인되지 않은 추정", "ETF연결": "코스피200 확인",
                                 "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        kr = next(b for b in out["시장브리핑"] if b["시장"] == "국내")
        self.assertEqual(kr["원인"], "")
        self.assertNotIn("근거", json.dumps(out, ensure_ascii=False))

    def test_current_day_intraday_is_kept_for_intraday_run(self):
        raw = {"top5": [{"제목": "코스닥 매도사이드카", "숫자": "8/21 장중 -4%", "영향": ""},
                        {"제목": "미 금리 상승", "숫자": "+4bp", "영향": "나스닥 부담"}]}
        data = {"날짜표시": "2026년 8월 21일 (금)", "뉴스": {}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual([x["제목"] for x in out["top5"]],
                         ["코스닥 매도사이드카", "미 금리 상승"])

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
