from typing import Any, Callable, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from views.widgets import apply_small_button_style

STATS_HEADERS = [
    "Page", "Video trên page", "Video đã đăng (app)", "View video",
    "Followers", "Fan count", "Trạng thái", "Cập nhật lúc",
]

COL_PAGE = 0
COL_STATUS = 6

STATUS_STYLE = {
    "ok": ("#15803d", "#dcfce7", "Thành công"),
    "error": ("#b91c1c", "#fee2e2", "Lỗi"),
    "checking": ("#b45309", "#fef3c7", "Đang cập nhật…"),
    "skip": ("#64748b", "#f1f5f9", "Bỏ qua"),
}


class StatsTab(QtWidgets.QWidget):
    stats_ready = QtCore.pyqtSignal(list)
    stats_progress = QtCore.pyqtSignal(dict)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._on_refresh_all: Optional[Callable] = None
        self._on_refresh_selected: Optional[Callable] = None
        self._build_ui()
        self.stats_ready.connect(self._on_stats_ready)
        self.stats_progress.connect(self.upsert_row)

    def set_callbacks(self, on_refresh_all: Callable, on_refresh_selected: Callable) -> None:
        self._on_refresh_all = on_refresh_all
        self._on_refresh_selected = on_refresh_selected

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        desc = QtWidgets.QLabel(
            "\"Video trên page\" và \"View video\" lấy từ Facebook (số thật trên page). "
            "\"Video đã đăng (app)\" đếm từ lịch sử đăng trong app. "
            "Cần quyền read_insights (view) và pages_read_engagement (followers/fan)."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #334155; font-weight: 650;")
        layout.addWidget(desc)

        controls = QtWidgets.QHBoxLayout()
        self.refresh_all_btn = QtWidgets.QPushButton("Cập nhật tất cả")
        apply_small_button_style(self.refresh_all_btn)
        controls.addWidget(self.refresh_all_btn)
        self.refresh_selected_btn = QtWidgets.QPushButton("Cập nhật page đã chọn")
        apply_small_button_style(self.refresh_selected_btn)
        controls.addWidget(self.refresh_selected_btn)
        controls.addStretch(1)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("color: #0284c7; font-weight: 800;")
        controls.addWidget(self.status_label)
        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, len(STATS_HEADERS))
        self.table.setHorizontalHeaderLabels(STATS_HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_PAGE, QtWidgets.QHeaderView.Stretch)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 110)
        self.table.setColumnWidth(5, 110)
        self.table.setColumnWidth(6, 130)
        self.table.setColumnWidth(7, 160)
        layout.addWidget(self.table)

        self.refresh_all_btn.clicked.connect(lambda: self._on_refresh_all and self._on_refresh_all())
        self.refresh_selected_btn.clicked.connect(lambda: self._on_refresh_selected and self._on_refresh_selected())

    # ── Data ──────────────────────────────────────────────────────
    @staticmethod
    def _row_values(row: Dict[str, Any]) -> List[str]:
        def fmt(value: Any) -> str:
            return "N/A" if value is None else f"{int(value):,}"

        status = row.get("status", "")
        status_text = row.get("status_text") or STATUS_STYLE.get(status, ("", "", ""))[2]
        return [
            str(row.get("page", "")),
            fmt(row.get("page_video_count")),
            fmt(row.get("app_video_count")),
            fmt(row.get("video_views")),
            fmt(row.get("followers")),
            fmt(row.get("fan_count")),
            str(status_text),
            str(row.get("updated_at", "")),
        ]

    def _find_row(self, page: str) -> int:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, COL_PAGE)
            if item is not None and item.text() == page:
                return r
        return -1

    def _ensure_row(self, page: str) -> int:
        row = self._find_row(page)
        if row >= 0:
            return row
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col in range(self.table.columnCount()):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(""))
        self.table.item(row, COL_PAGE).setText(page)
        self.table.setRowHeight(row, 26)
        return row

    def _apply_status_color(self, row: int, status: str) -> None:
        style = STATUS_STYLE.get(status)
        if not style:
            return
        fg, bg, _ = style
        for col in (COL_PAGE, COL_STATUS):
            item = self.table.item(row, col)
            if item is not None:
                item.setForeground(QtGui.QColor(fg))
                item.setBackground(QtGui.QColor(bg))

    def load_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.table.setRowCount(0)
        for row in rows or []:
            self.upsert_row(row)

    def upsert_row(self, row: Dict[str, Any]) -> None:
        page = str(row.get("page", ""))
        if not page:
            return
        target = self._ensure_row(page)
        for col, value in enumerate(self._row_values(row)):
            item = self.table.item(target, col)
            if item is None:
                item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(target, col, item)
            item.setText(value)
        self._apply_status_color(target, str(row.get("status", "")))
        self.table.setRowHeight(target, 26)

    def mark_checking(self, page_names: List[str]) -> None:
        for name in page_names or []:
            row = self._ensure_row(str(name))
            status_item = self.table.item(row, COL_STATUS)
            if status_item is not None:
                status_item.setText(STATUS_STYLE["checking"][2])
            self._apply_status_color(row, "checking")

    def set_busy(self, busy: bool) -> None:
        self.refresh_all_btn.setEnabled(not busy)
        self.refresh_selected_btn.setEnabled(not busy)
        self.status_label.setText("Đang cập nhật..." if busy else "")

    def _on_stats_ready(self, rows: List[Dict[str, Any]]) -> None:
        self.set_busy(False)
        self.load_rows(rows)
        ok = sum(1 for r in (rows or []) if r.get("status") == "ok")
        self.status_label.setText(f"Xong: {ok}/{len(rows or [])} page thành công")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
