"""Test danh sách video của page (link, view, lọc, sắp xếp, đánh số) + số liệu 1 page."""
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

    def save_page_stats(self, page_key, page_name, video_count=0, video_views=None,
                        followers_count=None, fan_count=None):
        self.saved.append({
            "page_key": page_key, "page_name": page_name, "video_count": video_count,
            "video_views": video_views, "followers_count": followers_count, "fan_count": fan_count,
        })


REELS = [
    {"id": "r1", "description": "Video moi nhat", "created_time": "2026-03-03T10:00:00+0000",
     "permalink_url": "https://facebook.com/reel/r1"},
    {"id": "r2", "description": "Video giua", "created_time": "2026-02-02T10:00:00+0000"},
    {"id": "r3", "description": "Reels trung ten", "created_time": "2026-01-01T10:00:00+0000",
     "permalink_url": "https://facebook.com/reel/r3"},
]
# r2 xuất hiện ở cả reels và videos -> phải khử trùng.
VIDEOS = [
    {"id": "r2", "title": "Video giua (ban videos)", "created_time": "2026-02-02T10:00:00+0000"},
    {"id": "v9", "title": "Video thuong", "created_time": "2025-12-31T10:00:00+0000"},
]
VIEWS = {"r1": 500, "r2": 30, "r3": 90}


def make_get(rich_fields_ok=True):
    def fake_get(url, params=None, timeout=None):
        params = params or {}
        fields = str(params.get("fields", ""))
        if url.endswith("/video_reels"):
            if not rich_fields_ok and fields != "id":
                return FakeResponse(400, {"error": {"message": "field khong hop le"}})
            return FakeResponse(200, {"data": REELS, "paging": {"cursors": {}}})
        if url.endswith("/videos"):
            if not rich_fields_ok and fields != "id":
                return FakeResponse(400, {"error": {"message": "field khong hop le"}})
            return FakeResponse(200, {"data": VIDEOS, "paging": {"cursors": {}}})
        if "/video_insights" in url:
            video_id = url.split("/")[4]
            return FakeResponse(200, {"data": [{"name": "total_video_views",
                                                "values": [{"value": VIEWS.get(video_id, 0)}]}]})
        return FakeResponse(200, {"followers_count": 1234, "fan_count": 1200})
    return fake_get


class TestFetchPageVideos(unittest.TestCase):
    def _service(self):
        return StatsService(stats_repo=FakeRepo(), base_url="https://graph.facebook.com/v25.0")

    def test_lists_videos_newest_first_with_link_and_views(self):
        service = self._service()
        with mock.patch("services.stats_service.requests.get", side_effect=make_get()):
            videos = service.fetch_page_videos("1", "tok")

        self.assertEqual([v["id"] for v in videos], ["r1", "r2", "r3", "v9"])
        self.assertEqual(videos[0]["link"], "https://facebook.com/reel/r1")
        self.assertEqual(videos[0]["views"], 500)
        # Không có permalink_url -> tự dựng link reel.
        self.assertEqual(videos[1]["link"], "https://www.facebook.com/reel/r2")
        self.assertEqual(videos[1]["views"], 30)

    def test_dedupes_same_id_across_edges(self):
        service = self._service()
        with mock.patch("services.stats_service.requests.get", side_effect=make_get()):
            videos = service.fetch_page_videos("1", "tok")
        ids = [v["id"] for v in videos]
        self.assertEqual(len(ids), len(set(ids)), f"video trung id: {ids}")

    def test_falls_back_to_id_only_request(self):
        """Meta từ chối field phức tạp -> vẫn lấy được danh sách video."""
        service = self._service()
        with mock.patch("services.stats_service.requests.get", side_effect=make_get(rich_fields_ok=False)):
            videos = service.fetch_page_videos("1", "tok")
        self.assertEqual([v["id"] for v in videos], ["r1", "r2", "r3", "v9"])
        self.assertTrue(all(v["link"] for v in videos))

    def test_no_token_returns_empty(self):
        service = self._service()
        self.assertEqual(service.fetch_page_videos("1", ""), [])
        self.assertEqual(service.fetch_page_videos("", "tok"), [])

    def test_respects_max_videos(self):
        service = self._service()
        with mock.patch("services.stats_service.requests.get", side_effect=make_get()):
            videos = service.fetch_page_videos("1", "tok", max_videos=2)
        self.assertEqual(len(videos), 2)
        self.assertEqual([v["id"] for v in videos], ["r1", "r2"])

    def test_refresh_page_returns_row_and_saves(self):
        service = self._service()
        with mock.patch("services.stats_service.requests.get", side_effect=make_get()):
            row = service.refresh_page({"id": "1", "name": "Page A", "access_token": "tok"})
        self.assertEqual(row["page"], "Page A")
        self.assertEqual(row["page_id"], "1")
        self.assertEqual(row["page_video_count"], 4)
        self.assertEqual(row["followers"], 1234)
        self.assertEqual(row["fan_count"], 1200)
        self.assertEqual(row["video_views"], 500 + 30 + 90 + 0)
        self.assertEqual(row["app_video_count"], 3)
        self.assertTrue(service._repo.saved)

    def test_refresh_page_without_token_marks_error(self):
        service = self._service()
        row = service.refresh_page({"id": "1", "name": "Page A", "access_token": ""})
        self.assertEqual(row["status"], "error")
        self.assertIsNone(row["video_views"])

    def test_stat_map_keyed_by_page_name(self):
        class Repo(FakeRepo):
            def load_all_page_stats(self):
                return [{"page_key": "Page A", "followers_count": 5, "video_views": 7, "updated_at": "2026-01-01T00:00:00"}]

        service = StatsService(stats_repo=Repo(), base_url="x")
        stat_map = service.stat_map()
        self.assertIn("Page A", stat_map)
        self.assertEqual(stat_map["Page A"]["followers_count"], 5)
        self.assertEqual(stat_map["Page A"]["video_views"], 7)


if __name__ == "__main__":
    unittest.main()
