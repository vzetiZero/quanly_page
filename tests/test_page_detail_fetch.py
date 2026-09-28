import unittest
from unittest import mock

from models.page_detail_model import PageDetailFetcher

INVALID_FIELDS = {"were_here_count", "overall_star_rating", "rating_count"}


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def fake_get(url, params=None, timeout=None):
    fields = (params or {}).get("fields", "")
    field_list = [f for f in str(fields).split(",") if f]
    if any(f in INVALID_FIELDS for f in field_list):
        return FakeResponse(400, {"error": {"message": "nonexisting field", "code": 100}})
    data = {"id": "1"}
    if "name" in field_list:
        data["name"] = "Page A"
    if "category" in field_list:
        data["category"] = "Business"
    if "followers_count" in field_list:
        data["followers_count"] = 1234
    if "fan_count" in field_list:
        data["fan_count"] = 1200
    return FakeResponse(200, data)


class TestPageDetailFetcher(unittest.TestCase):
    def test_fetch_fields_salvages_valid_fields_when_group_fails(self):
        fetcher = PageDetailFetcher("https://graph.facebook.com/v25.0")
        fields = ["name", "were_here_count", "followers_count"]
        with mock.patch("models.page_detail_model.requests.get", side_effect=fake_get):
            data, errors = fetcher._fetch_fields("1", "tok", fields)
        self.assertEqual(data.get("name"), "Page A")
        self.assertEqual(data.get("followers_count"), 1234)
        self.assertTrue(any(e.get("field") == "were_here_count" for e in errors), errors)

    def test_fetch_page_info_returns_counts(self):
        fetcher = PageDetailFetcher("https://graph.facebook.com/v25.0")
        with mock.patch("models.page_detail_model.requests.get", side_effect=fake_get):
            result = fetcher.fetch_page_info("1", "tok")
        self.assertEqual(result.get("followers_count"), 1234)
        self.assertEqual(result.get("fan_count"), 1200)
        self.assertEqual(result.get("name"), "Page A")

    def test_fetch_all_keeps_page_name_and_merges(self):
        fetcher = PageDetailFetcher("https://graph.facebook.com/v25.0")
        with mock.patch("models.page_detail_model.requests.get", side_effect=fake_get):
            result = fetcher.fetch_all("1", "My Page", "tok")
        self.assertEqual(result.get("page_id"), "1")
        self.assertEqual(result.get("page_name"), "My Page")
        self.assertEqual(result.get("followers_count"), 1234)


if __name__ == "__main__":
    unittest.main()
