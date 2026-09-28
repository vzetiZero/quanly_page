"""Test giao diện: đồng bộ Followers/View với tab Thống kê + danh sách video có lọc/sắp xếp."""
import os
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

try:
    from di.container import Container
    from presenters.main_presenter import MainPresenter
    from views.main_window import FacebookPageManagerWindow
    from views.page_detail_dialog import (
        VIDEO_COL_CREATED,
        VIDEO_COL_INDEX,
        VIDEO_COL_LINK,
        VIDEO_COL_VIEWS,
        PageDetailDialog,
    )
    from views.widgets import PAGE_COL_FOLLOWERS, PAGE_COL_VIEWS as PAGE_COL_VIEWS_PAGE, PAGE_HEADERS
    IMPORTS_OK = True
except Exception as exc:  # pragma: no cover
    IMPORTS_OK = False
    IMPORT_ERROR = exc

try:
    from tests.test_page_videos_list import REELS, VIDEOS, VIEWS, make_get
except Exception:  # pragma: no cover
    from test_page_videos_list import REELS, VIDEOS, VIEWS, make_get


VIDEOS_ALL = REELS + VIDEOS


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class PageStatsSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = FacebookPageManagerWindow(Container(PROJECT))
        self.presenter = MainPresenter(self.window._container, self.window)
        self.window.set_presenter(self.presenter)
        self.window.show_info = self.window.show_warning = self.window.show_error = lambda *a, **k: None
        self.presenter.page_list.pages = [
            {"id": "1", "name": "Page A", "account_token": "T1", "account_label": "Acc1",
             "access_token": "tok", "status": "Valid", "tasks": ["CREATE_CONTENT"]},
            {"id": "2", "name": "Page B", "account_token": "T1", "account_label": "Acc1",
             "access_token": "tok", "status": "Valid", "tasks": ["CREATE_CONTENT"]},
        ]

    def _cell(self, row, col):
        item = self.window.page_table.item(row, col)
        return item.text() if item else ""

    # ── 1. Cột Followers / View đồng bộ với tab Thống kê ──
    def test_headers_have_separate_followers_and_views(self):
        self.assertEqual(PAGE_HEADERS[PAGE_COL_FOLLOWERS], "Followers")
        self.assertEqual(PAGE_HEADERS[PAGE_COL_VIEWS_PAGE], "View video")
        self.assertEqual(self.window.page_table.columnCount(), len(PAGE_HEADERS))

    def test_follow_view_defaults_to_na(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.assertEqual(self._cell(0, PAGE_COL_FOLLOWERS), "N/A")
        self.assertEqual(self._cell(0, PAGE_COL_VIEWS_PAGE), "N/A")

    def test_sync_from_stats_tab_rows(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.sync_page_stats_from_stats_tab([
            {"page": "Page A", "followers": 12345, "fan_count": 12000,
             "video_views": 9876, "page_video_count": 4, "updated_at": "2026-03-01 10:00:00"},
            {"page": "Page B", "followers": None, "video_views": None},
        ])
        self.assertEqual(self._cell(0, PAGE_COL_FOLLOWERS), "12,345")
        self.assertEqual(self._cell(0, PAGE_COL_VIEWS_PAGE), "9,876")
        self.assertEqual(self._cell(1, PAGE_COL_FOLLOWERS), "N/A")
        self.assertEqual(self._cell(1, PAGE_COL_VIEWS_PAGE), "N/A")

    def test_sync_accepts_single_dict_from_progress_signal(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.sync_page_stats_from_stats_tab(
            {"page": "Page B", "followers": 7, "video_views": 8}
        )
        self.assertEqual(self._cell(1, PAGE_COL_FOLLOWERS), "7")
        self.assertEqual(self._cell(1, PAGE_COL_VIEWS_PAGE), "8")

    def test_stats_keep_after_page_table_repaint(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.sync_page_stats_from_stats_tab([{"page": "Page A", "followers": 50, "video_views": 60}])
        # Vẽ lại bảng (đổi trang / đổi quyền) không được mất số liệu.
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.assertEqual(self._cell(0, PAGE_COL_FOLLOWERS), "50")
        self.assertEqual(self._cell(0, PAGE_COL_VIEWS_PAGE), "60")

    def test_stats_tab_refresh_flows_into_page_table(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.stats_tab.stats_progress.emit(
            {"page": "Page A", "followers": 111, "video_views": 222, "status": "ok"}
        )
        self.app.processEvents()
        self.assertEqual(self._cell(0, PAGE_COL_FOLLOWERS), "111")
        self.assertEqual(self._cell(0, PAGE_COL_VIEWS_PAGE), "222")

    def test_update_page_info_sets_only_given_values(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.update_page_info("1", {"followers": 9, "views": "N/A"})
        self.assertEqual(self._cell(0, PAGE_COL_FOLLOWERS), "9")
        self.assertEqual(self._cell(0, PAGE_COL_VIEWS_PAGE), "N/A")
        self.assertEqual(self._cell(1, PAGE_COL_FOLLOWERS), "N/A")

    def test_tooltip_shows_update_time(self):
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.window.sync_page_stats_from_stats_tab(
            [{"page": "Page A", "followers": 5, "video_views": 6, "updated_at": "2026-03-01 10:00:00"}]
        )
        tooltip = self.window.page_table.item(0, PAGE_COL_FOLLOWERS).toolTip()
        self.assertIn("5", tooltip)
        self.assertIn("2026-03-01 10:00:00", tooltip)


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class PageDetailVideoListTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.dialog = PageDetailDialog("1", "Page A", "tok")
        self.videos = []
        seen = set()
        for item in VIDEOS_ALL:
            video_id = item["id"]
            if video_id in seen:  # service đã khử trùng khi lấy từ Facebook
                continue
            seen.add(video_id)
            self.videos.append({
                "id": video_id,
                "title": item.get("description") or item.get("title") or "",
                "link": item.get("permalink_url") or f"https://www.facebook.com/reel/{video_id}",
                "created_time": item.get("created_time", ""),
                "views": VIEWS.get(video_id),
            })

    def tearDown(self):
        self.dialog.deleteLater()

    def _col_texts(self, col):
        return [
            self.dialog.video_table.item(row, col).text()
            for row in range(self.dialog.video_table.rowCount())
        ]

    def test_populate_lists_all_videos_with_index(self):
        self.dialog.populate_videos(self.videos)
        self.assertEqual(self.dialog.video_table.rowCount(), len(self.videos))
        # Mới nhất trước (default)
        self.assertEqual(self._col_texts(VIDEO_COL_INDEX), ["1", "2", "3", "4"])
        self.assertEqual(self.dialog.video_table.item(0, VIDEO_COL_LINK).text(),
                         "https://facebook.com/reel/r1")
        self.assertEqual(self.dialog.video_table.item(0, VIDEO_COL_VIEWS).text(), "500")
        self.assertIn("Tổng: 4 video", self.dialog.video_count_label.text())

    def test_index_is_renumbered_after_filter(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_filter_input.setText("Video giua")
        self.assertEqual(self.dialog.video_table.rowCount(), 1)
        self.assertEqual(self._col_texts(VIDEO_COL_INDEX), ["1"])
        self.assertIn("Hiện 1/4 video", self.dialog.video_count_label.text())

    def test_filter_matches_video_id(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_filter_input.setText("r3")
        self.assertEqual(self.dialog.video_table.rowCount(), 1)
        self.assertEqual(self.dialog.video_table.item(0, VIDEO_COL_LINK).text(),
                         "https://facebook.com/reel/r3")

    def test_clear_filter_restores_all(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_filter_input.setText("abc khong ton tai")
        self.assertEqual(self.dialog.video_table.rowCount(), 0)
        self.dialog.video_filter_input.setText("")
        self.assertEqual(self.dialog.video_table.rowCount(), 4)

    def test_sort_by_views_desc(self):
        self.dialog.populate_videos(self.videos)
        idx = [key for label, key in
               [("x", "newest"), ("x", "oldest"), ("x", "views_desc"), ("x", "views_asc"), ("x", "index")]
               if key == "views_desc"][0]
        self.dialog.video_sort_combo.setCurrentIndex(2)
        self.assertEqual(self._col_texts(VIDEO_COL_VIEWS), ["500", "90", "30", "N/A"])
        self.assertEqual(self._col_texts(VIDEO_COL_INDEX), ["1", "2", "3", "4"])
        self.assertEqual(idx, "views_desc")

    def test_sort_by_views_asc_puts_missing_last(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_sort_combo.setCurrentIndex(3)
        self.assertEqual(self._col_texts(VIDEO_COL_VIEWS), ["30", "90", "500", "N/A"])

    def test_sort_by_oldest(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_sort_combo.setCurrentIndex(1)
        self.assertEqual(self._col_texts(VIDEO_COL_INDEX), ["1", "2", "3", "4"])
        self.assertEqual(self.dialog.video_table.item(0, VIDEO_COL_LINK).text(),
                         "https://www.facebook.com/reel/v9")

    def test_filter_and_sort_combine(self):
        self.dialog.populate_videos(self.videos)
        self.dialog.video_sort_combo.setCurrentIndex(1)  # cũ nhất trước
        self.dialog.video_filter_input.setText("Video")
        links = self._col_texts(VIDEO_COL_LINK)
        self.assertEqual(len(links), 3)
        self.assertEqual(links[-1], "https://facebook.com/reel/r1")

    def test_populate_overview_shows_video_view_follow(self):
        self.dialog.populate_overview({
            "page_video_count": 4, "video_views": 620, "followers": 1234, "fan_count": 1200,
            "updated_at": "2026-03-01 10:00:00",
        })
        self.assertEqual(self.dialog.labels["page_video_count"].text(), "4")
        self.assertEqual(self.dialog.labels["video_views"].text(), "620")
        self.assertEqual(self.dialog.labels["followers_count"].text(), "1,234")
        self.assertEqual(self.dialog.labels["fan_count"].text(), "1,200")
        self.assertIn("2026-03-01 10:00:00", self.dialog.updated_at_label.text())

    def test_populate_overview_missing_values_show_na(self):
        self.dialog.populate_overview({})
        self.assertEqual(self.dialog.labels["page_video_count"].text(), "N/A")
        self.assertEqual(self.dialog.labels["video_views"].text(), "N/A")

    def test_presenter_loads_videos_through_stats_service(self):
        from presenters.page_detail_presenter import PageDetailPresenter

        stats_service = self.dialog._container_stats_service = type(
            "FakeStats", (), {}
        )()
        received = {}

        def fetch_page_videos_async(page_id, token, on_complete, on_error=None):
            received["args"] = (page_id, token)
            on_complete([{"id": "v1", "link": "https://x", "views": 3, "created_time": "2026-01-01"}])

        stats_service.fetch_page_videos_async = fetch_page_videos_async
        presenter = PageDetailPresenter(
            view=self.dialog,
            page_detail_service=self.dialog_presenter_service(),
            stats_service=stats_service,
        )
        self.dialog.set_presenter(presenter)
        presenter.load_data("1", "Page A", "tok")
        self.dialog.videos_ready.emit([{"id": "v1", "link": "https://x", "views": 3, "created_time": "2026-01-01"}])
        self.assertEqual(received["args"], ("1", "tok"))
        self.assertEqual(self.dialog.video_table.rowCount(), 1)

    @staticmethod
    def dialog_presenter_service():
        class FakeDetailService:
            def load_latest(self, page_id):
                return {"page_id": page_id, "name": "Page A", "followers_count": 10}

        return FakeDetailService()


if __name__ == "__main__":
    unittest.main()
