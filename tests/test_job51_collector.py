import json
from unittest import TestCase

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.job51 import Job51Browser, Job51Collector, get_51job_city_code
from bosshunter.collection.text import clean_job_description


class Job51CollectorTests(TestCase):
    def test_city_and_option_defaults_are_fail_closed(self):
        self.assertEqual(get_51job_city_code("上海市"), "020000")
        self.assertIsNone(get_51job_city_code("北京"))
        options = normalize_collection_options({}, {
            "platform_order": ["51job"],
            "platforms": {"51job": {"keywords": ["AI 产品"], "cities": ["上海"]}},
        })
        search = options["platforms"]["51job"]
        self.assertEqual(search["city_codes"], {"上海": "020000"})
        self.assertEqual(search["max_pages"], 1)
        self.assertEqual(search["target_count"], 3)

    def test_collection_uses_platform_identity_and_rate_limit(self):
        list_payload = json.dumps({"status": "ready", "jobs": [
            {
                "source_job_id": "job-1",
                "title": "AI 产品经理",
                "company": "示例公司",
                "city": "上海",
                "url": "https://jobs.51job.com/shanghai/job-1.html",
            },
            {
                "source_job_id": "job-2",
                "title": "AI 产品运营",
                "company": "示例公司",
                "city": "上海",
                "url": "https://jobs.51job.com/shanghai/job-2.html",
            },
        ]}, ensure_ascii=False)
        detail_payload = json.dumps({
            "status": "ready",
            "title": "AI 产品",
            "company": "示例公司",
            "city": "上海",
            "jd": "[岗位kanzhun职责]负责需求分析，来自BOSS直聘要求会 SQL。",
        }, ensure_ascii=False)
        sleeps: list[float] = []

        def evaluate(_target, script):
            return list_payload if ".joblist-item" in script else detail_payload

        browser = Job51Browser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or len(collected) < 2,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )
        result = Job51Collector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("51job", ["AI 产品"], ["上海"], {"上海": "020000"}, max_pages=1, target_count=2),
            hooks,
        )

        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual([candidate.storage_id for candidate in collected], ["51job:job-1", "51job:job-2"])
        self.assertEqual(sleeps, [13.0])
        self.assertEqual(clean_job_description(collected[0].jd), "负责需求分析，要求会 SQL。")

    def test_verification_page_stops_platform(self):
        browser = Job51Browser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=lambda _target, _script: json.dumps({"status": "blocked", "jobs": []}),
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = Job51Collector(browser=browser).collect(
            PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "rate_limit")


class JobDescriptionCleanupTests(TestCase):
    def test_known_platform_source_noise_is_removed(self):
        dirty = "[岗位kanzhun职责]1.公司业务后台开发 来自BOSS直聘 2.掌握 SQL"
        self.assertEqual(clean_job_description(dirty), "1.公司业务后台开发 2.掌握 SQL")
