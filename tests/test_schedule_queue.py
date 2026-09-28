"""Test lịch đăng chạy nền: sinh khung giờ, lưu DB, quét và đẩy đúng giờ."""
import gc
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from models.database import DatabaseManager
from services.post_service import PostService
from services.schedule_service import (
    MODE_INTERVAL,
    MODE_SLOTS,
    ScheduleService,
    build_slots,
    parse_hhmm,
    row_to_schedule_row,
)


# ── build_slots ──────────────────────────────────────────────────────

class BuildSlotsTests(unittest.TestCase):
    def test_interval_bắt_đầu_từ_thời_điểm_cho_trước(self):
        start = datetime(2026, 9, 28, 9, 0)
        slots = build_slots(3, start, MODE_INTERVAL, interval_minutes=30)
        self.assertEqual(slots, [
            datetime(2026, 9, 28, 9, 0),
            datetime(2026, 9, 28, 9, 30),
            datetime(2026, 9, 28, 10, 0),
        ])

    def test_interval_tối_thiểu_một_phút(self):
        start = datetime(2026, 9, 28, 9, 0)
        slots = build_slots(2, start, MODE_INTERVAL, interval_minutes=0)
        self.assertEqual(slots, [start, start + timedelta(minutes=1)])

    def test_khung_giờ_lặp_qua_nhiều_ngày(self):
        start = datetime(2026, 9, 28, 7, 0)
        slots = build_slots(5, start, MODE_SLOTS, times_of_day=["09:00", "14:00", "20:00"])
        self.assertEqual(slots, [
            datetime(2026, 9, 28, 9, 0),
            datetime(2026, 9, 28, 14, 0),
            datetime(2026, 9, 28, 20, 0),
            datetime(2026, 9, 29, 9, 0),
            datetime(2026, 9, 29, 14, 0),
        ])

    def test_khung_giờ_đã_trôi_qua_bị_bỏ(self):
        start = datetime(2026, 9, 28, 15, 0)
        slots = build_slots(2, start, MODE_SLOTS, times_of_day=["09:00", "20:00"])
        self.assertEqual(slots, [datetime(2026, 9, 28, 20, 0), datetime(2026, 9, 29, 9, 0)])

    def test_giới_hạn_số_video_mỗi_ngày(self):
        start = datetime(2026, 9, 28, 7, 0)
        slots = build_slots(4, start, MODE_SLOTS, times_of_day=["09:00", "14:00", "20:00"], daily_limit=2)
        self.assertEqual(slots, [
            datetime(2026, 9, 28, 9, 0),
            datetime(2026, 9, 28, 14, 0),
            datetime(2026, 9, 29, 9, 0),
            datetime(2026, 9, 29, 14, 0),
        ])

    def test_khung_giờ_không_hợp_lệ_thì_không_có_lịch(self):
        start = datetime(2026, 9, 28, 7, 0)
        self.assertEqual(build_slots(3, start, MODE_SLOTS, times_of_day=["abc", "99:99", ""]), [])
        self.assertEqual(build_slots(3, start, MODE_SLOTS, times_of_day=[]), [])

    def test_khung_giờ_trùng_nhau_chỉ_giữ_một(self):
        start = datetime(2026, 9, 28, 7, 0)
        slots = build_slots(2, start, MODE_SLOTS, times_of_day=["09:00", "9:00"])
        self.assertEqual(slots, [datetime(2026, 9, 28, 9, 0), datetime(2026, 9, 29, 9, 0)])

    def test_count_bằng_0_thì_không_sinh_gì(self):
        self.assertEqual(build_slots(0, datetime.now(), MODE_SLOTS, times_of_day=["09:00"]), [])

    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("09:05"), (9, 5))
        self.assertEqual(parse_hhmm("9.5"), (9, 5))
        self.assertIsNone(parse_hhmm("9"))
        self.assertIsNone(parse_hhmm("25:00"))
        self.assertIsNone(parse_hhmm(None))


# ── Repository ───────────────────────────────────────────────────────

class ScheduleRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = DatabaseManager(Path(self._tmp.name) / "test.sqlite3")
        self.rows = [{
            "page_id": "111",
            "page_name": "Page A",
            "title": "Tieu de",
            "description": "Mo ta",
            "video_path": r"C:\v\a.mp4",
            "comment_text": "cmt",
            "comment_image_paths": "",
            "post_type": "video",
            "schedule_time": "2026-09-28T09:00:00",
        }]

    def tearDown(self):
        # `with sqlite3.connect(...)` không đóng connection nên Windows giữ
        # file khoá -> phải ép GC trước khi xoá thư mục tạm.
        gc.collect()
        self._tmp.cleanup()

    def test_lưu_đọc_và_xoá_lịch(self):
        saved = self.db.save_scheduled_posts(self.rows)
        self.assertEqual(len(saved), 1)
        self.assertIn("schedule_id", saved[0])
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 1)

        pending = self.db.load_pending_scheduled_posts()
        self.assertEqual(pending[0]["page_name"], "Page A")
        self.assertEqual(pending[0]["video_path"], r"C:\v\a.mp4")

        self.assertEqual(self.db.clear_scheduled_posts(["pending"]), 1)
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 0)

    def test_lên_lịch_lại_cùng_giờ_thì_ghi_đè_không_nhân_bản(self):
        self.db.save_scheduled_posts(self.rows)
        changed = [{**self.rows[0], "title": "Tieu de moi", "video_path": r"C:\v\b.mp4"}]
        self.db.save_scheduled_posts(changed)
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 1)
        pending = self.db.load_pending_scheduled_posts()
        self.assertEqual(pending[0]["title"], "Tieu de moi")

    def test_load_due_chỉ_lấy_lịch_đã_tới_giờ(self):
        past = [{**self.rows[0], "schedule_time": (datetime.now() - timedelta(hours=1)).isoformat()}]
        future = [{**self.rows[0], "page_id": "222", "page_name": "Page B",
                   "schedule_time": (datetime.now() + timedelta(hours=1)).isoformat()}]
        self.db.save_scheduled_posts(past + future)
        due = self.db.load_due_scheduled_posts(datetime.now().isoformat())
        self.assertEqual([d["page_name"] for d in due], ["Page A"])

    def test_đánh_dấu_đang_chạy_và_trả_về_chờ(self):
        self.db.save_scheduled_posts(self.rows)
        pending_id = self.db.load_pending_scheduled_posts()[0]["id"]
        self.db.mark_scheduled_dispatched([pending_id])
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 0)
        self.assertEqual(self.db.count_scheduled_posts(["running"]), 1)
        self.assertEqual(self.db.reset_running_scheduled_posts(), 1)
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 1)

    def test_lưu_kết_quả_đăng(self):
        self.db.save_scheduled_posts(self.rows)
        row_id = self.db.load_pending_scheduled_posts()[0]["id"]
        self.db.update_scheduled_result(row_id, "success", "https://fb/1", "", "2026-09-28 09:00:01")
        stored = self.db.load_scheduled_posts()[0]
        self.assertEqual(stored["status"], "success")
        self.assertEqual(stored["link"], "https://fb/1")

    def test_xoá_theo_danh_sách_id(self):
        self.db.save_scheduled_posts(self.rows)
        row_id = self.db.load_pending_scheduled_posts()[0]["id"]
        self.assertEqual(self.db.delete_scheduled_posts([row_id]), 1)

    def test_dòng_thiếu_tên_page_hoặc_thiếu_giờ_thì_bỏ(self):
        self.assertEqual(self.db.save_scheduled_posts([{"page_name": "", "schedule_time": "x"}]), [])
        self.assertEqual(self.db.save_scheduled_posts([{"page_name": "A"}]), [])

    def test_app_settings(self):
        self.assertEqual(self.db.get_setting("khoa", "mac_dinh"), "mac_dinh")
        self.db.set_setting("khoa", "gia_tri")
        self.assertEqual(self.db.get_setting("khoa"), "gia_tri")
        self.db.set_setting("khoa", "gia_tri_2")
        self.assertEqual(self.db.get_setting("khoa"), "gia_tri_2")

    def test_page_key_ưu_tiên_page_id(self):
        row = row_to_schedule_row(
            {"page_id": "999", "page_name": "Trùng tên", "video_path": "v.mp4"},
            datetime(2026, 9, 28, 9, 0),
        )
        saved = self.db.save_scheduled_posts([row])
        self.assertEqual(saved[0]["page_key"], "999")
        # 2 tài khoản cùng tên page vẫn là 2 lịch khác nhau.
        self.db.save_scheduled_posts([{**row, "page_id": "888"}])
        self.assertEqual(self.db.count_scheduled_posts(["pending"]), 2)


# ── ScheduleService ──────────────────────────────────────────────────

class _FakeRepo:
    def __init__(self, db):
        self.db = db

    def save_scheduled_posts(self, rows):
        return self.db.save_scheduled_posts(rows)

    def load_pending_scheduled_posts(self, limit=200):
        return self.db.load_pending_scheduled_posts(limit)

    def load_due_scheduled_posts(self, now_iso):
        return self.db.load_due_scheduled_posts(now_iso)

    def mark_scheduled_dispatched(self, ids, when_iso=""):
        return self.db.mark_scheduled_dispatched(ids, when_iso)

    def reset_running_scheduled_posts(self):
        return self.db.reset_running_scheduled_posts()

    def update_scheduled_result(self, schedule_id, status, link="", error="", posted_at=""):
        return self.db.update_scheduled_result(schedule_id, status, link, error, posted_at)

    def delete_scheduled_posts(self, ids):
        return self.db.delete_scheduled_posts(ids)

    def clear_scheduled_posts(self, statuses=None):
        return self.db.clear_scheduled_posts(statuses)

    def count_scheduled_posts(self, statuses=None):
        return self.db.count_scheduled_posts(statuses)

    def get_setting(self, key, default=""):
        return self.db.get_setting(key, default)

    def set_setting(self, key, value):
        return self.db.set_setting(key, value)


class _FakePostRepo:
    def load_posted_video_names_for_page(self, page_key):
        return set()

    def record_page_video(self, *args):
        pass

    def log_successful_post(self, *args):
        pass


class _FakeUploader:
    def get_permalink(self, page, upload_id, proxy_config=None):
        return f"https://www.facebook.com/reel/{upload_id}"


class _FakeProxy:
    def fetch_proxy(self):
        return None

    def build_proxy_config(self, proxy_info):
        return None


class ScheduleDispatchTests(unittest.TestCase):
    """Phần quan trọng nhất: lịch tới giờ phải được đẩy vào đúng luồng đăng."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = DatabaseManager(Path(self._tmp.name) / "test.sqlite3")
        self.repo = _FakeRepo(self.db)
        self.post_service = PostService(_FakePostRepo(), _FakeUploader(), _FakeProxy())
        self.service = ScheduleService(self.repo, self.post_service, "https://graph.facebook.com/v25.0")
        self.events = []
        self.pages = [{
            "id": "111", "name": "Page A", "access_token": "PAT-111", "tasks": ["CREATE_CONTENT"],
        }]
        # Ghi đè post_media để không gọi mạng.
        self.posted = []
        self.service._get_pages = lambda: self.pages
        self.service._on_event = self.events.append

        def fake_post_media(page, message, media_path, base_url, media_type="video",
                            title="", proxy_config=None, post_type=None):
            self.posted.append((page["id"], media_path))
            return {"id": "post-1", "permalink_url": "https://fb/post-1"}

        self.post_service.post_media = fake_post_media

    def tearDown(self):
        # `with sqlite3.connect(...)` không đóng connection nên Windows giữ
        # file khoá -> dừng luồng nền và ép GC trước khi xoá thư mục tạm.
        self.service.stop()
        thread = self.service._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        gc.collect()
        self._tmp.cleanup()

    def _queue_rows(self, count=2):
        return [{
            "page_id": "111",
            "page_name": "Page A",
            "title": f"Tieu de {index}",
            "description": "",
            "video_path": rf"C:\v\{index}.mp4",
            "comment": "",
            "comment_images": "",
            "post_type": "video",
        } for index in range(count)]

    def test_lên_lịch_rồi_quét_thì_đăng_đúng_số_video(self):
        start = datetime.now() - timedelta(hours=1)
        saved = self.service.plan_rows(self._queue_rows(3), {
            "mode": MODE_INTERVAL, "start_at": start, "interval_minutes": 10,
        })
        self.assertEqual(len(saved), 3)
        self.assertEqual(self.service.pending_count(), 3)

        self.service._tick()

        self.assertEqual(len(self.posted), 3)
        self.assertEqual(self.service.pending_count(), 0)
        stored = self.db.load_scheduled_posts()
        self.assertTrue(all(item["status"] == "success" for item in stored))
        self.assertTrue(all(item["link"] == "https://fb/post-1" for item in stored))
        self.assertTrue(all(item["posted_at"] for item in stored))
        kinds = [event["type"] for event in self.events]
        self.assertIn("dispatched", kinds)
        self.assertIn("done", kinds)

    def test_lịch_chưa_tới_giờ_thì_không_đăng(self):
        start = datetime.now() + timedelta(hours=2)
        self.service.plan_rows(self._queue_rows(2), {
            "mode": MODE_INTERVAL, "start_at": start, "interval_minutes": 10,
        })
        self.service._tick()
        self.assertEqual(self.posted, [])
        self.assertEqual(self.service.pending_count(), 2)

    def test_lịch_quá_khứ_được_đăng_bù_khi_mở_lại_app(self):
        self.service.plan_rows(self._queue_rows(1), {
            "mode": MODE_INTERVAL,
            "start_at": datetime.now() - timedelta(days=1),
            "interval_minutes": 60,
        })
        self.service._tick()
        self.assertEqual(len(self.posted), 1)

    def test_lịch_kẹt_running_được_trả_về_chờ_khi_bật_lại(self):
        self.service.plan_rows(self._queue_rows(1), {
            "mode": MODE_INTERVAL,
            "start_at": datetime.now() - timedelta(minutes=5),
            "interval_minutes": 10,
        })
        row_id = self.db.load_pending_scheduled_posts()[0]["id"]
        self.db.mark_scheduled_dispatched([row_id])
        self.assertEqual(self.service.pending_count(), 0)

        started = self.service.start(get_pages=lambda: self.pages, on_event=self.events.append)
        self.assertTrue(started)
        self.service.stop()
        self.assertEqual(self.service.pending_count(), 1)

    def test_không_có_page_thì_báo_đúng_lý_do(self):
        self.service.plan_rows(self._queue_rows(1), {
            "mode": MODE_INTERVAL, "start_at": datetime.now() - timedelta(minutes=1),
            "interval_minutes": 10,
        })
        self.service._get_pages = lambda: []
        self.service._tick()
        self.assertEqual(self.posted, [])
        warnings = [e for e in self.events if e["type"] == "warning"]
        self.assertTrue(warnings)
        # Vẫn giữ lịch chờ để không mất video.
        self.assertEqual(self.service.pending_count(), 1)

    def test_xoá_lịch_chờ_giữ_lịch_đã_đăng(self):
        self.service.plan_rows(self._queue_rows(2), {
            "mode": MODE_INTERVAL, "start_at": datetime.now() - timedelta(minutes=5),
            "interval_minutes": 5,
        })
        self.service._tick()
        self.service.plan_rows(self._queue_rows(1), {
            "mode": MODE_INTERVAL, "start_at": datetime.now() + timedelta(hours=3),
            "interval_minutes": 30,
        })
        self.assertEqual(self.service.pending_count(), 1)
        self.assertEqual(self.service.clear_pending(), 1)
        self.assertEqual(self.service.pending_count(), 0)
        self.assertEqual(len(self.db.load_scheduled_posts()), 2)

    def test_thất_bại_thì_ghi_lỗi_vào_lịch(self):
        def boom(*args, **kwargs):
            raise RuntimeError("Facebook từ chối")

        self.post_service.post_media = boom
        self.service.plan_rows(self._queue_rows(1), {
            "mode": MODE_INTERVAL, "start_at": datetime.now() - timedelta(minutes=1),
            "interval_minutes": 10,
        })
        self.service._tick()
        stored = self.db.load_scheduled_posts()[0]
        self.assertEqual(stored["status"], "failed")
        self.assertIn("Facebook từ chối", stored["error"])

    def test_bị_dừng_giữa_chừng_thì_trả_lịch_về_chờ(self):
        """Bấm Dừng giữa lô: dòng chưa kịp đăng phải quay lại hàng đợi."""
        def stop_after_first(page, message, media_path, base_url, **kwargs):
            self.posted.append((page["id"], media_path))
            self.post_service.stop_requested = True
            return {"id": "post-1", "permalink_url": "https://fb/post-1"}

        self.post_service.post_media = stop_after_first
        self.service.plan_rows(self._queue_rows(3), {
            "mode": MODE_INTERVAL, "start_at": datetime.now() - timedelta(minutes=1),
            "interval_minutes": 10,
        })
        self.service._tick()
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.service.pending_count(), 2)
        statuses = sorted(item["status"] for item in self.db.load_scheduled_posts())
        self.assertEqual(statuses, ["pending", "pending", "success"])


class ScheduleQueueResultTests(unittest.TestCase):
    """Mỗi dòng của hàng đợi phải có ``result["status"]`` để scheduler ghi được."""

    def setUp(self):
        self.post_service = PostService(_FakePostRepo(), _FakeUploader(), _FakeProxy())
        self.post_service.post_media = lambda *a, **k: {"id": "1", "permalink_url": "https://fb/1"}
        self.page = {"id": "111", "name": "Page A", "access_token": "PAT", "tasks": ["CREATE_CONTENT"]}

    def _rows(self, count=1):
        return [{
            "row_index": i, "page_id": "111", "page_name": "Page A", "title": "t",
            "description": "", "video_path": rf"C:\v\{i}.mp4", "schedule_time": "",
            "comment_text": "", "comment_image_paths": "", "post_type": "video",
        } for i in range(count)]

    def test_thành_công_có_status_success(self):
        rows = self._rows(1)
        self.post_service.run_config_post_queue(
            rows=rows, pages=[self.page], base_url="u", concurrency_enabled=False,
            concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
        )
        self.assertEqual(rows[0]["result"]["status"], "success")

    def test_thiếu_video_có_status_failed(self):
        rows = self._rows(1)
        rows[0]["video_path"] = ""
        self.post_service.run_config_post_queue(
            rows=rows, pages=[self.page], base_url="u", concurrency_enabled=False,
            concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
        )
        self.assertEqual(rows[0]["result"]["status"], "failed")
        self.assertIn("chưa có video", rows[0]["result"]["error"])

    def test_đã_đăng_rồi_có_status_skipped(self):
        repo = _PostedNamesRepo({"a.mp4"})
        service = PostService(repo, _FakeUploader(), _FakeProxy())
        service.post_media = lambda *a, **k: {"id": "1"}
        rows = [{
            "row_index": 0, "page_id": "111", "page_name": "Page A", "title": "t",
            "description": "", "video_path": r"C:\v\a.mp4", "schedule_time": "",
            "comment_text": "", "comment_image_paths": "", "post_type": "video",
        }]
        service.run_config_post_queue(
            rows=rows, pages=[self.page], base_url="u", concurrency_enabled=False,
            concurrency_threads=1, concurrency_delay=0,
            on_status=lambda *a: None, on_config_status=lambda *a: None,
        )
        self.assertEqual(rows[0]["result"]["status"], "skipped")

    def test_không_chạy_chồng_hai_lô_đăng(self):
        import threading
        state = {"busy_during": False, "max_depth": 0}
        depth = {"value": 0}
        lock = threading.Lock()

        def slow_post(*args, **kwargs):
            with lock:
                depth["value"] += 1
                state["max_depth"] = max(state["max_depth"], depth["value"])
                state["busy_during"] = self.post_service.is_busy
            import time
            time.sleep(0.05)
            with lock:
                depth["value"] -= 1
            return {"id": "1", "permalink_url": "https://fb/1"}

        self.post_service.post_media = slow_post
        results = []

        def run():
            self.post_service.run_config_post_queue(
                rows=self._rows(1), pages=[self.page], base_url="u", concurrency_enabled=False,
                concurrency_threads=1, concurrency_delay=0,
                on_status=lambda *a: None, on_config_status=lambda *a: None,
                on_complete=lambda *a: results.append(a),
            )

        first = threading.Thread(target=run)
        first.start()
        second = threading.Thread(target=run)
        second.start()
        first.join()
        second.join()
        self.assertEqual(state["max_depth"], 1)
        self.assertTrue(state["busy_during"])
        self.assertFalse(self.post_service.is_busy)
        self.assertEqual(len(results), 2)


class _PostedNamesRepo(_FakePostRepo):
    def __init__(self, names):
        super().__init__()
        self._names = names

    def load_posted_video_names_for_page(self, page_key):
        return set(self._names)


if __name__ == "__main__":
    unittest.main()
