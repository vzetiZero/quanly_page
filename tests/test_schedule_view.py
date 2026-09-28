"""Test phần giao diện của lịch đăng: nút, cột giờ, dialog và cảnh báo khi đóng app."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

try:
    from di.container import Container
    from presenters.main_presenter import MainPresenter
    from presenters.schedule_presenter import STATUS_PENDING_LABEL
    from services.schedule_service import MODE_SLOTS
    from views.main_window import (
        CONFIG_SCHEDULE_COL,
        CONFIG_STATUS_COL,
        FacebookPageManagerWindow,
    )
    from views.schedule_dialog import SchedulePlanDialog
    IMPORTS_OK = True
except Exception as exc:  # pragma: no cover
    IMPORTS_OK = False
    IMPORT_ERROR = exc


TIME_RE = __import__("re").compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$")


@unittest.skipUnless(IMPORTS_OK, f"khong import duoc giao dien: {IMPORT_ERROR if not IMPORTS_OK else ''}")
class ScheduleViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.container = Container(PROJECT)
        self.window = FacebookPageManagerWindow(self.container)
        self.presenter = MainPresenter(self.window._container, self.window)
        self.window.set_presenter(self.presenter)
        self.window.show_info = self.window.show_warning = self.window.show_error = lambda *a, **k: None
        self.warnings = []
        self.window.show_warning = lambda title, message: self.warnings.append((title, message))
        self.window.confirm = lambda *a, **k: True

        self.service = self.container.schedule_service
        self.service.stop()
        self.service.clear_pending()
        self.presenter.schedule._set_auto_enabled(False)

    def _add_queue_row(self, page_id, page_name, video, schedule_time=""):
        self.window.add_config_row({
            "page_id": page_id,
            "page_name": page_name,
            "video_path": video,
            "title": "Tieu de",
            "schedule_time": schedule_time,
        })

    # ── Nút & nhãn ─────────────────────────────────────────────────

    def test_có_đủ_nút_điều_khiển_lịch(self):
        for button in (
            self.window.schedule_batch_btn,
            self.window.schedule_auto_btn,
            self.window.schedule_clear_btn,
        ):
            self.assertIsNotNone(button)
            self.assertTrue(button.text())
        self.assertIn(self.window.schedule_batch_btn, self.window.config_tab.findChildren(QtWidgets.QPushButton))

    def test_nhãn_thanh_trạng_bám_theo_trạng_thái(self):
        self.window.update_schedule_info(0, False, False)
        self.assertEqual(self.window.schedule_info_label.text(), "Chưa có lịch chờ")
        self.assertEqual(self.window.schedule_auto_btn.text(), "Bật lịch tự động")

        self.window.update_schedule_info(5, True, False)
        self.assertIn("5 lịch chờ", self.window.schedule_info_label.text())
        self.assertIn("đang chạy nền", self.window.schedule_info_label.text())
        self.assertEqual(self.window.schedule_auto_btn.text(), "Tắt lịch tự động")

        self.window.update_schedule_info(5, True, True)
        self.assertIn("đang đăng", self.window.schedule_info_label.text())

    # ── Cột "Thời gian đăng" ───────────────────────────────────────

    def test_giờ_hiển_thị_dạng_ngày_giờ_nhưng_đọc_lại_ra_iso(self):
        moment = datetime(2026, 9, 28, 14, 30)
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4", moment.isoformat())
        cell = self.window.config_table.item(0, CONFIG_SCHEDULE_COL)
        self.assertTrue(TIME_RE.match(cell.text()), cell.text())
        self.assertEqual(self.window.get_config_rows()[0]["schedule_time"], moment.isoformat())

    def test_sửa_tay_giờ_thì_dùng_đúng_người_dùng_gõ(self):
        moment = datetime(2026, 9, 28, 14, 30)
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4", moment.isoformat())
        self.window.config_table.item(0, CONFIG_SCHEDULE_COL).setText("01/01/2030 08:00")
        self.assertEqual(self.window.get_config_rows()[0]["schedule_time"], "01/01/2030 08:00")

    def test_apply_schedule_times_khớp_đúng_dòng_theo_page_id(self):
        # 2 page trùng tên nhưng khác id -> phải gán đúng dòng.
        self._add_queue_row("111", "Ha", r"C:\v\a.mp4")
        self._add_queue_row("222", "Ha", r"C:\v\b.mp4")
        moment = datetime(2026, 9, 28, 9, 0).isoformat()
        self.window.apply_schedule_times([{"page_id": "222", "page_name": "Ha", "schedule_time": moment}])

        self.assertEqual(self.window.config_table.item(0, CONFIG_SCHEDULE_COL).text(), "")
        self.assertEqual(self.window.config_table.item(1, CONFIG_SCHEDULE_COL).text(), "28/09/2026 09:00")

    def test_apply_schedule_times_giữ_màu_ô(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.apply_schedule_times([{
            "page_id": "111", "page_name": "Page A",
            "schedule_time": datetime(2026, 9, 28, 9, 0).isoformat(),
        }])
        color = self.window.config_table.item(0, CONFIG_SCHEDULE_COL).foreground().color().name()
        self.assertEqual(color, "#1d4ed8")

    # ── Trạng thái chờ đến giờ ──────────────────────────────────────

    def test_trạng_thái_chờ_đến_giờ_được_tô_màu(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4", "2026-09-28T09:00:00")
        self.window._set_table_value(0, CONFIG_STATUS_COL, STATUS_PENDING_LABEL)
        self.window._apply_config_row_color(0, STATUS_PENDING_LABEL)
        item = self.window.config_table.item(0, CONFIG_STATUS_COL)
        self.assertEqual(item.foreground().color().name(), "#1d4ed8")

    # ── Presenter ──────────────────────────────────────────────────

    def test_plan_batch_khi_chưa_có_video_thì_cảnh_báo(self):
        self.window.add_config_row({"page_name": "Page A", "video_path": ""})
        self.presenter.schedule.plan_batch()
        self.assertTrue(self.warnings)
        self.assertEqual(self.service.pending_count(), 0)

    def test_plan_batch_ghi_lịch_ra_bảng_và_thanh_trạng(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self._add_queue_row("222", "Page B", r"C:\v\b.mp4")
        start = datetime.now() + timedelta(hours=1)
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": start, "interval_minutes": 30,
            "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()

        self.assertEqual(self.service.pending_count(), 2)
        self.assertTrue(TIME_RE.match(self.window.config_table.item(0, CONFIG_SCHEDULE_COL).text()))
        self.assertIn("2 lịch chờ", self.window.schedule_info_label.text())
        # Hai dòng phải khác giờ nhau.
        self.assertNotEqual(
            self.window.config_table.item(0, CONFIG_SCHEDULE_COL).text(),
            self.window.config_table.item(1, CONFIG_SCHEDULE_COL).text(),
        )

    def test_toggle_auto_bật_tắt_và_ghi_nhớ(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": datetime.now() + timedelta(days=1),
            "interval_minutes": 60, "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()

        self.presenter.schedule.toggle_auto()
        self.assertTrue(self.presenter.schedule.is_running())
        self.assertEqual(self.container.db.get_setting("schedule_auto_start", "0"), "1")

        self.presenter.schedule.toggle_auto()
        self.assertFalse(self.presenter.schedule.is_running())

        # Bấm "Dừng" thì không tự bật lại lần sau.
        self.presenter.schedule.toggle_auto()
        self.presenter.on_stop()
        self.assertFalse(self.presenter.schedule.is_running())
        self.assertEqual(self.container.db.get_setting("schedule_auto_start", "0"), "0")

    def test_clear_pending_xoá_đúng_lịch_chờ(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": datetime.now() + timedelta(days=1),
            "interval_minutes": 60, "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()
        self.assertEqual(self.service.pending_count(), 1)
        self.presenter.schedule.clear_pending()
        self.assertEqual(self.service.pending_count(), 0)
        self.assertIn("Chưa có lịch chờ", self.window.schedule_info_label.text())

    def test_chưa_qua_bản_quyền_thì_không_tự_bật_lịch(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": datetime.now() + timedelta(days=1),
            "interval_minutes": 60, "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()
        self.presenter.schedule._set_auto_enabled(True)
        try:
            # on_app_start chỉ nạp lịch, KHÔNG bật scheduler.
            self.presenter.schedule.on_app_start()
            self.assertFalse(self.presenter.schedule.is_running())
            # Chỉ chạy khi app đã qua kiểm tra bản quyền.
            self.presenter.schedule.start_auto_if_enabled()
            self.assertTrue(self.presenter.schedule.is_running())
        finally:
            self.presenter.schedule.stop(remember=False)
            self.presenter.schedule._set_auto_enabled(False)

    def test_hết_hạn_giữa_chừng_thì_dừng_lịch(self):
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": datetime.now() + timedelta(days=1),
            "interval_minutes": 60, "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()
        self.presenter.schedule._start_background()
        self.assertTrue(self.presenter.schedule.is_running())
        self.presenter._lock_trial_expired()
        self.assertFalse(self.presenter.schedule.is_running())
        # Lịch chờ vẫn còn nguyên để mở lại app là tiếp.
        self.assertEqual(self.service.pending_count(), 1)

    # ── Dialog ─────────────────────────────────────────────────────

    def test_dialog_giãn_cách(self):
        dialog = SchedulePlanDialog(3, self.window)
        # Ô bắt đầu chặn không cho chọn quá khứ -> dùng thời điểm tương lai.
        start = (datetime.now() + timedelta(days=3)).replace(second=0, microsecond=0)
        dialog.mode_interval_radio.setChecked(True)
        dialog.interval_spin.setValue(45)
        dialog.start_at_edit.setDateTime(start)
        plan = dialog.current_plan()
        self.assertEqual(plan["mode"], "interval")
        self.assertEqual(plan["interval_minutes"], 45)
        self.assertEqual(plan["start_at"], start)

    def test_dialog_khung_giờ_thêm_xoá(self):
        dialog = SchedulePlanDialog(4, self.window)
        dialog.mode_slots_radio.setChecked(True)
        dialog._on_clear_times()
        self.assertEqual(dialog.current_plan()["times_of_day"], [])

        dialog.time_edit.setTime(QtCore.QTime(9, 0))
        dialog._on_add_time()
        dialog.time_edit.setTime(QtCore.QTime(20, 30))
        dialog._on_add_time()
        # Thêm trùng -> không nhân bản.
        dialog.time_edit.setTime(QtCore.QTime(9, 0))
        dialog._on_add_time()
        self.assertEqual(dialog.current_plan()["times_of_day"], ["09:00", "20:30"])

        dialog.slots_table.selectRow(0)
        dialog._on_remove_time()
        self.assertEqual(dialog.current_plan()["times_of_day"], ["20:30"])

        dialog.daily_limit_spin.setValue(1)
        self.assertEqual(dialog.current_plan()["daily_limit"], 1)
        dialog.close()

    def test_dialog_báo_khi_không_đủ_khung_giờ(self):
        # 1 khung giờ/ngày mà cần 500 video -> vượt tầm 366 ngày -> phải báo.
        dialog = SchedulePlanDialog(500, self.window)
        dialog.mode_slots_radio.setChecked(True)
        dialog._on_clear_times()
        dialog.time_edit.setTime(QtCore.QTime(9, 0))
        dialog._on_add_time()
        self.assertIn("500", dialog.preview_label.text())
        self.assertIn("Chỉ tạo được", dialog.preview_label.text())
        dialog.close()

    def test_dialog_chế_độ_khung_giờ_tắt_ô_giãn_cách(self):
        dialog = SchedulePlanDialog(2, self.window)
        dialog.mode_slots_radio.setChecked(True)
        self.assertFalse(dialog.interval_spin.isEnabled())
        self.assertTrue(dialog.slots_widget.isEnabled())
        dialog.mode_interval_radio.setChecked(True)
        self.assertTrue(dialog.interval_spin.isEnabled())
        self.assertFalse(dialog.slots_widget.isEnabled())
        dialog.close()

    # ── Đóng app ───────────────────────────────────────────────────

    def test_đóng_app_có_hỏi_khi_còn_lịch_chờ(self):
        asked = []
        self.window.confirm = lambda title, message: asked.append((title, message)) or False
        self._add_queue_row("111", "Page A", r"C:\v\a.mp4")
        self.window.ask_schedule_plan = lambda count: {
            "mode": "interval", "start_at": datetime.now() + timedelta(days=1),
            "interval_minutes": 60, "times_of_day": [], "daily_limit": 0,
        }
        self.presenter.schedule.plan_batch()

        self.window.close()
        self.assertTrue(asked, "phai hoi nguoi dung khi con lich cho")
        self.assertIn("1 video", asked[0][1])

    def test_đóng_app_không_hỏi_khi_hết_lịch(self):
        asked = []
        self.window.confirm = lambda title, message: asked.append((title, message)) or False
        self.window.close()
        self.assertFalse(asked)


if __name__ == "__main__":
    unittest.main()
