from pathlib import Path
from typing import Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from views.widgets import apply_small_button_style

CHECK_COL = 0
NAME_COL = 1
POSTED_COL = 2
PATH_COL = 3


class VideoSelectionDialog(QtWidgets.QDialog):
    """Hiển thị danh sách video scan được để người dùng chọn (All hoặc 1 phần)."""

    def __init__(
        self,
        videos: List[str],
        posted_counts: Optional[Dict[str, int]] = None,
        page_count: int = 0,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chọn video để chia cho các page")
        self.resize(880, 600)
        self._videos = list(videos or [])
        self._posted_counts = dict(posted_counts or {})
        self._page_count = int(page_count or 0)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        header = QtWidgets.QLabel(
            f"Tìm thấy {len(self._videos)} video trong thư mục. "
            f"Sẽ chia cho {self._page_count} page đã chọn."
        )
        header.setWordWrap(True)
        header.setStyleSheet("font-weight: 800; color: #0f172a;")
        layout.addWidget(header)

        hint = QtWidgets.QLabel(
            "Cột 'Đã đăng ở' cho biết video đã được đăng ở bao nhiêu page trong nhóm đang chọn. "
            "Video đã đăng ở tất cả các page sẽ không còn được chia lại."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #334155;")
        layout.addWidget(hint)

        controls = QtWidgets.QHBoxLayout()
        self.select_all_btn = QtWidgets.QPushButton("Chọn tất cả")
        apply_small_button_style(self.select_all_btn)
        self.clear_btn = QtWidgets.QPushButton("Bỏ chọn")
        apply_small_button_style(self.clear_btn)
        controls.addWidget(self.select_all_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        self.count_label = QtWidgets.QLabel("Đã chọn 0 video")
        self.count_label.setStyleSheet("color: #0284c7; font-weight: 800;")
        controls.addWidget(self.count_label)
        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Chọn", "Tên video", "Đã đăng ở", "Đường dẫn"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(CHECK_COL, 60)
        self.table.setColumnWidth(NAME_COL, 280)
        self.table.setColumnWidth(POSTED_COL, 130)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QtWidgets.QPushButton("Hủy")
        apply_small_button_style(self.cancel_btn)
        self.ok_btn = QtWidgets.QPushButton("Xác nhận chia video")
        apply_small_button_style(self.ok_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.clear_btn.clicked.connect(lambda: self._set_all(False))
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        self.table.itemChanged.connect(lambda _item: self._update_count())

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, video_path in enumerate(self._videos):
            self.table.insertRow(row)
            check_item = QtWidgets.QTableWidgetItem()
            check_item.setFlags(check_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            check_item.setCheckState(QtCore.Qt.Checked)
            check_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, CHECK_COL, check_item)

            name_item = QtWidgets.QTableWidgetItem(Path(video_path).name)
            name_item.setToolTip(video_path)
            self.table.setItem(row, NAME_COL, name_item)

            count = int(self._posted_counts.get(video_path, 0) or 0)
            if self._page_count and count >= self._page_count:
                posted_text = f"Tất cả ({count}/{self._page_count})"
                posted_item = QtWidgets.QTableWidgetItem(posted_text)
                posted_item.setForeground(QtGui.QColor("#ef4444"))
            elif count > 0:
                posted_item = QtWidgets.QTableWidgetItem(f"{count}/{self._page_count} page")
                posted_item.setForeground(QtGui.QColor("#f59e0b"))
            else:
                posted_item = QtWidgets.QTableWidgetItem("Chưa đăng")
                posted_item.setForeground(QtGui.QColor("#22c55e"))
            posted_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, POSTED_COL, posted_item)

            path_item = QtWidgets.QTableWidgetItem(video_path)
            path_item.setToolTip(video_path)
            self.table.setItem(row, PATH_COL, path_item)
        self.table.blockSignals(False)
        self._update_count()

    def _set_all(self, checked: bool) -> None:
        self.table.blockSignals(True)
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, CHECK_COL)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_count()

    def _update_count(self) -> None:
        self.count_label.setText(f"Đã chọn {len(self.selected_videos())} video")

    def selected_videos(self) -> List[str]:
        selected: List[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, CHECK_COL)
            if item is not None and item.checkState() == QtCore.Qt.Checked and row < len(self._videos):
                selected.append(self._videos[row])
        return selected


class PostedVideosDialog(QtWidgets.QDialog):
    """Xem nhanh các video đã đăng (theo page)."""

    def __init__(self, rows: List[Dict[str, str]], parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Video đã đăng theo page")
        self.resize(820, 560)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        header = QtWidgets.QLabel(f"Tổng cộng {len(rows)} video đã đăng thành công.")
        header.setStyleSheet("font-weight: 800; color: #0f172a;")
        layout.addWidget(header)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Page", "Tên video", "Thời gian", "Link"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 150)
        layout.addWidget(self.table)

        for row_idx, row in enumerate(rows):
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(str(row.get("page_name", ""))))
            self.table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(row.get("video_name", ""))))
            self.table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(str(row.get("posted_at", ""))))
            self.table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(str(row.get("permalink_url", ""))))

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QtWidgets.QPushButton("Đóng")
        apply_small_button_style(close_btn)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
