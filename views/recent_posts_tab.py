import math
from typing import Callable, List, Optional

from PyQt5 import QtCore, QtWidgets

from views.widgets import apply_small_button_style


class RecentPostsTab(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.recent_page = 1
        self.recent_total_pages = 1
        self.recent_page_size = 20
        self._load_page_callback: Optional[Callable] = None
        self._build_ui()

    def set_load_callback(self, callback: Callable) -> None:
        self._load_page_callback = callback

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QtWidgets.QLabel("Danh sách các bài đăng thành công gần đây")
        label.setWordWrap(True)
        layout.addWidget(label)

        controls = QtWidgets.QHBoxLayout()
        self.clear_btn = QtWidgets.QPushButton("Xóa lịch sử")
        apply_small_button_style(self.clear_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)

        self.page_label = QtWidgets.QLabel("Trang 1")
        self.page_label.setStyleSheet("color: #334155; font-weight: 800;")
        controls.addWidget(self.page_label)

        self.prev_btn = QtWidgets.QPushButton("‹")
        apply_small_button_style(self.prev_btn)
        controls.addWidget(self.prev_btn)

        self.next_btn = QtWidgets.QPushButton("›")
        apply_small_button_style(self.next_btn)
        controls.addWidget(self.next_btn)
        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Page", "Nội dung", "Loại", "Thời gian"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 420)
        layout.addWidget(self.table)

    def load_page(self, rows: List[dict], total_count: int, page: int) -> None:
        self.recent_total_pages = max(1, math.ceil(total_count / self.recent_page_size))
        self.recent_page = max(1, min(page, self.recent_total_pages))
        self.table.setRowCount(0)
        for row_idx, row in enumerate(rows):
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(str(row.get("page_name", ""))))
            self.table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(row.get("content", ""))))
            self.table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(str(row.get("post_type", ""))))
            self.table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(str(row.get("posted_at", ""))))
        self.page_label.setText(f"Trang {self.recent_page}/{self.recent_total_pages}")
        self.prev_btn.setEnabled(self.recent_page > 1)
        self.next_btn.setEnabled(self.recent_page < self.recent_total_pages)

    def go_prev(self) -> None:
        if self._load_page_callback:
            self._load_page_callback(self.recent_page - 1)

    def go_next(self) -> None:
        if self._load_page_callback:
            self._load_page_callback(self.recent_page + 1)
