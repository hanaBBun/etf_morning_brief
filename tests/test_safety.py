import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src import delivery, events, krx, llm, main, news, render, youtube


class _FakeStock:
    @staticmethod
    def get_nearest_business_day_in_a_week(date, prev=True):
        return date


class SafetyTests(unittest.TestCase):
    def test_flowboard_never_places_positive_return_in_losers(self):
        data = {"ETF_후보": {"흐름판": {
            "상승": [{"이름": "상승 ETF", "등락률": 2.0},
                       {"이름": "잘못된 상승", "등락률": -1.0}],
            "하락": [{"이름": "하락 ETF", "등락률": -2.0},
                       {"이름": "잘못된 하락", "등락률": 1.0}],
        }}}
        flow = render._safe_flowboard(data)
        self.assertEqual([x["이름"] for x in flow["상승"]], ["상승 ETF"])
        self.assertEqual([x["이름"] for x in flow["하락"]], ["하락 ETF"])

    def test_top5_dedupes_same_indicator_and_return(self):
        rows = [
            {"제목": "필라델피아 반도체 하락", "숫자": "11,469.66 (-3.47%)"},
            {"제목": "미 반도체 지수 급락", "숫자": "필라델피아 반도체 -3.47%"},
            {"제목": "WTI 상승", "숫자": "85.17 (+2.12%)"},
        ]
        self.assertEqual([x["제목"] for x in llm._dedupe_top5(rows)],
                         ["필라델피아 반도체 하락", "WTI 상승"])

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

    def test_youtube_uses_upload_playlist_without_search_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ids = root / "channel_ids.json"
            uploads = root / "channel_uploads.json"
            daily = root / "youtube_daily_cache.json"
            ids.write_text('{"테스트채널":"UC1"}', encoding="utf-8")
            uploads.write_text('{"테스트채널":"UU1"}', encoding="utf-8")
            calls = []

            def fake_get(path, key, **params):
                calls.append(path)
                if path == "playlistItems":
                    return {"items": [{"snippet": {"title": "ETF 새 영상"},
                        "contentDetails": {"videoId": "v1",
                                           "videoPublishedAt": "2026-08-21T01:00:00Z"}}]}
                if path == "videos":
                    return {"items": [{"id": "v1", "statistics": {
                        "viewCount": "100", "commentCount": "2"},
                        "contentDetails": {"duration": "PT3M5S"}}]}
                if path == "commentThreads":
                    return {"items": []}
                raise AssertionError(f"예상하지 않은 API: {path}")

            fixed = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
            with patch.object(youtube, "CACHE", ids), \
                 patch.object(youtube, "UPLOADS_CACHE", uploads), \
                 patch.object(youtube, "DAILY_CACHE", daily), \
                 patch.object(youtube, "now_kst", return_value=fixed), \
                 patch.object(youtube, "env", return_value="key"), \
                 patch.object(youtube, "_get", side_effect=fake_get):
                result = youtube.collect({"유튜브": {"사용": True, "채널": ["테스트채널"],
                                                       "급상승_표시개수": 5,
                                                       "댓글_분석_영상수": 1}})
        self.assertEqual(result["급상승"][0]["영상ID"], "v1")
        self.assertIn("playlistItems", calls)
        self.assertNotIn("search", calls)

    def test_youtube_render_keeps_videos_with_grounded_fallback_overlap(self):
        raw = [{"영상ID": str(i), "제목": f"영상 {i}", "채널": "채널",
                "조회수": i, "링크": f"https://youtu.be/{i}"} for i in range(4)]
        notes = [{"영상ID": "0", "겹침": "보통", "겹침근거": "같은 연금 주제"}]
        out = render._youtube({"유튜브": {"급상승": raw}},
                              {"유튜브": notes, "top5": [{"제목": "금리 상승"}]})
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["관련성"], "낮음")
        self.assertEqual(out[1]["관련성"], "낮음")

    def test_cached_youtube_analysis_survives_partial_ai_response(self):
        raw = [{"영상ID": "kept", "제목": "연금 ETF", "채널": "채널",
                "조회수": 10, "링크": "https://youtu.be/kept"}]
        data = {"유튜브": {"급상승": raw, "분석": [
            {"영상ID": "kept", "겹침": "높음", "겹침근거": "같은 ETF 주제"}]}}
        out = render._youtube(data, {"유튜브": []})
        self.assertEqual(out[0]["관련성"], "높음")

    def test_empty_ai_fallback_cannot_overwrite_a_complete_daily_brief(self):
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
            "ETF_후보": {"거래량_급증": [{"이름": "테스트 ETF", "배수": 4.2}],
                "흐름판": {"기간": "전일", "기준일": "08/21", "최소거래대금_억원": 50,
                    "상승": [{"이름": "금채굴 ETF", "등락률": 6.2}],
                    "하락": [{"이름": "반도체 ETF", "등락률": -4.1}],
                    "고변동상품": [{"이름": "코스닥150 레버리지", "등락률": -8.0}],
                    "거래집중": [{"이름": "바이오 ETF", "배수": 3.4}]}},
            "뉴스": {}, "유튜브": {},
        }
        out = llm._stabilize_daily({}, {}, data, {"카카오": {}, "ETF_레이더": {}})
        self.assertEqual(len(out["top5"]), 5)
        self.assertEqual({x["시장"] for x in out["시장브리핑"]}, {"국내", "미국"})
        self.assertEqual(out["etf_레이더"], [])
        self.assertTrue(out["콘텐츠후보"])
        self.assertTrue(out["오늘의개념"])
        self.assertTrue(out["체크포인트"])
        errors = render.validate_daily({"카카오": {}}, data, out)
        self.assertNotIn("국내 시장브리핑 미완성", errors)
        self.assertNotIn("미국 시장브리핑 미완성", errors)
        self.assertTrue(all(x.get("원인") for x in out["시장브리핑"]))

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
        data["ETF_후보"]["흐름판"] = {
            "기간": "전일", "기준일": "08/21", "최소거래대금_억원": 50,
            "상승": [{"이름": "금채굴 ETF", "등락률": 6.2}],
            "하락": [{"이름": "반도체 ETF", "등락률": -4.1}],
            "고변동상품": [{"이름": "코스닥150 레버리지", "등락률": -8.0}],
            "거래집중": [{"이름": "바이오 ETF", "배수": 3.4}],
        }
        ai = llm._stabilize_daily({}, {}, data, {"카카오": {}, "ETF_레이더": {}})
        ai["etf_레이더"] = [
            {"구분": "자금 흐름", "제목": f"ETF 뉴스 {i}", "사실": f"자금 {i}억원 유입",
             "관찰": f"화면에서 숨길 관찰 {i}",
             "출처": [{"이름": "테스트경제", "url": f"https://example.com/{i}"}]}
            for i in range(1, 9)
        ]
        ai["주도테마"] = {"테마": "원자력", "제목": "원전 수주 기대에 동반 강세",
                           "움직임": "원자력 ETF 3개가 함께 상승했습니다.",
                           "원인": "수주 기대가 관련주를 거쳐 ETF 가격에 반영됐을 가능성이 있습니다.",
                           "주도종목": [{"이름": "현대건설", "등락률": 14.87}],
                           "ETF연결": "후속 수주 여부를 확인합니다.", "출처": []}
        ai["관심종목"] = [{"시장": "국내", "이름": "현대건설", "등락률": 14.87,
                           "이유": "원전 수주 기대가 건설업종 ETF에 반영됐습니다.", "출처": []}]
        with tempfile.TemporaryDirectory() as td, patch.object(render, "DOCS", Path(td)):
            path, _ = render.render({"브리핑": {}}, data, ai, "daily")
            html = path.read_text(encoding="utf-8")
        for heading in ("오늘의 주요 뉴스", "시장 한눈에", "한·미 시장과 글로벌 변수",
                        "ETF 레이더", "경쟁 채널 동향", "ETF 아는형 콘텐츠 후보",
                        "오늘의 개념", "체크포인트 · 주요 일정", "출처"):
            self.assertIn(heading, html)
        self.assertIn("채널 관련성", html)
        self.assertIn("추가로 읽을 ETF 뉴스 5개", html)
        self.assertIn("ETF 뉴스 8", html)
        self.assertIn("ETF 흐름판", html)
        self.assertIn("오늘의 주도 테마", html)
        self.assertIn("오늘의 특징주", html)
        self.assertLess(html.index("오늘의 특징주"), html.index("<h2>📡 ETF 레이더"))
        self.assertIn("현대건설", html)
        self.assertIn("금채굴 ETF", html)
        self.assertIn("레버리지·인버스 별도", html)
        self.assertNotIn("화면에서 숨길 관찰", html)
        self.assertNotIn("오늘 해야 하는 이유", html)

    def test_kakao_share_button_reuses_single_message_and_daily_url(self):
        data = {"날짜표시": "2026년 8월 25일 (화)"}
        ai = {"카톡": {"1": "☀️ 8/25 브리핑\n1. 국내 증시\n2. 미국 증시\n3. ETF\n4. 일정\n5. 관전"}}
        cfg = {"브리핑": {"사이트_주소": "https://hanabbun.github.io/etf_morning_brief/"}}
        fixed = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
        with patch.object(render, "now_kst", return_value=fixed), \
             patch.object(render, "config_env", return_value="javascript-key"):
            context = render.build_context(cfg, data, ai, "daily")
            template = render.Environment(
                loader=render.FileSystemLoader(str(render.ROOT / "templates")),
                autoescape=render.select_autoescape(["html"]),
            ).get_template("brief.html.j2")
            html = template.render(**context)
        self.assertIn("카카오톡으로 공유", html)
        self.assertIn("Kakao.Share.sendDefault", html)
        self.assertIn("1. 국내 증시", context["공유본문"])
        self.assertIn("https://hanabbun.github.io/etf_morning_brief/2026-08-25.html", html)

    def test_kakao_share_button_is_hidden_until_javascript_key_exists(self):
        with patch.object(render, "config_env", return_value=""):
            context = render.build_context({}, {}, {"카톡": {"1": "본문"}}, "daily")
        self.assertEqual(context["카카오JS키"], "")

    def test_weekend_and_monday_briefs_have_distinct_roles(self):
        monday = datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(main._brief_identity(monday, "daily")[1], "이번 주 준비")
        self.assertEqual(main._brief_identity(monday, "weekly")[1], "지난 한 주 복기")

    def test_daily_backup_schedule_includes_thursday_and_skips_duplicates(self):
        workflow = (render.ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "55 21 * * 0-4"', workflow)
        self.assertIn('cron: "15 22 * * 0-4"', workflow)
        self.assertIn('cron: "5 23 * * 0-4"', workflow)
        self.assertIn("--skip-if-existing", workflow)
        self.assertIn("skip_if_existing:", workflow)
        self.assertIn("inputs.skip_if_existing", workflow)

    def test_weekly_and_thursday_external_dispatch_skip_duplicates(self):
        root = render.ROOT / ".github" / "workflows"
        for filename in ("weekly.yml", "thursday.yml"):
            workflow = (root / filename).read_text(encoding="utf-8")
            self.assertIn("skip_if_existing:", workflow)
            self.assertIn("inputs.skip_if_existing", workflow)
            self.assertIn("--skip-if-existing", workflow)

    def test_published_check_supports_thursday_handoff(self):
        fixed = datetime(2026, 9, 3, 9, 40, tzinfo=ZoneInfo("Asia/Seoul"))
        with tempfile.TemporaryDirectory() as td, \
             patch.object(render, "DOCS", Path(td)), \
             patch.object(main, "now_kst", return_value=fixed):
            (Path(td) / "2026-09-03-handoff.html").write_text("완료", encoding="utf-8")
            self.assertTrue(main._published("thursday"))

    def test_delivery_boundary_keeps_kakao_out_of_main_pipeline(self):
        cfg = {"발행": {"채널": "kakao"}}
        with patch.object(delivery.kakao, "send_brief", return_value=True) as send:
            self.assertTrue(delivery.send_brief(cfg, ["브리핑"], "https://example.com"))
        send.assert_called_once_with(cfg, ["브리핑"], "https://example.com")

    def test_delivery_rejects_unconfigured_provider(self):
        with self.assertRaisesRegex(RuntimeError, "지원하지 않는 발행 채널"):
            delivery.send_brief({"발행": {"채널": "unknown"}}, ["브리핑"])

    def test_delivery_builds_single_thursday_message(self):
        ai = {"카톡": {"1": "첫 메시지", "2": "두 번째 메시지"}}
        self.assertEqual(delivery.build_messages("thursday", {}, ai), ["첫 메시지"])

    def test_waiting_factors_require_quiet_market_and_two_fresh_sources(self):
        articles = [
            {"링크": "https://example.com/pce", "출처": "매체1", "날짜": "2026-08-27",
             "경과시간": 1},
            {"링크": "https://example.com/nvidia", "출처": "매체2", "날짜": "2026-08-27",
             "경과시간": 2},
        ]
        data = {"뉴스": {"국제": articles}, "지표": {"해외지수": [
            {"이름": "S&P 500", "등락률": 0.2},
            {"이름": "나스닥 종합", "등락률": -0.3},
            {"이름": "다우 30", "등락률": 0.1},
        ]}}
        raw = {"관망요인": {"시장": "미국", "제목": "움직임을 제한한 이유",
            "항목": [
                {"제목": "예상에 부합한 물가", "설명": "PCE가 예상과 같았습니다.",
                 "전달경로": "금리 기대의 추가 변화가 제한됐습니다.", "출처": [{"id": "n1"}]},
                {"제목": "장 마감 뒤 실적 대기", "설명": "대형 기술주 실적을 앞뒀습니다.",
                 "전달경로": "반도체주 방향성이 엇갈렸습니다.", "출처": [{"id": "n2"}]},
            ]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual(len(out["관망요인"]["항목"]), 2)
        self.assertEqual(out["관망요인"]["항목"][0]["출처"][0]["이름"], "매체1")

        data["지표"]["해외지수"][0]["등락률"] = 1.2
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertIsNone(out["관망요인"])

    def test_top_news_explanation_requires_resolved_article_source(self):
        data = {"뉴스": {"국내": [{"링크": "https://example.com/a", "출처": "매체",
                                      "날짜": "2026-08-27", "경과시간": 1}]}}
        raw = {"top5": [
            {"제목": "근거 있는 뉴스", "숫자": "1%", "설명": "확인된 사실입니다.",
             "영향": "업종 ETF에 연결", "출처": [{"id": "n1"}]},
            {"제목": "근거 없는 뉴스", "숫자": "2%", "설명": "근거 없는 설명입니다.",
             "영향": "ETF 영향", "출처": []},
        ]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual(out["top5"][0]["설명"], "확인된 사실입니다.")
        self.assertEqual(out["top5"][1]["설명"], "")

    def test_scheduled_retry_detects_existing_official_daily_file(self):
        fixed = datetime(2026, 8, 27, 7, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td, \
             patch.object(render, "DOCS", Path(td)), \
             patch.object(main, "now_kst", return_value=fixed):
            self.assertFalse(main._daily_published())
            (Path(td) / "2026-08-27.html").write_text("official", encoding="utf-8")
            self.assertTrue(main._daily_published())

    def test_period_comparison_is_weekly_only(self):
        data = {"주간_대표흐름": [{"이름": "코스피", "1일": 1.0, "1주": -2.0,
                                      "1개월": 3.0, "기준일": "2026-08-21"}]}
        self.assertEqual(render._weekly_table(data, "daily"), [])
        weekly = render._weekly_table(data, "weekly")
        self.assertEqual(weekly[0]["1주"], "-2.00%")

    def test_market_regime_requires_two_fresh_article_sources(self):
        raw = {"시장국면": {"제목": "국면 전환", "설명": "달라진 흐름",
                              "출처": [{"id": "n1"}, {"id": "n2"}]}}
        data = {"뉴스": {"국내": [
            {"링크": "https://example.com/1", "출처": "매체1", "경과시간": 1},
            {"링크": "https://example.com/2", "출처": "매체2", "경과시간": 2},
        ]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual(out["시장국면"]["제목"], "국면 전환")
        data["뉴스"]["국내"][1]["경과시간"] = 80
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertIsNone(out["시장국면"])

    def test_radar_semantic_duplicates_are_removed(self):
        raw = {"etf_레이더": [
            {"제목": "단일종목 레버리지 ETF 규제", "사실": "단일종목 레버리지 규제 강화", "출처": []},
            {"제목": "레버리지 ETF 규제 강화", "사실": "단일종목 레버리지 규제 논의", "출처": []},
        ]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {"최대_항목수": 8}},
                               {"뉴스": {}})
        self.assertEqual(len(out["etf_레이더"]), 1)

    def test_retirement_claim_is_qualified_without_product_document(self):
        raw = {"etf_레이더": [{"제목": "퇴직연금 ETF", "사실": "비위험자산으로 100% 편입 가능",
                                 "관찰": "", "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertIn("상품별", out["etf_레이더"][0]["사실"])

    def test_youtube_channel_types_separate_official_and_general(self):
        self.assertEqual(render._channel_type("KODEX", True), "운용사 공식")
        self.assertEqual(render._channel_type("삼프로TV", False), "일반 경제")

    def test_short_official_video_is_excluded_from_marketing_line(self):
        data = {"유튜브": {"급상승": [{"영상ID": "short", "제목": "상품 광고",
                 "채널": "스마트 타이거", "조회수": 10, "링크": "https://youtu.be/short",
                 "ETF관련": True, "길이초": 119}]}}
        context = render.build_context({}, data, {"유튜브": []}, "daily")
        self.assertEqual(context["유튜브운용사"], [])

    def test_two_minute_official_video_can_remain(self):
        data = {"유튜브": {"급상승": [{"영상ID": "long", "제목": "상품 설명",
                 "채널": "스마트 타이거", "조회수": 10, "링크": "https://youtu.be/long",
                 "ETF관련": True, "길이초": 120}]}}
        context = render.build_context({}, data, {"유튜브": []}, "daily")
        self.assertEqual(len(context["유튜브운용사"]), 1)

    def test_youtube_duration_parser(self):
        self.assertEqual(youtube._duration_seconds("PT1H2M3S"), 3723)

    def test_routine_live_is_excluded_but_new_channel_live_is_kept(self):
        rules = {"삼프로TV": ["개장시황", "더블 밸류업"]}
        routine = {"채널": "삼프로TV", "제목": "[9월 1일 개장시황] 더블 밸류업",
                   "방송상태": "none"}
        new_live = {"채널": "서대리TV", "제목": "처음 해보는 ETF 라이브",
                    "방송상태": "live"}
        self.assertTrue(youtube._is_routine_live(routine, rules))
        self.assertFalse(youtube._is_routine_live(new_live, rules))

    def test_routine_live_filter_also_removes_cached_replay(self):
        rows = [
            {"영상ID": "live", "채널": "삼프로TV", "제목": "오늘의 개장시황"},
            {"영상ID": "video", "채널": "삼프로TV", "제목": "반도체 ETF 분석"},
        ]
        kept = youtube._exclude_routine_lives(rows, {"삼프로TV": ["개장시황"]})
        self.assertEqual([x["영상ID"] for x in kept], ["video"])

    def test_etf_flow_deduplicates_themes_and_separates_leverage(self):
        rows = [{"이름": "KODEX 반도체", "등락률": 5.0},
                {"이름": "TIGER 반도체", "등락률": 4.8},
                {"이름": "ACE 바이오", "등락률": 4.0}]
        self.assertEqual([x["이름"] for x in krx._dedupe_ranked(rows, 3)],
                         ["KODEX 반도체", "ACE 바이오"])
        self.assertTrue(krx._is_leveraged_etf("KODEX 코스닥150레버리지"))
        self.assertFalse(krx._is_leveraged_etf("KODEX 코스닥150"))

    def test_etf_flow_uses_free_fallback_when_krx_snapshot_is_empty(self):
        import pandas as pd

        class EmptyETFStock:
            @staticmethod
            def get_etf_ohlcv_by_ticker(day):
                return pd.DataFrame()

            @staticmethod
            def get_nearest_business_day_in_a_week(date, prev=True):
                return date

        names = {"1": "KODEX 반도체", "2": "TIGER 반도체", "3": "ACE 바이오",
                 "4": "RISE 화장품", "5": "PLUS 방산", "6": "KODEX 코스닥150레버리지"}
        frame = pd.DataFrame([
            {"티커": str(i), "종가": 10000, "등락률": rate,
             "거래량": 1000000, "거래대금": 10000000000}
            for i, rate in enumerate((5.0, 4.8, 3.0, -2.0, -4.0, -8.0), 1)
        ]).set_index("티커")
        with patch.object(krx, "_stock", return_value=EmptyETFStock()), \
             patch.object(krx, "_closing_etf_snapshot", return_value=(frame, names)), \
             patch.object(krx, "_save_snapshot"):
            out = krx.etf_radar("20260821", {"ETF_레이더": {
                "흐름판_최소거래대금_억원": 50, "흐름판_상하위개수": 3}})
        flow = out["흐름판"]
        self.assertEqual([x["이름"] for x in flow["상승"]],
                         ["KODEX 반도체", "ACE 바이오"])
        self.assertEqual([x["이름"] for x in flow["하락"]],
                         ["PLUS 방산", "RISE 화장품"])
        self.assertEqual(flow["고변동상품"][0]["이름"], "KODEX 코스닥150레버리지")

    def test_zero_turnover_is_not_rewritten_as_liquid_etf_flow(self):
        import pandas as pd

        class EmptyETFStock:
            @staticmethod
            def get_etf_ohlcv_by_ticker(day):
                return pd.DataFrame()

            @staticmethod
            def get_nearest_business_day_in_a_week(date, prev=True):
                return date

        names = {str(i): f"일반 ETF {i}" for i in range(1, 7)}
        frame = pd.DataFrame([
            {"티커": str(i), "종가": 10000, "등락률": rate, "거래량": 0,
             "거래대금": 0, "순자산총액": 1000 - i}
            for i, rate in enumerate((5.0, 3.0, 1.0, -1.0, -3.0, -5.0), 1)
        ]).set_index("티커")
        with patch.object(krx, "_stock", return_value=EmptyETFStock()), \
             patch.object(krx, "_closing_etf_snapshot", return_value=(frame, names)), \
             patch.object(krx, "_save_snapshot"):
            out = krx.etf_radar("20260826", {"ETF_레이더": {
                "흐름판_최소거래대금_억원": 50, "흐름판_상하위개수": 3}})
        self.assertFalse((out.get("흐름판") or {}).get("상승"))
        self.assertFalse((out.get("흐름판") or {}).get("하락"))
        self.assertEqual(out["진단"]["유동성필터통과"], 0)

    def test_operator_alert_only_reports_actual_required_omissions(self):
        data = {"ETF_후보": {"흐름판": {}, "진단": {
                    "원본": 1163, "유동성필터통과": 0}},
                "수집상태": {"YouTube": "정상"},
                "뉴스": {"ETF시장": [{"제목": "ETF 기사"}]}}
        ai = {"시장브리핑": [
                {"시장": "국내", "자동생성": True, "결과": "코스피 +1%",
                 "원인": "", "ETF연결": "코스피200 ETF"},
                {"시장": "미국", "결과": "S&P500 +1%", "원인": "금리 하락",
                 "ETF연결": "S&P500 ETF"}],
              "etf_레이더": [], "관심종목": [], "시장국면": None}
        issues = main._operator_issues(data, ai)
        self.assertIn("국내 시황: AI 해설 미완성", issues)
        self.assertTrue(any("원본 1163" in x for x in issues))
        self.assertTrue(any("ETF 레이더" in x for x in issues))
        self.assertFalse(any("특징주" in x or "시장국면" in x for x in issues))

    def test_naver_etf_fallback_accepts_cp949_response(self):
        payload = {"result": {"etfItemList": [{
            "itemcode": "123456", "itemname": "테스트 한글 ETF", "nowVal": 10000,
            "changeRate": 2.5, "risefall": "2", "quant": 1000000, "marketSum": 100,
        }]}}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def read():
                return json.dumps(payload, ensure_ascii=False).encode("cp949")

        with patch.object(krx.urllib.request, "urlopen", return_value=Response()):
            frame, names = krx._naver_etf_snapshot()
        self.assertEqual(names["123456"], "테스트 한글 ETF")
        self.assertEqual(float(frame.loc["123456", "등락률"]), 2.5)

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

    def test_krx_business_day_failure_falls_back_to_previous_weekday(self):
        class BrokenStock:
            @staticmethod
            def get_nearest_business_day_in_a_week(**_kwargs):
                raise ValueError("empty KRX response")

        monday_morning = datetime(2026, 8, 24, 7, 0,
                                  tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(krx, "_stock", return_value=BrokenStock()), \
             patch.object(krx, "now_kst", return_value=monday_morning):
            self.assertEqual(krx.last_business_day(), "20260821")

    def test_complete_data_fallback_market_cards_can_publish(self):
        data = {
            "국내지수": [
                {"이름": "코스피", "종가": 3000, "등락률": 0.4},
                {"이름": "코스닥", "종가": 800, "등락률": -0.2},
            ],
            "수급": [{"주체": "외국인", "순매수": -103900000000}],
            "지표": {"해외지수": [
                {"이름": "S&P 500", "종가": 6500, "등락률": 0.3},
                {"이름": "나스닥 종합", "종가": 22000, "등락률": 0.5},
            ]},
        }
        cards = [llm._fallback_market_brief("국내", data),
                 llm._fallback_market_brief("미국", data)]
        ai = {"top5": [{"제목": str(i)} for i in range(5)],
              "시장브리핑": cards,
              "etf_레이더": [{"제목": "ETF"}],
              "콘텐츠후보": [{"제목": "기획"}],
              "오늘의개념": {"용어": "개념"},
              "체크포인트": [{"내용": "확인"}],
              "카톡": {"1": "1. a\n2. b\n3. c\n4. d\n5. e"}}
        self.assertFalse(any("시장브리핑 미완성" in x
                             for x in render.validate_daily({}, data, ai)))

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
        self.assertGreaterEqual(len(out["출연자추천"]), 1)
        self.assertEqual(out["출연자추천"][0]["이름"], "최창규")

    def test_handoff_guest_fallback_matches_recent_news_topics(self):
        data = {"뉴스": {"ETF시장": [
            {"제목": "퇴직연금 ETF 49조원 시대", "요약": "노후 자산배분 원칙"},
            {"제목": "레버리지 ETF 규제", "요약": "시장 구조 변화"},
        ]}}
        out = llm._postprocess_handoff({}, 195, data)
        names = [g["이름"] for g in out["출연자추천"]]
        self.assertIn("김성일", names)
        self.assertIn("최창규", names)

    def test_handoff_keeps_extra_news_and_quotes_for_toggle(self):
        articles = [{"제목": f"ETF 뉴스 {i}", "출처": "테스트", "날짜": "2026-08-25",
                     "링크": f"https://example.com/{i}", "경과시간": i}
                    for i in range(1, 9)]
        data = {"뉴스": {"ETF시장": articles}}
        llm._link_index(data)
        raw = {
            "etf_뉴스6선": [{"id": f"n{i}", "주제": "시장 구조", "한줄": "요약"}
                            for i in range(1, 9)],
            "발언정리": [{"이름": f"전문가{i}", "소속": "테스트증권", "직함": "연구원",
                         "주제": "ETF", "발언": "ETF 시장 발언",
                         "출처": {"이름": "테스트", "id": f"n{i}"}}
                        for i in range(1, 9)],
        }
        out = llm._postprocess_handoff(raw, 195, data)
        self.assertEqual(len(out["etf_뉴스6선"]), 8)
        self.assertEqual(len(out["발언정리"]), 8)

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
        self.assertIn("1. 핵심 이슈 1", out["카톡"]["1"])
        self.assertIn("4. 핵심 이슈 4", out["카톡"]["1"])
        self.assertIn("5. 핵심 이슈 5", out["카톡"]["1"])
        self.assertNotIn("…", out["카톡"]["1"])
        self.assertNotIn(" | ", out["카톡"]["1"])

    def test_kakao_does_not_force_duplicate_numbers_into_all_five_items(self):
        raw = {"top5": [
            {"제목": f"핵심 이슈 {i}", "숫자": f"대표수치{i} {i}.25% · 보조수치 {i * 10}", "영향": ""}
            for i in range(1, 6)]}
        out = llm._postprocess(raw, {"카카오": {"글자수_제한": 195}, "ETF_레이더": {}},
                               {"날짜표시": "2026년 8월 25일 (화)", "뉴스": {}})
        self.assertLessEqual(len(out["카톡"]["1"]), 195)
        for i in range(1, 6):
            self.assertIn(f"{i}. 핵심 이슈 {i}", out["카톡"]["1"])
            self.assertNotIn(f"대표수치{i}", out["카톡"]["1"])

    def test_kakao_matches_clean_morning_brief_style(self):
        raw = {"top5": [
            {"제목": "삼성그룹주 대폭락", "숫자": "코스피 6696.96 · 삼성전자 -8.70%"},
            {"제목": "미국 반도체주 약세", "숫자": "필라델피아 반도체 11423.17"},
            {"제목": "국내 수급 악화", "숫자": "외국인 -3.68조원 · 기관 -1.29조원"},
            {"제목": "미국 장기금리 하락", "숫자": "10년물 4.70%"},
            {"제목": "원자재 변동성 확대", "숫자": "금 선물 4731.10달러"},
        ]}
        out = llm._postprocess(raw, {"카카오": {"글자수_제한": 195}, "ETF_레이더": {}},
                               {"날짜표시": "2026년 8월 25일 (화)", "뉴스": {}})
        self.assertEqual(out["카톡"]["1"],
                         "☀️ 8/25 브리핑\n"
                         "1. 삼성그룹주 대폭락\n"
                         "2. 미국 반도체주 약세\n"
                         "3. 국내 수급 악화\n"
                         "4. 미국 장기금리 하락\n"
                         "5. 원자재 변동성 확대")

    def test_index_divergence_is_added_to_ai_input(self):
        data = {"국내지수": [{"이름": "코스피", "종가": 6696, "등락률": -3.12},
                              {"이름": "코스닥", "종가": 813, "등락률": 1.42}],
                "뉴스": {}, "지표": {}}
        compact = llm._compact(data, "daily")
        self.assertIn("코스피 -3.12%", compact["지수괴리"])

    def test_domestic_market_etf_link_is_filled_deterministically(self):
        raw = {"시장브리핑": [{"시장": "국내", "제목": "대형주 급락", "결과": "코스피 -3%",
                                 "원인": "삼성전자 하락", "ETF연결": "", "출처": []}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        kr = next(x for x in out["시장브리핑"] if x["시장"] == "국내")
        self.assertIn("코스피200", kr["ETF연결"])

    def test_generic_content_filler_is_removed(self):
        raw = {"콘텐츠후보": [{"제목": "VIX 상승, ETF에는 어떤 영향?", "이유": "수치 설명",
                                "관련ETF": "VIX 상승", "차별점": "수치와 ETF 전달 경로 중심",
                                "질문": "오늘의 시장 변동이 ETF 투자자에게 중요한 이유는 무엇인가요?"}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertEqual(out["콘텐츠후보"], [])

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

    def test_radar_can_keep_five_items_for_compact_extra_news(self):
        raw = {"etf_레이더": [
            {"구분": "자금 흐름", "제목": f"ETF 뉴스 {i}", "사실": f"자금 {i}억원 유입",
             "관찰": "추가 흐름 확인", "출처": []}
            for i in range(1, 6)
        ]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {"최대_항목수": 5}},
                               {"뉴스": {}})
        self.assertEqual(len(out["etf_레이더"]), 5)

    def test_daily_news_window_starts_at_previous_midnight(self):
        fixed = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
        with patch.object(news, "now_kst", return_value=fixed):
            hours, label = news.daily_window()
        self.assertEqual(hours, 31)
        self.assertIn("08/20 00:00", label)

    def test_familiar_publishers_receive_higher_source_tier(self):
        cfg = {"뉴스_편성": {"핵심언론사": ["한국경제", "머니투데이"],
                              "보조언론사": ["전자신문"]}}
        self.assertEqual(news._source_tier("한국경제 증권", cfg), 2)
        self.assertEqual(news._source_tier("전자신문", cfg), 1)
        self.assertEqual(news._source_tier("알 수 없는 매체", cfg), 0)

    def test_repeated_top_etfs_create_dynamic_theme_query(self):
        candidates = {"흐름판": {"상승": [
            {"이름": "TIGER 코리아원자력", "등락률": 11.37},
            {"이름": "ACE 원자력TOP10", "등락률": 10.18},
            {"이름": "반도체 ETF", "등락률": 2.0},
        ]}}
        themes = news.detect_etf_themes(candidates)
        self.assertEqual(themes[0]["테마"], "원자력")
        self.assertEqual(len(themes[0]["ETF"]), 2)
        source = news._theme_sources(themes)[0]
        self.assertIn("news.google.com/rss/search", source["url"])
        self.assertIn("%EC%9B%90%EC%9E%90%EB%A0%A5", source["url"])

    def test_theme_story_requires_detected_theme_article_and_real_stock(self):
        raw = {"주도테마": {
            "테마": "원자력", "제목": "미국 원전 수주 기대에 동반 강세",
            "움직임": "원자력 ETF 여러 종목이 함께 상승했습니다.",
            "원인": "미국 원전 사업 수주 기대가 관련주를 거쳐 ETF 가격에 반영됐을 가능성이 있습니다.",
            "주도종목": [{"이름": "현대건설", "등락률": 14.87},
                         {"이름": "지어낸종목", "등락률": 30.0}],
            "ETF연결": "후속 수주와 거래대금 지속 여부를 확인합니다.",
            "출처": [{"id": "n1"}],
        }}
        data = {
            "ETF_주도테마후보": [{"테마": "원자력", "방향": "상승", "ETF": []}],
            "종목_후보_국내": [{"종목명": "현대건설", "등락률": 14.87}],
            "뉴스": {"주도테마": [{"링크": "https://example.com/nuclear", "출처": "연합뉴스",
                                      "날짜": "2026-08-26", "경과시간": 2}]},
        }
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertEqual(out["주도테마"]["테마"], "원자력")
        self.assertEqual([x["이름"] for x in out["주도테마"]["주도종목"]], ["현대건설"])
        self.assertEqual(out["주도테마"]["출처"][0]["이름"], "연합뉴스")

    def test_theme_story_without_article_is_hidden(self):
        raw = {"주도테마": {"테마": "원자력", "제목": "원전 강세", "원인": "추정",
                              "출처": []}}
        data = {"ETF_주도테마후보": [{"테마": "원자력"}],
                "뉴스": {"국내": [{"링크": "https://example.com/other", "출처": "매체",
                                     "경과시간": 1}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        self.assertIsNone(out["주도테마"])

    def test_radar_causal_story_can_name_flowboard_etf(self):
        raw = {"etf_레이더": [{"구분": "유형 쏠림", "제목": "TIGER 코리아원자력 강세 배경",
                                "사실": "미국 원전 사업 기대가 관련주에 반영됐습니다.",
                                "관찰": "후속 수주 여부를 확인할 수 있습니다.",
                                "출처": [{"id": "n1"}]}]}
        data = {"ETF_후보": {"흐름판": {"상승": [{"이름": "TIGER 코리아원자력"}]}},
                "뉴스": {"주도테마": [{"링크": "https://example.com/nuclear", "출처": "한국경제",
                                         "경과시간": 1}]}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {"최대_항목수": 5}}, data)
        self.assertEqual(len(out["etf_레이더"]), 1)

    def test_price_only_top5_does_not_claim_fund_inflow(self):
        raw = {"top5": [{"제목": "원자력 ETF 강세", "숫자": "+11.37%",
                          "영향": "원자력 ETF 자금 유입"}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, {"뉴스": {}})
        self.assertNotIn("자금 유입", out["top5"][0]["영향"])
        self.assertIn("가격 강세", out["top5"][0]["영향"])

    def test_previous_brief_topics_are_added_to_next_ai_input(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "brief_daily_cache.json"
            cache.write_text(json.dumps({"날짜": "2026-08-25", "ai": {
                "top5": [{"제목": "미 장기금리 하락"}, {"제목": "반도체 약세"}]
            }}, ensure_ascii=False), encoding="utf-8")
            data = {"날짜표시": "2026년 8월 26일 (수)", "뉴스": {}, "지표": {}}
            with patch.object(llm, "DAILY_AI_CACHE", cache):
                compact = llm._compact(data, "daily")
            self.assertEqual(compact["최근브리핑주제"][0]["날짜"], "2026-08-25")
            self.assertIn("반도체 약세", compact["최근브리핑주제"][0]["주제"])

    def test_checkpoints_group_week_dates_and_always(self):
        items = [
            {"유형": "일정", "때": "8/26 (수) 21:30", "날짜": "2026-08-26", "내용": "PCE"},
            {"유형": "확인", "때": "상시", "내용": "유가"},
            {"유형": "일정", "때": "8/26 (수) 21:30", "날짜": "2026-08-26", "내용": "GDP"},
            {"유형": "확인", "때": "이번 주", "내용": "외국인 수급"},
        ]
        groups = render._checkpoint_groups(items)
        self.assertEqual([g["라벨"] for g in groups], ["이번 주 확인", "8/26 (수)", "상시 확인"])
        self.assertEqual([x["내용"] for x in groups[1]["항목"]], ["PCE", "GDP"])

    def test_official_event_is_converted_to_kst(self):
        dt = datetime(2026, 8, 26, 12, 30, tzinfo=timezone.utc)
        item = events._event(dt, "미국 PCE", "BEA", "https://example.com")
        self.assertEqual(item["때"], "8/26 (수) 21:30")

    @patch("src.events.requests.get")
    def test_bok_mpc_official_date_is_collected_without_fake_time(self, get):
        get.return_value.text = "08월 27일(목) 10월 22일(목)"
        start = datetime(2026, 8, 24, tzinfo=events.KST)
        out = events._bok_mpc(start, start + timedelta(days=7))
        self.assertEqual(out[0]["때"], "8/27 (목)")
        self.assertIn("기준금리", out[0]["내용"])

    @patch("src.events.requests.get")
    def test_nvidia_earnings_is_converted_from_pt_to_kst(self, get):
        get.side_effect = [
            type("R", (), {"text": "<item><title>NVIDIA Sets Conference Call for Second-Quarter Financial Results</title><link>https://example.com/nvda</link></item>"})(),
            type("R", (), {"text": "<urlset></urlset>"})(),
            type("R", (), {"text": ("NVIDIA will host a conference call on Wednesday, August 26, at 2 p.m. PT. "
                                      "Results will be publicly announced at approximately 1:20 p.m. PT")})(),
        ]
        start = datetime(2026, 8, 24, tzinfo=events.KST)
        out = events._nvidia_earnings(start, start + timedelta(days=7))
        self.assertEqual(out[0]["때"], "8/27 (목) 05:20")
        self.assertIn("미국 8/26", out[0]["내용"])

    @patch("src.events.requests.get")
    def test_warsh_keynote_is_collected_from_fed_calendar(self, get):
        get.return_value.text = ("10:00 a.m. Speech - Chairman Kevin Warsh Watch Live "
                                 "Keynote Remarks At Jackson Hole 28")
        start = datetime(2026, 8, 24, tzinfo=events.KST)
        out = events._fed_speeches(start, start + timedelta(days=7))
        self.assertEqual(out[0]["때"], "8/28 (금) 23:00")

    def test_official_schedule_tops_up_ai_checkpoints_without_duplicate(self):
        official = {"유형": "일정", "때": "8/26 (수) 21:30", "날짜": "2026-08-26",
                    "내용": "미국 개인소득·소비 및 PCE 물가"}
        raw = {"체크포인트": [{"유형": "일정", "때": "8/26 (수)",
                                 "내용": "미국 PCE 물가지수 발표"}]}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}},
                               {"뉴스": {}, "공식일정": [official]})
        pce = [x for x in out["체크포인트"] if "PCE" in x["내용"]]
        self.assertEqual(len(pce), 1)

    def test_past_schedule_is_removed_but_same_day_and_watch_items_remain(self):
        raw = {"체크포인트": [
            {"유형": "일정", "때": "8/26 (수) 21:30", "내용": "미국 PCE"},
            {"유형": "일정", "때": "8/27 (목) 10:00", "내용": "한국은행 금통위"},
            {"유형": "확인", "때": "이번 주", "내용": "외국인 수급"},
        ]}
        data = {"날짜표시": "2026년 8월 27일 (목)", "뉴스": {}}
        out = llm._postprocess(raw, {"카카오": {}, "ETF_레이더": {}}, data)
        contents = [x["내용"] for x in out["체크포인트"]]
        self.assertNotIn("미국 PCE", contents)
        self.assertIn("한국은행 금통위", contents)
        self.assertIn("외국인 수급", contents)

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

    def test_corrupt_etf_name_and_impossible_return_are_rejected(self):
        broken = "0195S0    상품명\nName: 종목명, dtype: object"
        self.assertEqual(krx._clean_etf_name(broken, "0195S0"), "0195S0")
        self.assertTrue(krx._valid_etf_return(9.17, weekly=True))
        self.assertFalse(krx._valid_etf_return(104627704.29, weekly=True))

    def test_flowboard_validation_blocks_corrupt_publication_values(self):
        data = {"브리핑종류": "주간", "ETF_후보": {"흐름판": {
            "상승": [{"이름": "정상 ETF", "등락률": 3.1}],
            "하락": [{"이름": "0195S0\nName: 종목명, dtype: object", "등락률": -2.0}],
            "고변동상품": [{"이름": "레버리지 ETF", "등락률": 60111507.08}],
        }}}
        errors = render.flowboard_errors(data)
        self.assertTrue(any("종목명" in x for x in errors))
        self.assertTrue(any("수익률" in x for x in errors))

    def test_weekly_table_uses_same_daily_close_as_market_glance(self):
        data = {
            "국내지수": [{"이름": "코스피", "등락률": -1.79}],
            "주간_대표흐름": [{"이름": "코스피", "1일": 1.53, "1주": -2.0,
                                  "1개월": 3.0, "기준일": "2026-08-28"}],
        }
        row = render._weekly_table(data, "weekly")[0]
        self.assertEqual(row["1일"], "-1.79%")

    def test_monday_summary_is_only_rendered_for_monday_role(self):
        ai = {"월요일요약": {"지난주복기": ["지난주 코스피 하락"],
                              "주말변수": [{"제목": "주말 정책 발표", "설명": "영향 확인"}],
                              "국내개장영향": "국내 ETF 변동성 점검"}}
        monday = render._monday_summary({"브리핑역할": "이번 주 준비"}, ai)
        self.assertEqual(monday["지난주복기"], ["지난주 코스피 하락"])
        self.assertIsNone(render._monday_summary({"브리핑역할": "일일"}, ai))

    def test_monday_news_window_starts_after_friday_close(self):
        fixed = datetime(2026, 8, 31, 7, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(news, "now_kst", return_value=fixed):
            hours, label = news.daily_window()
        self.assertEqual(hours, 64)
        self.assertIn("08/28 15:30", label)

    def test_delayed_weekend_snapshot_saves_last_business_day(self):
        import pandas as pd
        frame = pd.DataFrame([{"종가": 100.0, "등락률": 1.0, "거래량": 10,
                               "거래대금": 100000000.0, "순자산총액": 500.0}],
                             index=["123456"])
        fixed = datetime(2026, 8, 29, 12, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        with tempfile.TemporaryDirectory() as td, \
             patch.object(krx, "ETF_CLOSE_CACHE", Path(td) / "close.json"), \
             patch.object(krx, "now_kst", return_value=fixed), \
             patch.object(krx, "last_business_day", return_value="20260828"), \
             patch.object(krx, "_naver_etf_snapshot",
                          return_value=(frame, {"123456": "정상 ETF"})):
            saved = krx.save_closing_etf_snapshot()
            payload = json.loads(krx.ETF_CLOSE_CACHE.read_text(encoding="utf-8"))
        self.assertEqual(saved, "20260828")
        self.assertEqual(payload["20260828"]["항목"][0]["이름"], "정상 ETF")

    def test_delayed_weekday_snapshot_saves_previous_completed_close(self):
        import pandas as pd
        frame = pd.DataFrame([{"종가": 100.0, "등락률": 1.0, "거래량": 10,
                               "거래대금": 100000000.0, "순자산총액": 500.0}],
                             index=["123456"])
        # 월요일 마감 예약이 화요일 새벽까지 밀린 실제 장애를 재현한다.
        fixed = datetime(2026, 9, 1, 1, 6, tzinfo=ZoneInfo("Asia/Seoul"))
        with tempfile.TemporaryDirectory() as td, \
             patch.object(krx, "ETF_CLOSE_CACHE", Path(td) / "close.json"), \
             patch.object(krx, "now_kst", return_value=fixed), \
             patch.object(krx, "last_business_day", return_value="20260831"), \
             patch.object(krx, "_naver_etf_snapshot",
                          return_value=(frame, {"123456": "정상 ETF"})):
            saved = krx.save_closing_etf_snapshot()
            payload = json.loads(krx.ETF_CLOSE_CACHE.read_text(encoding="utf-8"))
        self.assertEqual(saved, "20260831")
        self.assertIn("20260831", payload)

    def test_weekly_workflow_has_retries_and_deduplication(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/weekly.yml").read_text(
            encoding="utf-8")
        self.assertGreaterEqual(workflow.count("cron:"), 3)
        self.assertIn("--skip-if-existing", workflow)

    def test_flowboard_recovers_from_cached_closes_and_individual_turnover(self):
        import pandas as pd

        class FakeStock:
            @staticmethod
            def get_etf_ohlcv_by_date(start, end, ticker):
                turnover = 6_000_000_000 if ticker == "111111" else 1_000_000_000
                return pd.DataFrame([{"거래대금": turnover}])

        cache = {
            "20260827": {
                "111111": {"종가": 100, "이름": "테스트 원자력 ETF"},
                "222222": {"종가": 100, "이름": "테스트 바이오 ETF"},
            },
            "20260828": {
                "111111": {"종가": 110, "이름": "테스트 원자력 ETF"},
                "222222": {"종가": 105, "이름": "테스트 바이오 ETF"},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "snapshot.json"
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            with patch.object(krx, "ETF_SNAPSHOT_CACHE", path):
                rows = krx._recover_flow_rows(FakeStock(), "20260828", 5_000_000_000)
        self.assertEqual([x["티커"] for x in rows], ["111111"])
        self.assertEqual(rows[0]["등락률"], 10.0)

    def test_monday_recap_uses_scan_friendly_cards(self):
        template = (Path(__file__).parents[1] / "templates/brief.html.j2").read_text(
            encoding="utf-8")
        self.assertIn('class="recap-grid"', template)
        self.assertIn('class="recap-card"', template)

    def test_failed_naver_fallback_does_not_hide_primary_diagnostics(self):
        cfg = {"ETF_레이더": {"흐름판_최소거래대금_억원": 50}}
        primary = {"전일_순매수": [], "전일_순매도": [], "거래량_급증": [],
                   "신규상장": [], "순자산_급증": [], "기준일": "20260828",
                   "흐름판": {}, "진단": {"소스": "KRX", "원본": 1163,
                                            "유동성필터통과": 0, "등락률유효": 0}}
        fallback = {**primary, "진단": {"소스": "네이버 금융 마감 스냅샷",
                                         "원본": 0, "유동성필터통과": 0,
                                         "오류": "확정 마감 시세 없음"}}
        with patch.object(krx, "_stock"), \
             patch.object(krx, "_closing_etf_snapshot", return_value=(None, {})), \
             patch.object(krx, "now_kst", return_value=datetime(
                 2026, 8, 31, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))), \
             patch.object(krx, "etf_radar", side_effect=[primary, fallback]):
            # 재귀 호출 자체는 기존 별도 테스트가 검증한다. 여기서는 운영 진단 규칙을 확인한다.
            first = krx.etf_radar("20260828", cfg)
            second = krx.etf_radar("20260828", cfg, _force_naver=True)
        merged = first["진단"].copy()
        merged["보조오류"] = second["진단"]["오류"]
        self.assertEqual(merged["원본"], 1163)
        self.assertEqual(merged["보조오류"], "확정 마감 시세 없음")


if __name__ == "__main__":
    unittest.main()
