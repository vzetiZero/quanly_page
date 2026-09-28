from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from PyQt5 import QtCore, QtWidgets

from views.widgets import apply_small_button_style


class QuickConfigTab(QtWidgets.QWidget):
    schedule_changed = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        quick_group = QtWidgets.QGroupBox("Cấu hình nhanh cho toàn bộ page")
        quick_layout = QtWidgets.QVBoxLayout(quick_group)

        desc = QtWidgets.QLabel("Dùng khi nội dung đăng và nội dung comment giống nhau cho toàn bộ page.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        quick_layout.addWidget(desc)

        quick_layout.addWidget(QtWidgets.QLabel("Nội dung bài đăng chung:"))
        self.post_content_input = QtWidgets.QPlainTextEdit()
        self.post_content_input.setPlaceholderText("Nội dung bài đăng chung")
        self.post_content_input.setMaximumHeight(100)
        quick_layout.addWidget(self.post_content_input)

        quick_layout.addWidget(QtWidgets.QLabel("Nội dung comment chung:"))
        self.comment_content_input = QtWidgets.QPlainTextEdit()
        self.comment_content_input.setPlaceholderText("Nội dung comment chung")
        self.comment_content_input.setMaximumHeight(100)
        quick_layout.addWidget(self.comment_content_input)

        file_group = QtWidgets.QGroupBox("Chọn file media")
        file_layout = QtWidgets.QGridLayout(file_group)
        file_layout.setSpacing(8)

        file_layout.addWidget(QtWidgets.QLabel("Thư mục video:"), 0, 0)
        self.video_folder_input = QtWidgets.QLineEdit()
        self.video_folder_input.setPlaceholderText("Chọn thư mục chứa video")
        file_layout.addWidget(self.video_folder_input, 0, 1)
        self.select_video_folder_btn = QtWidgets.QPushButton("Chọn")
        apply_small_button_style(self.select_video_folder_btn)
        file_layout.addWidget(self.select_video_folder_btn, 0, 2)

        file_layout.addWidget(QtWidgets.QLabel("Thư mục ảnh comment:"), 1, 0)
        self.comment_image_folder_input = QtWidgets.QLineEdit()
        self.comment_image_folder_input.setPlaceholderText("Chọn thư mục chứa ảnh comment")
        file_layout.addWidget(self.comment_image_folder_input, 1, 1)
        self.select_comment_image_folder_btn = QtWidgets.QPushButton("Chọn")
        apply_small_button_style(self.select_comment_image_folder_btn)
        file_layout.addWidget(self.select_comment_image_folder_btn, 1, 2)

        self.skip_missing_checkbox = QtWidgets.QCheckBox("Bỏ qua page khi video không đủ")
        self.skip_missing_checkbox.setChecked(True)
        file_layout.addWidget(self.skip_missing_checkbox, 2, 0, 1, 3)

        self.use_comment_images_checkbox = QtWidgets.QCheckBox("Dùng ảnh comment")
        self.use_comment_images_checkbox.setChecked(False)
        file_layout.addWidget(self.use_comment_images_checkbox, 3, 0, 1, 3)

        quick_layout.addWidget(file_group)

        schedule_group = QtWidgets.QGroupBox("Lịch đăng và lặp lại")
        schedule_layout = QtWidgets.QVBoxLayout(schedule_group)

        self.schedule_checkbox = QtWidgets.QCheckBox("Bật lịch đăng")
        self.schedule_checkbox.toggled.connect(self._on_schedule_changed)
        schedule_layout.addWidget(self.schedule_checkbox)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Chế độ:"))
        self.schedule_mode_combo = QtWidgets.QComboBox()
        self.schedule_mode_combo.addItem("Một lần", "once")
        self.schedule_mode_combo.addItem("Lặp sau X giờ", "interval")
        self.schedule_mode_combo.addItem("Mỗi ngày vào giờ", "daily")
        self.schedule_mode_combo.addItem("Theo thứ", "weekly")
        self.schedule_mode_combo.currentIndexChanged.connect(self._on_schedule_changed)
        mode_row.addWidget(self.schedule_mode_combo)
        mode_row.addStretch(1)
        schedule_layout.addLayout(mode_row)

        self.once_row = QtWidgets.QWidget()
        once_l = QtWidgets.QHBoxLayout(self.once_row)
        once_l.setContentsMargins(0, 0, 0, 0)
        once_l.addWidget(QtWidgets.QLabel("Ngày giờ:"))
        self.schedule_datetime = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.schedule_datetime.setCalendarPopup(True)
        self.schedule_datetime.dateTimeChanged.connect(self._on_schedule_changed)
        once_l.addWidget(self.schedule_datetime)
        once_l.addStretch(1)
        schedule_layout.addWidget(self.once_row)

        self.interval_row = QtWidgets.QWidget()
        int_l = QtWidgets.QHBoxLayout(self.interval_row)
        int_l.setContentsMargins(0, 0, 0, 0)
        int_l.addWidget(QtWidgets.QLabel("Sau X giờ:"))
        self.schedule_interval_spin = QtWidgets.QDoubleSpinBox()
        self.schedule_interval_spin.setRange(0.25, 168.0)
        self.schedule_interval_spin.setSingleStep(0.25)
        self.schedule_interval_spin.setValue(24.0)
        self.schedule_interval_spin.valueChanged.connect(self._on_schedule_changed)
        int_l.addWidget(self.schedule_interval_spin)
        int_l.addStretch(1)
        schedule_layout.addWidget(self.interval_row)

        self.daily_row = QtWidgets.QWidget()
        daily_l = QtWidgets.QHBoxLayout(self.daily_row)
        daily_l.setContentsMargins(0, 0, 0, 0)
        daily_l.addWidget(QtWidgets.QLabel("Giờ chạy:"))
        self.schedule_time_of_day = QtWidgets.QTimeEdit(QtCore.QTime.currentTime())
        self.schedule_time_of_day.setDisplayFormat("HH:mm")
        self.schedule_time_of_day.timeChanged.connect(self._on_schedule_changed)
        daily_l.addWidget(self.schedule_time_of_day)
        daily_l.addStretch(1)
        schedule_layout.addWidget(self.daily_row)

        self.weekday_row = QtWidgets.QWidget()
        wd_l = QtWidgets.QGridLayout(self.weekday_row)
        wd_l.setContentsMargins(0, 0, 0, 0)
        wd_l.addWidget(QtWidgets.QLabel("Chọn thứ:"), 0, 0)
        self.weekday_checks: List[QtWidgets.QCheckBox] = []
        for idx, name in enumerate(["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"]):
            chk = QtWidgets.QCheckBox(name)
            chk.toggled.connect(self._on_schedule_changed)
            self.weekday_checks.append(chk)
            wd_l.addWidget(chk, 0, idx + 1)
        schedule_layout.addWidget(self.weekday_row)

        quick_layout.addWidget(schedule_group)

        actions = QtWidgets.QHBoxLayout()
        self.apply_btn = QtWidgets.QPushButton("Áp dụng cho toàn bộ dòng")
        actions.addWidget(self.apply_btn)
        actions.addStretch(1)
        quick_layout.addLayout(actions)

        layout.addWidget(quick_group)

    def _on_schedule_changed(self) -> None:
        enabled = self.schedule_checkbox.isChecked()
        mode = str(self.schedule_mode_combo.currentData() or "once")
        self.once_row.setVisible(enabled and mode == "once")
        self.interval_row.setVisible(enabled and mode == "interval")
        self.daily_row.setVisible(enabled and mode in {"daily", "weekly"})
        self.weekday_row.setVisible(enabled and mode == "weekly")
        self.schedule_changed.emit()

    def build_schedule_data(self) -> Dict[str, Any]:
        mode = str(self.schedule_mode_combo.currentData() or "once")
        return {
            "enabled": self.schedule_checkbox.isChecked(),
            "mode": mode,
            "once_datetime": self.schedule_datetime.dateTime().toPyDateTime().isoformat() if self.schedule_checkbox.isChecked() and mode == "once" else None,
            "interval_hours": float(self.schedule_interval_spin.value()),
            "time_of_day": self.schedule_time_of_day.time().toString("HH:mm"),
            "weekdays": [idx for idx, chk in enumerate(self.weekday_checks) if chk.isChecked()],
        }

    def load_schedule_data(self, data: Dict[str, Any]) -> None:
        payload = data or {}
        self.schedule_checkbox.setChecked(bool(payload.get("enabled", False)))
        mode = str(payload.get("mode") or "").strip().lower()
        if not mode and payload.get("datetime"):
            mode = "once"
        idx = self.schedule_mode_combo.findData(mode or "once")
        if idx >= 0:
            self.schedule_mode_combo.setCurrentIndex(idx)

        once_value = payload.get("once_datetime") or payload.get("datetime")
        if once_value:
            try:
                parsed = datetime.fromisoformat(str(once_value))
                self.schedule_datetime.setDateTime(QtCore.QDateTime(parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second))
            except Exception:
                pass

        try:
            self.schedule_interval_spin.setValue(float(payload.get("interval_hours", 24.0)))
        except Exception:
            pass

        time_value = payload.get("time_of_day") or payload.get("daily_time")
        if time_value:
            qtime = QtCore.QTime.fromString(str(time_value), "HH:mm")
            if qtime.isValid():
                self.schedule_time_of_day.setTime(qtime)

        weekdays = payload.get("weekdays", [])
        if isinstance(weekdays, str):
            weekdays = [int(p.strip()) for p in weekdays.split(",") if p.strip().isdigit()]
        weekday_set = {int(v) for v in weekdays}
        for idx, chk in enumerate(self.weekday_checks):
            chk.setChecked(idx in weekday_set)

        self._on_schedule_changed()

    def get_selected_schedule_base_time(self) -> Optional[datetime]:
        if not self.schedule_checkbox.isChecked():
            return None
        mode = str(self.schedule_mode_combo.currentData() or "once")
        now = datetime.now()
        if mode == "once":
            return self.schedule_datetime.dateTime().toPyDateTime()
        if mode == "interval":
            return now + timedelta(hours=float(self.schedule_interval_spin.value()))
        if mode in {"daily", "weekly"}:
            time_obj = self.schedule_time_of_day.time().toPyTime()
            candidate = datetime.combine(now.date(), time_obj)
            if candidate <= now:
                candidate += timedelta(days=1)
            if mode == "weekly":
                weekdays = [idx for idx, chk in enumerate(self.weekday_checks) if chk.isChecked()]
                if weekdays:
                    candidates = []
                    for weekday in weekdays:
                        days_ahead = (weekday - now.weekday()) % 7
                        c = datetime.combine((now + timedelta(days=days_ahead)).date(), time_obj)
                        if c <= now:
                            c += timedelta(days=7)
                        candidates.append(c)
                    return min(candidates) if candidates else None
                return None
            return candidate
        return None

    def get_next_repeat_run_time(self) -> Optional[datetime]:
        if not self.schedule_checkbox.isChecked():
            return None
        mode = str(self.schedule_mode_combo.currentData() or "once")
        if mode == "once":
            return None
        now = datetime.now()
        if mode == "interval":
            return now + timedelta(hours=float(self.schedule_interval_spin.value()))
        return self.get_selected_schedule_base_time()

    def get_video_folder(self) -> str:
        return self.video_folder_input.text().strip()

    def get_comment_image_folder(self) -> str:
        return self.comment_image_folder_input.text().strip()

    def get_skip_missing(self) -> bool:
        return self.skip_missing_checkbox.isChecked()

    def get_use_comment_images(self) -> bool:
        return self.use_comment_images_checkbox.isChecked()

    def select_video_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục video")
        if folder:
            self.video_folder_input.setText(folder)

    def select_comment_image_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục ảnh comment")
        if folder:
            self.comment_image_folder_input.setText(folder)
