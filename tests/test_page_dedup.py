import unittest

from services.page_service import PageService


class FakePageRepo:
    def __init__(self, pages):
        self.pages = list(pages)

    def save_pages(self, token, pages):
        pass

    def load_cached_pages(self, token):
        return [p for p in self.pages if p.get("account_token") == token]

    def load_all_cached_pages(self):
        return list(self.pages)

    def delete_pages_for_token(self, token):
        pass


class FakeTokenRepo:
    def save_token_state(self, *args, **kwargs):
        pass

    def save_account_state(self, *args, **kwargs):
        pass

    def load_all_accounts(self):
        return []


class TestPageDedup(unittest.TestCase):
    def test_dedupe_three_pages_across_two_tokens(self):
        pages = [
            {"id": "1", "name": "A", "access_token": "t1", "last_updated": "2024-01-01T00:00:00"},
            {"id": "2", "name": "B", "access_token": "t1", "last_updated": "2024-01-01T00:00:00"},
            {"id": "3", "name": "C", "access_token": "t1", "last_updated": "2024-01-01T00:00:00"},
            {"id": "1", "name": "A", "access_token": "t2", "last_updated": "2024-06-01T00:00:00"},
            {"id": "2", "name": "B", "access_token": "t2", "last_updated": "2024-06-01T00:00:00"},
            {"id": "3", "name": "C", "access_token": "t2", "last_updated": "2024-06-01T00:00:00"},
        ]
        svc = PageService(page_repo=FakePageRepo(pages), token_repo=FakeTokenRepo())
        result = svc.load_all_from_cache()
        self.assertEqual(len(result), 3)
        self.assertEqual({p["id"] for p in result}, {"1", "2", "3"})
        self.assertTrue(all(p["access_token"] == "t2" for p in result))

    def test_dedupe_prefers_entry_with_access_token(self):
        pages = [
            {"id": "9", "name": "X", "access_token": "", "last_updated": "2024-09-01T00:00:00"},
            {"id": "9", "name": "X", "access_token": "tok", "last_updated": "2024-01-01T00:00:00"},
        ]
        svc = PageService(page_repo=FakePageRepo(pages), token_repo=FakeTokenRepo())
        result = svc.load_all_from_cache()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["access_token"], "tok")


if __name__ == "__main__":
    unittest.main()
