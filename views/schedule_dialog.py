"""Dialog chọn cách bố trí lịch đăng cho cả hàng đợi.

Hai chế độ:
  * Giãn cách: bắt đầu từ một thời điểm, mỗi ``interval`` phút đăng 1 video.
  * Khung giờ: các mốc cố định trong ngày (09:00 / 14:00 / 20:00) lặp lại qua
    các ngày sau, tuỳ chọn giới hạn số video mỗi ngày.
"""

from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtWidgets

from services.schedule_service import MODE_INTERVAL, MODE_SLOTS, build_slots, format_slots
from views.widgets import apply_small_button_style

PREVIEW_LIMIT = 6


class SchedulePlanDialog(QtWidgets.QDialog):
    """Thu thập tham số lên lịch và trả về dict plan (hoặc None nếu huỷ)."""

    def __init__(self, count: int, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lên lịch đăng hàng loạt")
        self.resize(520, 430)
        self._count = max(0, int(count or 0))
        self._times: List[str] = ["09:00", "14:00", "20:00"]
        self._build_ui()
        self._refresh_preview()

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QtWidgets.QLabel(
            f"Hàng đợi đang có <b>{self._count}</b> dòng đủ video. "
            "Các dòng sẽ được gán giờ theo thứ tự trong bảng."
        )
        header.setWordWrap(True)
        header.setStyleSheet("color:#334155; font-weight:650;")
        layout.addWidget(header)

        mode_group = QtWidgets.QGroupBox("Cách bố trí")
        mode_layout = QtWidgets.QVBoxLayout(mode_group)
        mode_layout.setSpacing(6)
        self.mode_interval_radio = QtWidgets.QRadioButton("Giãn cách đều theo phút")
        self.mode_slots_radio = QtWidgets.QRadioButton("Khung giờ cố định trong ngày")
        self.mode_interval_radio.setChecked(True)
        mode_layout.addWidget(self.mode_interval_radio)
        mode_layout.addWidget(self.mode_slots_radio)

        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(1, 24 * 60)
        self.interval_spin.setValue(60)
        self.interval_spin.setSuffix(" phút")
        interval_row = QtWidgets.QHBoxLayout()
        interval_row.addWidget(QtWidgets.QLabel("Cách nhau:"))
        interval_row.addWidget(self.interval_spin, 1)
        mode_layout.addLayout(interval_row)

        self.start_at_edit = QtWidgets.QDateTimeEdit(
            QtCore.QDateTime.currentDateTime().addSecs(5 * 60)
        )
        self.start_at_edit.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.start_at_edit.setCalendarPopup(True)
        self.start_at_edit.setMinimumDateTime(QtCore.QDateTime.currentDateTime().addSecs(30))
        start_row = QtWidgets.QHBoxLayout()
        start_row.addWidget(QtWidgets.QLabel("Bắt đầu:"))
        start_row.addWidget(self.start_at_edit, 1)
        mode_layout.addLayout(start_row)

        self.slots_widget = QtWidgets.QWidget()
        slots_layout = QtWidgets.QVBoxLayout(self.slots_widget)
        slots_layout.setContentsMargins(0, 4, 0, 0)
        slots_layout.setSpacing(4)

        add_row = QtWidgets.QHBoxLayout()
        add_row.addWidget(QtWidgets.QLabel("Khung giờ:"))
        self.time_edit = QtWidgets.QTimeEdit(QtCore.QTime(9, 0))
        self.time_edit.setDisplayFormat("HH:mm")
        add_row.addWidget(self.time_edit)
        self.add_time_btn = QtWidgets.QPushButton("Thêm khung giờ")
        apply_small_button_style(self.add_time_btn)
        add_row.addWidget(self.add_time_btn)
        add_row.addStretch(1)
        slots_layout.addLayout(add_row)

        self.slots_table = QtWidgets.QTableWidget(0, 1)
        self.slots_table.setHorizontalHeaderLabels(["Khung giờ"])
        self.slots_table.horizontalHeader().setStretchLastSection(True)
        self.slots_table.setMaximumHeight(110)
        self.slots_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        slots_layout.addWidget(self.slots_table)

        tools_row = QtWidgets.QHBoxLayout()
        self.remove_time_btn = QtWidgets.QPushButton("Xoá khung giờ đã chọn")
        apply_small_button_style(self.remove_time_btn)
        self.clear_times_btn = QtWidgets.QPushButton("Xoá hết")
        apply_small_button_style(self.clear_times_btn)
        tools_row.addWidget(self.remove_time_btn)
        tools_row.addWidget(self.clear_times_btn)
        tools_row.addStretch(1)
        slots_layout.addLayout(tools_row)

        self.daily_limit_spin = QtWidgets.QSpinBox()
        self.daily_limit_spin.setRange(0, 200)
        self.daily_limit_spin.setValue(0)
        self.daily_limit_spin.setSpecialValueText("Không giới hạn")
        limit_row = QtWidgets.QHBoxLayout()
        limit_row.addWidget(QtWidgets.QLabel("Tối đa video mỗi ngày:"))
        limit_row.addWidget(self.daily_limit_spin, 1)
        slots_layout.addLayout(limit_row)
        mode_layout.addWidget(self.slots_widget)
        layout.addWidget(mode_group)

        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color:#0f172a; background:#f1f5f9; border-radius:6px; padding:6px;")
        layout.addWidget(self.preview_label)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Lên lịch")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Huỷ")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.add_time_btn.clicked.connect(self._on_add_time)
        self.remove_time_btn.clicked.connect(self._on_remove_time)
        self.clear_times_btn.clicked.connect(self._on_clear_times)
        self.mode_interval_radio.toggled.connect(self._sync_mode)
        self.interval_spin.valueChanged.connect(lambda _v: self._refresh_preview())
        self.start_at_edit.dateTimeChanged.connect(lambda _v: self._refresh_preview())
        self.daily_limit_spin.valueChanged.connect(lambda _v: self._refresh_preview())
        self._sync_mode()

    def _sync_mode(self) -> None:
        is_slots = self.mode_slots_radio.isChecked()
        self.interval_spin.setEnabled(not is_slots)
        self.start_at_edit.setEnabled(not is_slots)
        self.slots_widget.setEnabled(is_slots)
        self._refresh_preview()

    # ── Danh sách khung giờ ───────────────────────────────────────

    def _on_add_time(self) -> None:
        value = self.time_edit.time().toString("HH:mm")
        if value in self._times:
            return
        self._times.append(value)
        self._sync_times()

    def _on_remove_time(self) -> None:
        rows = {index.row() for index in self.slots_table.selectedIndexes()}
        if not rows:
            return
        self._times = [t for index, t in enumerate(self._times) if index not in rows]
        self._sync_times()

    def _on_clear_times(self) -> None:
        self._times = []
        self._sync_times()

    def _sync_times(self) -> None:
        self._times = sorted(set(self._times))
        self.slots_table.setRowCount(0)
        for value in self._times:
            row = self.slots_table.rowCount()
            self.slots_table.insertRow(row)
            item = QtWidgets.QTableWidgetItem(value)
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.slots_table.setItem(row, 0, item)
        self._refresh_preview()

    # ── Plan ──────────────────────────────────────────────────────

    def current_plan(self) -> Dict[str, Any]:
        """Tham số lên lịch hiện tại (dùng khi dialog trả về Accepted)."""
        return self._current_plan()

    def _current_plan(self) -> Dict[str, Any]:
        is_slots = self.mode_slots_radio.isChecked()
        return {
            "mode": MODE_SLOTS if is_slots else MODE_INTERVAL,
            "start_at": self.start_at_edit.dateTime().toPyDateTime(),
            "interval_minutes": self.interval_spin.value(),
            "times_of_day": list(self._times),
            "daily_limit": self.daily_limit_spin.value(),
        }

    def _refresh_preview(self) -> None:
        plan = self._current_plan()
        slots = build_slots(
            self._count,
            plan["start_at"],
            plan["mode"],
            plan["interval_minutes"],
            plan["times_of_day"],
            plan["daily_limit"],
        )
        if not slots:
            self.preview_label.setText("Chưa có lịch — kiểm tra lại khung giờ hoặc số phút.")
            return
        if len(slots) < self._count:
            text = (
                f"Chỉ tạo được {len(slots)}/{self._count} lịch — thêm khung giờ "
                "hoặc giảm số video mỗi ngày."
            )
        else:
            text = f"{len(slots)} video sẽ được đăng, từ {format_slots(slots)}"
            if len(slots) > PREVIEW_LIMIT:
                text += f" … (tới {slots[-1].strftime('%d/%m %H:%M')})"
        self.preview_label.setText(text)
