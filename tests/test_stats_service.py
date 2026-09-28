import unittest
from unittest import mock

from services.stats_service import StatsService


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRepo:
    def __init__(self):
        self.saved = []

    def count_posted_videos_by_page(self):
        return {"Page A": 3}

    def load_all_page_stats(self):
        return []

    def load_posted_video_ids(self, page_key):
        return [{"post_id": "v1"}, {"post_id": "v2"}]

    def save_page_stats(self, page_key, page_name, video_count=0, video_views=None, followers_count=None, fan_count=None):
        self.saved.append({
            "page_key": page_key, "page_name": page_name, "video_count": video_count,
            "video_views": video_views, "followers_count": followers_count, "fan_count": fan_count,
        })


PAGE_INSIGHTS_OK = True
VIDEO_INSIGHTS_OK = True
VIDEOS = [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]


def fake_get(url, params=None, timeout=None):
    params = params or {}
    metric = str(params.get("metric", ""))
    if url.endswith("/video_reels"):
        return FakeResponse(200, {"data": [], "paging": {"cursors": {}}})
    if url.endswith("/videos"):
        return FakeResponse(200, {"data": VIDEOS, "paging": {"cursors": {}}})
    if "/video_insights" in url:
        if VIDEO_INSIGHTS_OK:
            return FakeResponse(200, {"data": [{"name": "total_video_views", "values": [{"value": 20}]}]})
        return FakeResponse(400, {"error": {"message": "no video insight"}})
    if url.endswith("/insights"):
        if "page_video_views" in metric and PAGE_INSIGHTS_OK:
            return FakeResponse(200, {"data": [{"name": "page_video_views", "values": [{"value": 50}]}]})
        return FakeResponse(400, {"error": {"message": "metric deprecated"}})
    return FakeResponse(200, {"followers_count": 100, "fan_count": 90})


class TestStatsService(unittest.TestCase):
    def setUp(self):
        global PAGE_INSIGHTS_OK, VIDEO_INSIGHTS_OK
        PAGE_INSIGHTS_OK = True
        VIDEO_INSIGHTS_OK = True

    def _service(self):
        return StatsService(stats_repo=FakeRepo(), base_url="https://graph.facebook.com/v25.0")

    def test_refresh_counts_page_videos_and_views(self):
        service = self._service()
        page = {"id": "1", "name": "Page A", "access_token": "tok"}
        with mock.patch("services.stats_service.requests.get", side_effect=fake_get):
            rows = service.refresh([page])
        self.assertEqual(rows[0]["page_video_count"], 3)
        self.assertEqual(rows[0]["app_video_count"], 3)
        self.assertEqual(rows[0]["video_views"], 60)  # 3 video x 20
        self.assertEqual(rows[0]["followers"], 100)
        self.assertEqual(rows[0]["fan_count"], 90)

    def test_page_videos_dedupes_repeated_ids(self):
        service = self._service()
        state = {"call": 0}

        def dedupe_get(url, params=None, timeout=None):
            params = params or {}
            if url.endswith("/video_reels"):
                return FakeResponse(200, {"data": [], "paging": {"cursors": {}}})
            if url.endswith("/videos"):
                state["call"] += 1
                if state["call"] == 1:
                    return FakeResponse(200, {"data": [{"id": "v1"}, {"id": "v2"}], "paging": {"cursors": {"after": "c1"}}})
                return FakeResponse(200, {"data": [{"id": "v1"}, {"id": "v2"}], "paging": {"cursors": {}}})
            if "/video_insights" in url:
                return FakeResponse(200, {"data": [{"name": "total_video_views", "values": [{"value": 20}]}]})
            return FakeResponse(200, {"followers_count": 1, "fan_count": 1})

        page = {"id": "1", "name": "Page A", "access_token": "tok"}
        with mock.patch("services.stats_service.requests.get", side_effect=dedupe_get):
            rows = service.refresh([page])
        self.assertEqual(rows[0]["page_video_count"], 2)

    def test_video_views_prefers_non_zero_metric(self):
        service = self._service()

        def metric_get(url, params=None, timeout=None):
            params = params or {}
            metric = str(params.get("metric", ""))
            if url.endswith("/video_reels"):
                return FakeResponse(200, {"data": [], "paging": {"cursors": {}}})
            if url.endswith("/videos"):
                return FakeResponse(200, {"data": [{"id": "v1"}], "paging": {"cursors": {}}})
            if "/video_insights" in url:
                value = 0 if metric == "total_video_views" else 27
                return FakeResponse(200, {"data": [{"name": metric, "values": [{"value": value}]}]})
            return FakeResponse(200, {"followers_count": 1, "fan_count": 1})

        page = {"id": "1", "name": "Page A", "access_token": "tok"}
        with mock.patch("services.stats_service.requests.get", side_effect=metric_get):
            rows = service.refresh([page])
        self.assertEqual(rows[0]["video_views"], 27)

    def test_refresh_falls_back_to_page_insights(self):
        global VIDEO_INSIGHTS_OK
        VIDEO_INSIGHTS_OK = False
        service = self._service()
        page = {"id": "1", "name": "Page A", "access_token": "tok"}
        with mock.patch("services.stats_service.requests.get", side_effect=fake_get):
            rows = service.refresh([page])
        self.assertEqual(rows[0]["video_views"], 50)

    def test_local_rows_without_network(self):
        service = self._service()
        rows = service.local_rows([{"id": "1", "name": "Page A"}])
        self.assertEqual(rows[0]["app_video_count"], 3)
        self.assertIsNone(rows[0]["video_views"])


if __name__ == "__main__":
    unittest.main()
