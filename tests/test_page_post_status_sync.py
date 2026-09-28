"""Test đồng bộ trạng thái đăng sang bảng page + ghi thời gian đăng ở hàng đợi.

Bắt được lỗi thật: trạng thái trước đây khớp dòng hàng đợi theo **tên** page,
nên 2 page trùng tên sẽ cập nhật nhầm trạng thái của nhau.
"""
import os
import re
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

try:
    from di.container import Container
    from presenters.main_presenter import MainPresenter
    from views.main_window import (
        FacebookPageManagerWindow,
        CONFIG_STATUS_COL,
        CONFIG_POSTED_AT_COL,
    )
    from views.widgets import PAGE_COL_POST_STATUS, PAGE_COL_SELECT
    IMPORTS_OK = True
except Exception as exc:  # pragma: no cover
    IMPORTS_OK = False
    IMPORT_ERROR = exc


TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2} \d{2}/\d{2}/\d{4}$")


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class PagePostStatusSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = FacebookPageManagerWindow(Container(PROJECT))
        self.presenter = MainPresenter(self.window._container, self.window)
        self.window.set_presenter(self.presenter)
        self.window.show_info = self.window.show_warning = self.window.show_error = lambda *a, **k: None

        # 3 page; 2 page trùng tên "Ha" nhưng khác page_id
        self.presenter.page_list.pages = [
            {"id": "111", "name": "Ha", "account_token": "T1", "account_label": "Acc1",
             "access_token": "tok_A", "status": "Valid", "tasks": ["CREATE_CONTENT"]},
            {"id": "222", "name": "Ha", "account_token": "T2", "account_label": "Acc2",
             "access_token": "tok_B", "status": "Valid", "tasks": ["CREATE_CONTENT"]},
            {"id": "1290184884184308", "name": "Page X", "account_token": "T1", "account_label": "Acc1",
             "access_token": "tok_C", "status": "Valid", "tasks": ["CREATE_CONTENT"]},
        ]
        self.window.populate_page_table(self.presenter.page_list.pages)

        self.presenter.config.populate_assignment_rows([
            {"page_id": "111", "page_name": "Ha", "video_path": r"C:\v\a.mp4", "title": "T1",
             "description": "", "comment": "", "comment_images": "", "post_type": "video",
             "schedule_time": "", "status": "Chờ đăng"},
            {"page_id": "222", "page_name": "Ha", "video_path": r"C:\v\b.mp4", "title": "T2",
             "description": "", "comment": "", "comment_images": "", "post_type": "video",
             "schedule_time": "", "status": "Chờ đăng"},
            {"page_id": "1290184884184308", "page_name": "Page X", "video_path": r"C:\v\c.mp4",
             "title": "T3", "description": "", "comment": "", "comment_images": "",
             "post_type": "video", "schedule_time": "", "status": "Chờ đăng"},
        ])

    def _queue_status(self, row):
        return self.window.config_table.item(row, CONFIG_STATUS_COL).text()

    def _queue_posted_at(self, row):
        item = self.window.config_table.item(row, CONFIG_POSTED_AT_COL)
        return item.text() if item else ""

    def _page_status(self, page_id):
        """Ô 'Trang thai dang' cua page theo id trong bang page."""
        for row in range(self.window.page_table.rowCount()):
            key = str(self.window.page_table.item(row, PAGE_COL_SELECT).data(QtCore.Qt.UserRole) or "")
            page = self.presenter.page_list.get_page_by_key(key)
            if page and str(page.get("id")) == page_id:
                return self.window.page_table.item(row, PAGE_COL_POST_STATUS)
        return None

    # ── 1. Trạng thái đăng ở bảng page phải đổi khi đăng thành công ──
    def test_page_status_updates_after_success(self):
        self.assertEqual(self._queue_status(0), "Chờ đăng")
        self.assertEqual(self._page_status("111").text(), "Chưa đăng")

        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()

        self.assertEqual(self._page_status("111").text(), "Thành công")
        self.assertEqual(self._page_status("1290184884184308").text(), "Chưa đăng")

    def test_same_name_pages_are_not_mixed_up(self):
        """2 page trùng tên 'Ha' nhưng khác id: trạng thái phải tách biệt."""
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()

        self.assertEqual(self._queue_status(0), "Thành công")
        self.assertEqual(self._queue_status(1), "Chờ đăng", "page 222 bi nham trang thai cua page 111")
        self.assertEqual(self._page_status("111").text(), "Thành công")
        self.assertEqual(self._page_status("222").text(), "Chưa đăng",
                         "page 222 bi nham trang thai cua page 111")

    def test_failure_only_affects_its_own_page(self):
        self.window.update_config_status("Ha", "Thành công", "111")
        self.window.update_config_status("Ha", "Thất bại", "222")
        self.app.processEvents()

        self.assertEqual(self._queue_status(0), "Thành công")
        self.assertEqual(self._queue_status(1), "Thất bại")
        self.assertEqual(self._page_status("111").text(), "Thành công")
        self.assertEqual(self._page_status("222").text(), "Thất bại")

    def test_fallback_to_name_when_no_id(self):
        """Không có page_id thì vẫn khớp theo tên (page cũ trong DB)."""
        self.window.update_config_status("Page X", "Thành công")
        self.app.processEvents()
        self.assertEqual(self._queue_status(2), "Thành công")
        self.assertEqual(self._page_status("1290184884184308").text(), "Thành công")

    def test_unknown_page_name_does_nothing(self):
        self.window.update_config_status("Khong Ton Tai", "Thành công")
        self.app.processEvents()
        for row in range(3):
            self.assertEqual(self._queue_status(row), "Chờ đăng")

    def test_all_three_pages_success_sync(self):
        for page_id, name in (("111", "Ha"), ("222", "Ha"), ("1290184884184308", "Page X")):
            self.window.update_config_status(name, "Thành công", page_id)
        self.app.processEvents()
        for pid in ("111", "222", "1290184884184308"):
            self.assertEqual(self._page_status(pid).text(), "Thành công", f"page {pid} chua dong bo")

    # ── 2. Màu trạng thái phải đúng (tiếng Việt) ──
    def test_success_status_is_green(self):
        item = self._page_status("111")
        self.window.set_page_post_status(
            self.presenter.page_list.page_key(self.presenter.page_list.pages[0]), "Thành công"
        )
        self.assertEqual(item.text(), "Thành công")
        self.assertEqual(item.foreground().color().name(), "#15803d")
        self.assertEqual(item.background().color().name(), "#dcfce7")

    def test_failed_status_is_red(self):
        item = self._page_status("222")
        self.window.set_page_post_status(
            self.presenter.page_list.page_key(self.presenter.page_list.pages[1]), "Thất bại"
        )
        self.assertEqual(item.text(), "Thất bại")
        self.assertEqual(item.foreground().color().name(), "#b91c1c")

    def test_pending_status_is_blue(self):
        self.window._mark_queue_pending()
        item = self._page_status("111")
        self.assertEqual(item.text(), "Chờ xử lý")
        self.assertEqual(item.foreground().color().name(), "#1d4ed8")
        self.assertEqual(self._queue_status(0), "Chờ xử lý")

    def test_not_posted_stays_grey(self):
        item = self._page_status("1290184884184308")
        self.assertEqual(item.text(), "Chưa đăng")
        self.assertEqual(item.foreground().color().name(), "#64748b")

    def test_page_table_repaint_keeps_status(self):
        """Vẽ lại bảng page không được mất trạng thái đã đánh dấu."""
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()
        self.window.populate_page_table(self.presenter.page_list.pages)
        self.assertEqual(self._page_status("111").text(), "Thành công")
        self.assertEqual(self._page_status("222").text(), "Chưa đăng")

    # ── 3. Ghi thời gian đăng ở hàng đợi ──
    def test_posted_at_written_on_success(self):
        self.assertEqual(self._queue_posted_at(0), "")
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()
        self.assertTrue(TIME_RE.match(self._queue_posted_at(0)), self._queue_posted_at(0))
        self.assertEqual(self._queue_posted_at(1), "", "page chua dang khong duoc co thoi gian")
        self.assertEqual(self._queue_posted_at(2), "")

    def test_posted_at_not_written_on_failure(self):
        self.window.update_config_status("Ha", "Thất bại", "111")
        self.app.processEvents()
        self.assertEqual(self._queue_posted_at(0), "")

    def test_posted_at_roundtrip(self):
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()
        rows = self.window.get_config_rows()
        self.assertTrue(TIME_RE.match(rows[0]["posted_at"]), rows[0]["posted_at"])

    def test_failure_keeps_previous_posted_at(self):
        self.window.update_config_status("Ha", "Thành công", "222")
        self.app.processEvents()
        first = self._queue_posted_at(1)
        self.window._apply_config_status_by_id("222", "Thất bại")
        self.app.processEvents()
        self.assertEqual(self._queue_posted_at(1), first)

    def test_posted_at_not_overwritten_on_repost_success(self):
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()
        first = self._queue_posted_at(0)
        self.window.update_config_status("Ha", "Đang đăng", "111")
        self.window.update_config_status("Ha", "Thành công", "111")
        self.app.processEvents()
        self.assertEqual(self._queue_posted_at(0), first)


if __name__ == "__main__":
    unittest.main()
