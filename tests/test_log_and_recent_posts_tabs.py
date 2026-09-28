"""Test tab Log và tab Lịch sử đăng: wire-up dữ liệu, phân trang và xoá.

Hai tab này trước đây bị "treo" — không có ai nạp dữ liệu vào. Các test dưới
đây bảo đảm: log của luồng đăng tay + scheduler hiển thị lên tab Log, và các
bài đăng thành công đã lưu DB hiện ra bảng Lịch sử đăng (kèm phân trang/xoá).
"""
import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

try:
    from di.container import Container
    from models.database import DatabaseManager
    from presenters.main_presenter import MainPresenter
    from views.log_tab import LogTab
    from views.main_window import FacebookPageManagerWindow
    from views.recent_posts_tab import RecentPostsTab
    IMPORTS_OK = True
except Exception as exc:  # pragma: no cover
    IMPORTS_OK = False
    IMPORT_ERROR = exc


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class LogTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_append_log_thêm_dòng_có_mức(self):
        tab = LogTab()
        tab.append_log("Đang đăng video", "info")
        tab.append_log("Lỗi API", "error")
        text = tab.log_output.toPlainText()
        self.assertIn("[INFO] Đang đăng video", text)
        self.assertIn("[ERROR] Lỗi API", text)

    def test_append_log_mức_không_biết_thì_mặc_định_info(self):
        tab = LogTab()
        tab.append_log("hehe", "debug")
        self.assertIn("[INFO] hehe", tab.log_output.toPlainText())

    def test_load_history_nạp_tail_file_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "facebook_scraper.log"
            lines = [f"2026-09-28 08:00:00 - INFO - dong {i}" for i in range(10)]
            log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            tab = LogTab()
            tab.load_history(log_file, max_lines=4)
            text = tab.log_output.toPlainText()
            # Chỉ 4 dòng cuối được nạp.
            self.assertEqual(len(text.splitlines()), 4)
            self.assertIn("dong 6", text)
            self.assertIn("dong 9", text)
            self.assertNotIn("dong 0", text)

    def test_load_history_không_lỗi_khi_file_không_tồn_tại(self):
        tab = LogTab()
        tab.load_history("khong-co-file-this.log")  # không ném exception
        self.assertEqual(tab.log_output.toPlainText(), "")


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class LogAndRecentPostsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.container = Container(Path(self._tmp.name))
        self.window = FacebookPageManagerWindow(self.container)
        self.presenter = MainPresenter(self.window._container, self.window)
        self.window.set_presenter(self.presenter)
        self.window.show_info = self.window.show_warning = self.window.show_error = lambda *a, **k: None
        self.window.confirm = lambda *a, **k: True

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass
        gc.collect()
        self._tmp.cleanup()

    # ── Tab Log ────────────────────────────────────────────────────

    def test_log_từ_nút_đăng_hiện_trên_tab_log(self):
        self.window.log("Bắt đầu đăng 2 dòng trong hàng đợi", "info")
        text = self.window.log_tab.log_output.toPlainText()
        self.assertIn("[INFO] Bắt đầu đăng 2 dòng trong hàng đợi", text)

    def test_log_từ_thread_ngoài_qua_signal_vẫn_vào_tab(self):
        # Emit trực tiếp signal y như worker thread gọi log() — với cửa sổ
        # đang ở GUI thread thì connection trực tiếp chạy đồng bộ.
        self.window.log_message.emit("Page A: Đang đăng - Đang gửi yêu cầu tới Facebook", "info")
        self.assertIn("Page A: Đang đăng", self.window.log_tab.log_output.toPlainText())

    def test_on_post_status_đẩy_status_vào_tab_log(self):
        self.presenter.config._on_post_status("Page A", "Thành công", "Đăng thành công")
        self.assertIn("[INFO] Page A: Thành công - Đăng thành công", self.window.log_tab.log_output.toPlainText())

        self.presenter.config._on_post_status("Page B", "Thất bại", "Lỗi API")
        self.assertIn("[ERROR] Page B: Thất bại - Lỗi API", self.window.log_tab.log_output.toPlainText())

    def test_on_posting_complete_ghi_log_tổng_kết(self):
        self.window._on_posting_complete(2, 1)
        self.assertIn("[WARN] Đăng xong: thành công 2, thất bại 1", self.window.log_tab.log_output.toPlainText())

    # ── Tab Lịch sử đăng ───────────────────────────────────────────

    def test_lịch_sử_đăng_hiện_các_bài_đã_lưu(self):
        self.container.db.log_successful_post("Page A", "Video so 1", "video")
        self.container.db.log_successful_post("Page B", "Reel moi", "video")

        self.window._on_load_recent_posts_page(1)
        table = self.window.recent_posts_tab.table
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(0, 0).text(), "Page B")  # mới nhất lên đầu
        self.assertEqual(table.item(0, 1).text(), "Reel moi")
        self.assertIn("1/1", self.window.recent_posts_tab.page_label.text())

    def test_phân_trang_lịch_sử_đăng(self):
        for i in range(25):
            self.container.db.log_successful_post(f"Page {i}", f"Noi dung {i}", "video")

        tab = self.window.recent_posts_tab
        self.window._on_load_recent_posts_page(1)
        self.assertEqual(tab.table.rowCount(), 20)
        self.assertFalse(tab.prev_btn.isEnabled())
        self.assertTrue(tab.next_btn.isEnabled())
        self.assertEqual(tab.recent_page, 1)

        tab.go_next()  # nút kế -> nạp trang 2
        self.assertEqual(tab.table.rowCount(), 5)
        self.assertTrue(tab.prev_btn.isEnabled())
        self.assertFalse(tab.next_btn.isEnabled())
        self.assertIn("2/2", tab.page_label.text())

        tab.go_prev()
        self.assertEqual(tab.table.rowCount(), 20)

    def test_xoá_lịch_sử_đăng(self):
        self.container.db.log_successful_post("Page A", "Video", "video")
        self.window._on_load_recent_posts_page(1)
        self.assertEqual(self.window.recent_posts_tab.table.rowCount(), 1)

        self.window.recent_posts_tab.clear_btn.click()
        self.assertEqual(self.window.recent_posts_tab.table.rowCount(), 0)
        self.assertEqual(self.container.db.load_recent_posts(0, 100)[1], 0)

    def test_chuyển_đến_tab_lịch_sử_thì_tự_nạp_lại(self):
        self.container.db.log_successful_post("Page A", "Video", "video")
        index = self.window.tabs.indexOf(self.window.recent_posts_tab)
        self.window.tabs.setCurrentIndex(index)  # -> _on_tab_changed nạp dữ liệu
        self.assertEqual(self.window.recent_posts_tab.table.rowCount(), 1)


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class RecentPostsTabWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_tab_phân_trang_nội_bộ(self):
        rows = [{"page_name": f"Page {i}", "content": f"c{i}", "post_type": "video", "posted_at": ""} for i in range(25)]
        tab = RecentPostsTab()
        tab.load_page(rows[:20], 25, 1)
        self.assertEqual(tab.table.rowCount(), 20)
        self.assertEqual(tab.recent_total_pages, 2)
        self.assertFalse(tab.prev_btn.isEnabled())
        self.assertTrue(tab.next_btn.isEnabled())

        tab.load_page(rows[20:], 25, 2)
        self.assertEqual(tab.table.rowCount(), 5)
        self.assertTrue(tab.prev_btn.isEnabled())
        self.assertFalse(tab.next_btn.isEnabled())

    def test_callback_phân_trang_được_nối(self):
        seen = []
        tab = RecentPostsTab()
        tab.set_load_callback(lambda page: seen.append(page))
        tab.recent_page = 2
        tab.go_next()
        tab.go_prev()
        self.assertEqual(seen, [3, 1])


if __name__ == "__main__":
    unittest.main()