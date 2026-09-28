"""Test áp dụng nội dung chung vào hàng đợi + link sau khi đăng."""
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
    from views.main_window import (
        FacebookPageManagerWindow,
        CONFIG_TITLE_COL,
        CONFIG_DESCRIPTION_COL,
        CONFIG_COMMENT_COL,
        CONFIG_STATUS_COL,
        CONFIG_LINK_COL,
    )
    IMPORTS_OK = True
except Exception as exc:  # pragma: no cover
    IMPORTS_OK = False
    IMPORT_ERROR = exc


def _entries():
    return [
        {
            "page_id": "1", "page_name": "Page A", "video_path": r"C:\v\video_abc.mp4",
            "title": "video_abc", "description": "", "comment": "", "comment_images": "",
            "post_type": "video", "schedule_time": "", "status": "Chờ đăng",
        },
        {
            "page_id": "2", "page_name": "Page B", "video_path": r"C:\v\video_xyz.mp4",
            "title": "video_xyz", "description": "", "comment": "", "comment_images": "",
            "post_type": "video", "schedule_time": "", "status": "Chờ đăng",
        },
    ]


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class QueueContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = FacebookPageManagerWindow(Container(PROJECT))
        presenter = MainPresenter(self.window._container, self.window)
        self.window.set_presenter(presenter)
        self.window.show_info = self.window.show_warning = self.window.show_error = lambda *a, **k: None
        presenter.config.populate_assignment_rows(_entries())

    def _cell(self, row, col):
        return self.window.config_table.item(row, col).text()

    # ── 1. Nội dung bài đăng làm title, không nhân bản sang mô tả/comment ──
    def test_apply_post_content_only_sets_title(self):
        self.window.quick_post_content_input.setPlainText("KHUYEN MAI 50% OFF")
        self.window.quick_comment_content_input.setPlainText("")
        self.window._on_apply_quick_content()

        self.assertEqual(self._cell(0, CONFIG_TITLE_COL), "KHUYEN MAI 50% OFF")
        self.assertEqual(self._cell(1, CONFIG_TITLE_COL), "KHUYEN MAI 50% OFF")
        self.assertEqual(self._cell(0, CONFIG_DESCRIPTION_COL), "")
        self.assertEqual(self._cell(0, CONFIG_COMMENT_COL), "")

    # ── 2. Nội dung comment chỉ đi vào cột Comment ──
    def test_apply_comment_goes_to_comment_column_only(self):
        self.window.quick_post_content_input.setPlainText("Bai dang")
        self.window.quick_comment_content_input.setPlainText("Comment chung")
        self.window._on_apply_quick_content()

        self.assertEqual(self._cell(0, CONFIG_TITLE_COL), "Bai dang")
        self.assertEqual(self._cell(0, CONFIG_DESCRIPTION_COL), "")
        self.assertEqual(self._cell(0, CONFIG_COMMENT_COL), "Comment chung")

    # ── 3. Bỏ trống nội dung comment thì xoá comment do áp dụng trước đó ──
    def test_clearing_comment_input_clears_auto_comment(self):
        self.window.quick_post_content_input.setPlainText("Bai dang")
        self.window.quick_comment_content_input.setPlainText("Comment chung")
        self.window._on_apply_quick_content()
        self.assertEqual(self._cell(0, CONFIG_COMMENT_COL), "Comment chung")

        self.window.quick_comment_content_input.setPlainText("")
        self.window._on_apply_quick_content()
        self.assertEqual(self._cell(0, CONFIG_COMMENT_COL), "")

    # ── 4. Comment người dùng tự gõ không bị xoá ──
    def test_manual_comment_is_kept(self):
        self.window.config_table.item(0, CONFIG_COMMENT_COL).setText("Comment tay")
        self.window.quick_post_content_input.setPlainText("Bai dang")
        self.window._on_apply_quick_content()
        self.assertEqual(self._cell(0, CONFIG_COMMENT_COL), "Comment tay")

    # ── 5. Tiêu đề tự sửa tay không bị đồng bộ ghi đè ──
    def test_manual_title_is_kept_by_sync(self):
        self.window.config_table.item(1, CONFIG_TITLE_COL).setText("Tieu de tay sua")
        self.window.quick_post_content_input.setPlainText("Bai dang")
        changed = self.window._sync_queue_titles()
        self.assertEqual(self._cell(1, CONFIG_TITLE_COL), "Tieu de tay sua")
        self.assertEqual(changed, 1)  # chỉ dòng A còn tên video nên bị thay

    # ── 6. Không có nội dung bài đăng -> title lấy tên file video ──
    def test_title_falls_back_to_video_name(self):
        self.window.config_table.item(0, CONFIG_TITLE_COL).setText("")
        self.window.quick_post_content_input.setPlainText("")
        self.window._sync_queue_titles()
        self.assertEqual(self._cell(0, CONFIG_TITLE_COL), "video_abc")

    # ── 7. Link xuất hiện sau khi đăng thành công ──
    def test_link_filled_after_success(self):
        self.window.update_config_status("Page A", "Thành công")
        self.window.update_config_link("Page A", "https://www.facebook.com/reel/123")
        self.app.processEvents()

        item = self.window.config_table.item(0, CONFIG_LINK_COL)
        self.assertEqual(item.text(), "https://www.facebook.com/reel/123")
        self.assertEqual(self._cell(0, CONFIG_STATUS_COL), "Thành công")

    # ── 8. Không có link -> cột Link để trống ──
    def test_empty_link_not_written(self):
        self.window.update_config_link("Page B", "")
        self.app.processEvents()
        self.assertEqual(self._cell(1, CONFIG_LINK_COL), "")

    # ── 8b. Link phải ghi đúng dòng khi 2 page trùng tên ──
    def test_link_matched_by_page_id_not_name(self):
        window2 = FacebookPageManagerWindow(self.window._container)
        presenter2 = MainPresenter(window2._container, window2)
        window2.set_presenter(presenter2)
        presenter2.config.populate_assignment_rows([
            {"page_id": "111", "page_name": "Ha", "video_path": r"C:\v\a.mp4", "title": "T1",
             "description": "", "comment": "", "comment_images": "", "post_type": "video",
             "schedule_time": "", "status": "Chờ đăng"},
            {"page_id": "222", "page_name": "Ha", "video_path": r"C:\v\b.mp4", "title": "T2",
             "description": "", "comment": "", "comment_images": "", "post_type": "video",
             "schedule_time": "", "status": "Chờ đăng"},
        ])
        window2.update_config_link("Ha", "https://www.facebook.com/reel/222", "222")
        self.app.processEvents()

        self.assertEqual(window2.config_table.item(0, CONFIG_LINK_COL).text(), "")
        self.assertEqual(
            window2.config_table.item(1, CONFIG_LINK_COL).text(),
            "https://www.facebook.com/reel/222",
        )
        window2.close()

    # ── 9. Hàng đợi trống -> cảnh báo, không crash ──
    def test_apply_on_empty_queue_warns(self):
        warned = []
        self.window.show_warning = lambda title, msg: warned.append(title)
        self.window.config_table.setRowCount(0)
        self.window.quick_post_content_input.setPlainText("Bai dang")
        self.window._on_apply_quick_content()
        self.assertTrue(warned)


if __name__ == "__main__":
    unittest.main()
