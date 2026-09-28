"""Test luồng đăng hàng đợi: chạy song song thật sự, khớp page theo id, báo lỗi đầy đủ."""
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from services.post_service import PostService


class _FakeRepo:
    def __init__(self):
        self.posted = []

    def load_posted_video_names_for_page(self, page_key):
        return set()

    def record_page_video(self, page_key, page_id, video_path, post_id, permalink):
        self.posted.append((page_key, page_id, video_path, post_id, permalink))

    def log_successful_post(self, page_name, message, media_type):
        pass


class _FakeUploader:
    def get_permalink(self, page, upload_id, proxy_config=None):
        return f"https://www.facebook.com/reel/{upload_id}"


class _FakeProxy:
    def fetch_proxy(self):
        return None

    def build_proxy_config(self, proxy_info):
        return None


def _make_service():
    return PostService(_FakeRepo(), _FakeUploader(), _FakeProxy())


def _rows(n, with_video=True):
    return [{
        "row_index": index,
        "page_id": str(1000 + index),
        "page_name": f"Page {index}",
        "title": f"Noi dung {index}",
        "description": "",
        "video_path": rf"C:\v\video_{index}.mp4" if with_video else "",
        "schedule_time": "",
        "comment_text": "",
        "comment_image_paths": "",
        "post_type": "video",
    } for index in range(n)]


def _pages(n):
    return [{"id": str(1000 + i), "name": f"Page {i}", "access_token": "tok", "tasks": ["CREATE_CONTENT"]}
            for i in range(n)]


def _run(service, rows, pages, threads, delay=0.0, enabled=True):
    """Chạy hàng đợi, trả về (kết quả, dict {page_name: thời điểm bắt đầu})."""
    starts = {}
    lock = threading.Lock()

    def timed_post_media(page, message, media_path, base_url, media_type="video",
                         title=None, proxy_config=None, post_type=None):
        with lock:
            starts[page["name"]] = time.time()
        time.sleep(0.4)
        return {"permalink_url": f"https://fb/reel/{page['id']}"}

    service.post_media = timed_post_media
    result = {}
    service.run_config_post_queue(
        rows=rows, pages=pages, base_url="https://graph",
        concurrency_enabled=enabled, concurrency_threads=threads, concurrency_delay=delay,
        on_status=lambda *a: None, on_config_status=lambda *a: None,
        on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
    )
    return result, starts


class ParallelTimingTest(unittest.TestCase):
    """Số luồng > số page: TẤT CẢ page phải cùng chạy (trường hợp người dùng gặp)."""

    def test_5_threads_3_pages_all_start_together(self):
        service = _make_service()
        result, starts = _run(service, _rows(3), _pages(3), threads=5)
        self.assertEqual(len(starts), 3)
        self.assertEqual(result["success"], 3)
        self.assertEqual(result["fail"], 0)
        spread = max(starts.values()) - min(starts.values())
        self.assertLess(spread, 0.2, f"3 page/5 luong khong chay dong thoi (lech {spread:.2f}s)")

    def test_5_threads_3_pages_parallel_not_batched(self):
        """3 page mỗi page 0.4s, 5 luồng -> tổng ~0.4s (chia lô sẽ mất ~1.2s)."""
        service = _make_service()
        began = time.time()
        _run(service, _rows(3), _pages(3), threads=5)
        elapsed = time.time() - began
        self.assertLess(elapsed, 0.9, f"chạy tuần tự/chia lo: {elapsed:.2f}s (mong doi < 0.9s)")

    def test_1_thread_1_page(self):
        service = _make_service()
        result, starts = _run(service, _rows(1), _pages(1), threads=1, enabled=False)
        self.assertEqual(result["success"], 1)
        self.assertEqual(len(starts), 1)

    def test_5_threads_10_pages_pipelined(self):
        """5 luồng / 10 page: 5 chạy trước, 5 chạy tiếp khi có slot trống."""
        service = _make_service()
        result, starts = _run(service, _rows(10), _pages(10), threads=5)
        self.assertEqual(result["success"], 10)
        self.assertEqual(len(starts), 10)
        first_wave = sorted(starts.values())[:5]
        second_wave = sorted(starts.values())[5:]
        self.assertLess(max(first_wave) - min(first_wave), 0.2)
        # 5 page thứ 2 phải bắt đầu NGAY khi slot trống, không chờ cả lô.
        self.assertLess(max(second_wave) - min(first_wave), 0.7)

    def test_delay_staggers_but_does_not_block(self):
        service = _make_service()
        result, starts = _run(service, _rows(3), _pages(3), threads=5, delay=0.15)
        self.assertEqual(result["success"], 3)
        self.assertLess(max(starts.values()) - min(starts.values()), 0.6)


class MatchPageTest(unittest.TestCase):
    def test_match_by_id_wins_over_name(self):
        pages = [
            {"id": "111", "name": "Hạ", "access_token": "tok_A"},
            {"id": "222", "name": "Hạ", "access_token": "tok_B"},
        ]
        found = PostService._match_page(pages, "Hạ", "222")
        self.assertEqual(found["id"], "222")
        self.assertEqual(found["access_token"], "tok_B")

    def test_match_falls_back_to_name(self):
        pages = [{"id": "111", "name": "Hạ", "access_token": "t"}]
        self.assertEqual(PostService._match_page(pages, "Hạ", "")["id"], "111")
        self.assertEqual(PostService._match_page(pages, "111", None)["id"], "111")

    def test_match_returns_none_when_missing(self):
        self.assertIsNone(PostService._match_page([{"id": "1", "name": "A"}], "B", ""))


class FailureReportingTest(unittest.TestCase):
    def test_failure_is_logged_and_reported(self):
        service = _make_service()
        details = {}
        result = {}

        def boom(page, message, media_path, base_url, media_type="video",
                 title=None, proxy_config=None, post_type=None):
            raise RuntimeError("Không tạo được session upload")

        service.post_media = boom
        service.run_config_post_queue(
            rows=_rows(1), pages=_pages(1), base_url="https://graph",
            concurrency_enabled=False, concurrency_threads=1, concurrency_delay=0,
            on_status=lambda n, s, d: details.update({n: d}),
            on_config_status=lambda *a: None,
            on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
        )
        self.assertEqual(result["fail"], 1)
        self.assertIn("Không tạo được session upload", result["reasons"][0][1])
        self.assertIn("Không tạo được session upload", details["Page 0"])

    def test_missing_video_reports_clearly(self):
        service = _make_service()
        result = {}
        service.post_media = lambda *a, **k: {}
        service.run_config_post_queue(
            rows=_rows(1, with_video=False), pages=_pages(1), base_url="https://graph",
            concurrency_enabled=False, concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
            on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
        )
        self.assertEqual(result["fail"], 1)
        self.assertIn("chưa có video", result["reasons"][0][1])

    def test_missing_access_token_reports_clearly(self):
        service = _make_service()
        pages = [{"id": "1000", "name": "Page 0", "access_token": "", "tasks": ["CREATE_CONTENT"]}]
        result = {}
        service.post_media = lambda *a, **k: {}
        service.run_config_post_queue(
            rows=_rows(1), pages=pages, base_url="https://graph",
            concurrency_enabled=False, concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
            on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
        )
        self.assertEqual(result["fail"], 1)
        self.assertIn("Access Token", result["reasons"][0][1])

    def test_unknown_page_reports_clearly(self):
        service = _make_service()
        rows = _rows(1)
        rows[0]["page_id"] = "999"
        rows[0]["page_name"] = "Khong Ton Tai"
        result = {}
        service.post_media = lambda *a, **k: {}
        service.run_config_post_queue(
            rows=rows, pages=_pages(1), base_url="https://graph",
            concurrency_enabled=False, concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
            on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
        )
        self.assertEqual(result["fail"], 1)
        self.assertIn("Không tìm thấy page", result["reasons"][0][1])

    def test_duplicate_names_use_correct_token(self):
        """2 page trùng tên, mỗi page 1 video -> đúng token tương ứng."""
        service = _make_service()
        used = {}
        lock = threading.Lock()
        pages = [
            {"id": "111", "name": "Hạ", "access_token": "tok_A", "tasks": ["CREATE_CONTENT"]},
            {"id": "222", "name": "Hạ", "access_token": "tok_B", "tasks": ["CREATE_CONTENT"]},
        ]
        rows = [
            {"row_index": 0, "page_id": "111", "page_name": "Hạ", "title": "t", "description": "",
             "video_path": r"C:\v\a.mp4", "schedule_time": "", "comment_text": "",
             "comment_image_paths": "", "post_type": "video"},
            {"row_index": 1, "page_id": "222", "page_name": "Hạ", "title": "t", "description": "",
             "video_path": r"C:\v\b.mp4", "schedule_time": "", "comment_text": "",
             "comment_image_paths": "", "post_type": "video"},
        ]

        def record_post_media(page, message, media_path, base_url, media_type="video",
                              title=None, proxy_config=None, post_type=None):
            with lock:
                used[media_path] = page["access_token"]
            return {"permalink_url": f"https://fb/reel/{page['id']}"}

        service.post_media = record_post_media
        result = {}
        service.run_config_post_queue(
            rows=rows, pages=pages, base_url="https://graph",
            concurrency_enabled=True, concurrency_threads=2, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
            on_complete=lambda s, f, r: result.update(success=s, fail=f, reasons=r),
        )
        self.assertEqual(result["success"], 2)
        self.assertEqual(used[r"C:\v\a.mp4"], "tok_A")
        self.assertEqual(used[r"C:\v\b.mp4"], "tok_B")


if __name__ == "__main__":
    unittest.main()
