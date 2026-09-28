import unittest
from unittest import mock

from services.page_service import REQUIRED_PERMISSIONS, PageService


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRepo:
    def save_pages(self, token, pages):
        pass

    def load_cached_pages(self, token):
        return []

    def load_all_cached_pages(self):
        return []

    def save_token_state(self, *a, **k):
        pass

    def save_account_state(self, *a, **k):
        pass


class TestCheckPermissions(unittest.TestCase):
    def _service(self):
        return PageService(page_repo=FakeRepo(), token_repo=FakeRepo(), base_url="https://graph.facebook.com/v25.0")

    def test_reports_missing_permissions(self):
        service = self._service()
        payload = {"data": [
            {"permission": "pages_show_list", "status": "granted"},
            {"permission": "read_insights", "status": "granted"},
            {"permission": "business_management", "status": "declined"},
        ]}
        with mock.patch("services.page_service.requests.get", return_value=FakeResponse(200, payload)):
            result = service.check_permissions("token")
        self.assertTrue(result["ok"])
        self.assertIn("pages_show_list", result["granted"])
        self.assertIn("read_insights", result["granted"])
        self.assertNotIn("business_management", result["granted"])
        self.assertIn("business_management", result["missing"])
        self.assertIn("pages_manage_posts", result["missing"])

    def test_all_permissions_granted(self):
        service = self._service()
        payload = {"data": [{"permission": p, "status": "granted"} for p in REQUIRED_PERMISSIONS]}
        with mock.patch("services.page_service.requests.get", return_value=FakeResponse(200, payload)):
            result = service.check_permissions("token")
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])

    def test_invalid_token_returns_error(self):
        service = self._service()
        payload = {"error": {"message": "Invalid OAuth access token"}}
        with mock.patch("services.page_service.requests.get", return_value=FakeResponse(400, payload)):
            result = service.check_permissions("token")
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], list(REQUIRED_PERMISSIONS))


if __name__ == "__main__":
    unittest.main()
