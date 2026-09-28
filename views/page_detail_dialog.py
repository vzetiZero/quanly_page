from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

# ── Danh sách video của page ──────────────────────────────────────
VIDEO_HEADERS = ["STT", "Ngày đăng", "Nội dung", "Link video", "View"]
VIDEO_COL_INDEX = 0
VIDEO_COL_CREATED = 1
VIDEO_COL_TITLE = 2
VIDEO_COL_LINK = 3
VIDEO_COL_VIEWS = 4

SORT_OPTIONS = [
    ("Mới nhất trước", "newest"),
    ("Cũ nhất trước", "oldest"),
    ("View cao nhất", "views_desc"),
    ("View thấp nhất", "views_asc"),
    ("STT tăng dần", "index"),
]


def _format_number(value: Any) -> str:
    if value is None or value == "" or value == "N/A":
        return "N/A"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


class PageDetailDialog(QtWidgets.QDialog):
    details_ready = QtCore.pyqtSignal(dict)
    details_error = QtCore.pyqtSignal(str)
    videos_ready = QtCore.pyqtSignal(list)
    page_stats_ready = QtCore.pyqtSignal(dict)

    def __init__(
        self,
        page_id: str,
        page_name: str,
        access_token: str,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.page_id = page_id
        self.page_name = page_name
        self.access_token = access_token
        self._overlay: Optional[QtWidgets.QWidget] = None
        self._overlay_label: Optional[QtWidgets.QLabel] = None
        self._presenter = None
        self._videos: List[Dict[str, Any]] = []

        self.setWindowTitle(f"Chi tiết Page: {page_name}")
        self.resize(1000, 720)
        self.setMinimumSize(820, 560)
        self.setStyleSheet(
            """
            * { font-family: "Nunito"; font-size: 9pt; }
            QDialog { background: #f8fafc; color: #0f172a; }
            QGroupBox { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 10px; padding: 12px; font-weight: 800; }
            QLabel { color: #334155; font-weight: 650; }
            QTableWidget { background: #ffffff; alternate-background-color: #f8fafc; color: #0f172a; gridline-color: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 8px; }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section { background: #e2e8f0; color: #0f172a; padding: 7px; border: 1px solid #cbd5e1; font-weight: 800; }
            QPushButton { background: #0284c7; color: #ffffff; border: 1px solid #0284c7; border-radius: 8px; padding: 8px 14px; font-weight: 800; }
            QPushButton:hover { background: #0ea5e9; }
            QPushButton:disabled { background: #e2e8f0; border-color: #cbd5e1; color: #64748b; }
            QComboBox { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 3px 6px; }
            """
        )
        self._build_ui()
        self.details_ready.connect(self._on_details_ready)
        self.details_error.connect(self._on_details_error)
        self.videos_ready.connect(self.populate_videos)
        self.page_stats_ready.connect(self.populate_overview)

    def set_presenter(self, presenter: Any) -> None:
        self._presenter = presenter

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(f"Page Details: {self.page_name}")
        title_label.setStyleSheet("font-size: 13pt; font-weight: 800; color: #0f172a;")
        header.addWidget(title_label)
        header.addStretch(1)
        self.refresh_btn = QtWidgets.QPushButton("Cập nhật")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self.refresh_btn.setMinimumWidth(120)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.warning_label = QtWidgets.QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b91c1c; font-weight: 700;")
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)

        info_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        basic_group = QtWidgets.QGroupBox("Thông tin cơ bản")
        basic_layout = QtWidgets.QFormLayout(basic_group)
        basic_layout.setSpacing(6)
        self.labels: Dict[str, QtWidgets.QLabel] = {}
        for field_name, display_name in [
            ("page_id", "Page ID"), ("category", "Danh mục"), ("about", "Giới thiệu"),
            ("description", "Mô tả"), ("phone", "Điện thoại"), ("website", "Website"),
            ("location", "Địa điểm"), ("emails", "Emails"), ("verification_status", "Xác minh"),
            ("link", "Link"), ("created_time", "Ngày tạo"), ("instagram", "Instagram"),
        ]:
            lbl = QtWidgets.QLabel("N/A")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            basic_layout.addRow(f"{display_name}:", lbl)
            self.labels[field_name] = lbl
        info_splitter.addWidget(basic_group)

        counts_group = QtWidgets.QGroupBox("Thống kê & Engagement")
        counts_layout = QtWidgets.QFormLayout(counts_group)
        counts_layout.setSpacing(6)
        for field_name, display_name in [
            ("followers_count", "Followers"), ("fan_count", "Fan count"),
            ("likes_count", "Likes"), ("talking_about_count", "Talking About"),
        ]:
            lbl = QtWidgets.QLabel("N/A")
            lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            counts_layout.addRow(f"{display_name}:", lbl)
            self.labels[field_name] = lbl

        # Số liệu chính: số video trên page / tổng view / follow (đồng bộ tab Thống kê)
        for field_name, display_name in [
            ("page_video_count", "Số video trên page"),
            ("video_views", "Tổng view video"),
        ]:
            overview = QtWidgets.QLabel("N/A")
            overview.setStyleSheet("color: #0f172a; font-weight: 800;")
            overview.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.labels[field_name] = overview
            counts_layout.addRow(f"{display_name}:", overview)

        self.updated_at_label = QtWidgets.QLabel("Cập nhật lúc: N/A")
        self.updated_at_label.setStyleSheet("color: #64748b; font-weight: 650;")
        counts_layout.addRow("", self.updated_at_label)
        self.stats_btn = QtWidgets.QPushButton("Cập nhật số liệu")
        self.stats_btn.clicked.connect(self._on_stats_clicked)
        self.stats_btn.setMaximumWidth(160)
        stats_row = QtWidgets.QHBoxLayout()
        stats_row.addWidget(self.stats_btn)
        stats_row.addStretch(1)
        counts_layout.addRow("", self._wrap(stats_row))
        info_splitter.addWidget(counts_group)
        info_splitter.setStretchFactor(0, 3)
        info_splitter.setStretchFactor(1, 2)
        info_splitter.setMaximumHeight(320)
        layout.addWidget(info_splitter)

        # ── Danh sách video của page (thay cho "Lịch sử cập nhật") ──
        video_group = QtWidgets.QGroupBox("Danh sách video của page")
        video_layout = QtWidgets.QVBoxLayout(video_group)
        video_layout.setSpacing(6)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(QtWidgets.QLabel("Lọc:"))
        self.video_filter_input = QtWidgets.QLineEdit()
        self.video_filter_input.setPlaceholderText("Nội dung, ID hoặc link video…")
        self.video_filter_input.textChanged.connect(self._apply_video_view)
        filter_row.addWidget(self.video_filter_input, 1)
        filter_row.addWidget(QtWidgets.QLabel("Sắp xếp:"))
        self.video_sort_combo = QtWidgets.QComboBox()
        for label, key in SORT_OPTIONS:
            self.video_sort_combo.addItem(label, key)
        self.video_sort_combo.currentIndexChanged.connect(self._apply_video_view)
        filter_row.addWidget(self.video_sort_combo)
        self.video_count_label = QtWidgets.QLabel("Tổng: 0 video")
        self.video_count_label.setStyleSheet("color: #0284c7; font-weight: 800;")
        filter_row.addWidget(self.video_count_label)
        self.video_refresh_btn = QtWidgets.QPushButton("Tải lại danh sách")
        self.video_refresh_btn.clicked.connect(self._on_videos_clicked)
        filter_row.addWidget(self.video_refresh_btn)
        video_layout.addLayout(filter_row)

        self.video_table = QtWidgets.QTableWidget(0, len(VIDEO_HEADERS))
        self.video_table.setHorizontalHeaderLabels(VIDEO_HEADERS)
        self.video_table.setAlternatingRowColors(True)
        self.video_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.video_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.video_table.verticalHeader().setVisible(False)
        self.video_table.verticalHeader().setDefaultSectionSize(26)
        self.video_table.setSortingEnabled(False)
        vheader = self.video_table.horizontalHeader()
        vheader.setSectionResizeMode(VIDEO_COL_TITLE, QtWidgets.QHeaderView.Stretch)
        self.video_table.setColumnWidth(VIDEO_COL_INDEX, 52)
        self.video_table.setColumnWidth(VIDEO_COL_CREATED, 140)
        self.video_table.setColumnWidth(VIDEO_COL_LINK, 230)
        self.video_table.setColumnWidth(VIDEO_COL_VIEWS, 100)
        self.video_table.cellDoubleClicked.connect(self._on_video_cell_double_clicked)
        video_layout.addWidget(self.video_table)
        layout.addWidget(video_group, 1)

        close_btn = QtWidgets.QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        close_btn.setMaximumWidth(120)
        close_layout = QtWidgets.QHBoxLayout()
        close_layout.addStretch(1)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)

    @staticmethod
    def _wrap(layout: QtWidgets.QHBoxLayout) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        return widget

    # ── Nút ───────────────────────────────────────────────────────
    def _on_refresh_clicked(self) -> None:
        if self._presenter:
            self._presenter.refresh(self.page_id, self.page_name, self.access_token)

    def _on_videos_clicked(self) -> None:
        if self._presenter:
            self._presenter.load_videos()

    def _on_stats_clicked(self) -> None:
        if self._presenter:
            self._presenter.refresh_stats()

    def _on_video_cell_double_clicked(self, row: int, col: int) -> None:
        if col != VIDEO_COL_LINK:
            return
        item = self.video_table.item(row, VIDEO_COL_LINK)
        link = item.text().strip() if item is not None else ""
        if not link:
            item = self.video_table.item(row, VIDEO_COL_TITLE)
            link = item.text().strip() if item is not None else ""
        if link.startswith("http"):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))

    # ── Nhận kết quả từ luồng nền qua signal (an toàn luồng) ──────
    def _on_details_ready(self, data: Dict[str, Any]) -> None:
        self.hide_overlay()
        self.set_refresh_enabled(True)
        self.set_refresh_text("Cập nhật")
        if data:
            self.populate_info(data)
        self._show_field_errors(data)

    def _on_details_error(self, message: str) -> None:
        self.hide_overlay()
        self.set_refresh_enabled(True)
        self.set_refresh_text("Cập nhật")
        self.show_error(f"Không thể cập nhật: {message}")

    def _show_field_errors(self, data: Dict[str, Any]) -> None:
        errors = data.get("_errors") if isinstance(data, dict) else None
        if not errors:
            self.warning_label.setText("")
            self.warning_label.setVisible(False)
            return
        parts = []
        for item in errors[:8]:
            field = item.get("field", "")
            message = item.get("message", "")
            parts.append(f"{field} ({message})" if message else field)
        self.warning_label.setText("Không lấy được một số trường: " + "; ".join(parts))
        self.warning_label.setVisible(True)

    # ── IPageDetailView interface implementation ──────────────────

    def populate_info(self, data: Dict[str, Any]) -> None:
        ig_id = data.get("instagram_business_account_id", "")
        ig_username = data.get("instagram_business_account_username", "")
        ig_text = f"@{ig_username} ({ig_id})" if ig_username else (ig_id or "N/A")

        mapping = {
            "page_id": data.get("page_id", data.get("id", "N/A")),
            "category": data.get("category", "N/A"),
            "about": data.get("about", "N/A"),
            "description": data.get("description", "N/A"),
            "phone": data.get("phone", "N/A"),
            "website": data.get("website", "N/A"),
            "location": data.get("location", "N/A"),
            "emails": data.get("emails", "N/A"),
            "verification_status": data.get("verification_status", "N/A"),
            "link": data.get("link", "N/A"),
            "created_time": data.get("created_time", "N/A"),
            "instagram": ig_text,
        }
        for key, value in mapping.items():
            if key in self.labels:
                self.labels[key].setText(str(value) if value else "N/A")

        def _fmt(value: Any) -> str:
            if value is None:
                return "N/A"
            if isinstance(value, float):
                return f"{value:.1f}"
            if isinstance(value, int):
                return f"{value:,}"
            return str(value)

        for key in ["followers_count", "fan_count", "likes_count", "talking_about_count"]:
            if key in self.labels:
                self.labels[key].setText(_fmt(data.get(key)))

    def populate_overview(self, stats: Dict[str, Any]) -> None:
        """Số video / tổng view / follow của page (cùng nguồn số liệu với tab Thống kê)."""
        if not isinstance(stats, dict):
            return
        video_count = stats.get("page_video_count", stats.get("video_count"))
        video_views = stats.get("video_views", stats.get("views"))
        followers = stats.get("followers", stats.get("followers_count"))
        fan_count = stats.get("fan_count")
        for key, value in (
            ("page_video_count", _format_number(video_count)),
            ("video_views", _format_number(video_views)),
            ("followers_count", _format_number(followers)),
            ("fan_count", _format_number(fan_count)),
        ):
            label = self.labels.get(key)
            if label is not None:
                label.setText(value)
        updated = str(stats.get("updated_at") or "").strip()
        self.updated_at_label.setText(f"Cập nhật lúc: {updated or 'N/A'}")

    def populate_videos(self, videos: List[Dict[str, Any]]) -> None:
        self._videos = list(videos or [])
        self._apply_video_view()

    # ── Lọc + sắp xếp + đánh số thứ tự ───────────────────────────
    def _apply_video_view(self) -> None:
        keyword = self.video_filter_input.text().strip().lower()
        rows = list(self._videos)

        if keyword:
            def matches(video: Dict[str, Any]) -> bool:
                haystack = " ".join([
                    str(video.get("title") or ""),
                    str(video.get("id") or ""),
                    str(video.get("link") or ""),
                ]).lower()
                return keyword in haystack

            rows = [v for v in rows if matches(v)]

        sort_key = self.video_sort_combo.currentData() or "newest"
        if sort_key == "newest":
            rows.sort(key=lambda v: str(v.get("created_time") or ""), reverse=True)
        elif sort_key == "oldest":
            rows.sort(key=lambda v: str(v.get("created_time") or ""))
        elif sort_key == "views_desc":
            rows.sort(key=lambda v: (v.get("views") if isinstance(v.get("views"), int) else -1), reverse=True)
        elif sort_key == "views_asc":
            rows.sort(key=lambda v: (v.get("views") if isinstance(v.get("views"), int) else 10**12))
        # "index": giữ nguyên thứ tự Facebook trả về

        self.video_table.setRowCount(0)
        for position, video in enumerate(rows, start=1):
            row_idx = self.video_table.rowCount()
            self.video_table.insertRow(row_idx)

            index_item = QtWidgets.QTableWidgetItem(str(position))
            index_item.setTextAlignment(QtCore.Qt.AlignCenter)
            index_item.setData(QtCore.Qt.UserRole, str(video.get("id") or ""))
            self.video_table.setItem(row_idx, VIDEO_COL_INDEX, index_item)

            created = str(video.get("created_time") or "")
            self.video_table.setItem(row_idx, VIDEO_COL_CREATED, QtWidgets.QTableWidgetItem(
                created[:19].replace("T", " ") if created else "N/A"))

            title_item = QtWidgets.QTableWidgetItem(str(video.get("title") or "N/A"))
            title_item.setToolTip(str(video.get("title") or ""))
            self.video_table.setItem(row_idx, VIDEO_COL_TITLE, title_item)

            link = str(video.get("link") or "")
            link_item = QtWidgets.QTableWidgetItem(link or "N/A")
            if link:
                link_item.setForeground(QtGui.QColor("#0284c7"))
                link_item.setToolTip(f"Nhấp đôi để mở:\n{link}")
            self.video_table.setItem(row_idx, VIDEO_COL_LINK, link_item)

            views = video.get("views")
            views_item = QtWidgets.QTableWidgetItem(_format_number(views))
            views_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if isinstance(views, int):
                views_item.setForeground(QtGui.QColor("#15803d"))
            else:
                views_item.setForeground(QtGui.QColor("#94a3b8"))
            self.video_table.setItem(row_idx, VIDEO_COL_VIEWS, views_item)

            self.video_table.setRowHeight(row_idx, 26)

        total = len(self._videos)
        if keyword or sort_key != "newest":
            self.video_count_label.setText(
                f"Hiện {len(rows)}/{total} video"
                + (f" (lọc: \"{self.video_filter_input.text().strip()}\")" if keyword else "")
            )
        else:
            self.video_count_label.setText(f"Tổng: {total} video")

    def show_overlay(self, text: str = "Đang xử lý...") -> None:
        if self._overlay is None:
            self._overlay = QtWidgets.QWidget(self)
            self._overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
            self._overlay.setStyleSheet("background: rgba(0,0,0,0.25);")
            self._overlay.setGeometry(self.rect())
            self._overlay_label = QtWidgets.QLabel("", self._overlay)
            self._overlay_label.setStyleSheet("color: white; font-weight: 800; background: transparent;")
            self._overlay_label.setAlignment(QtCore.Qt.AlignCenter)
            self._overlay_label.setWordWrap(True)
            self._overlay_label.setGeometry(0, 0, self.width(), self.height())
            self._overlay.hide()
        self._overlay_label.setText(text)
        self._overlay.setGeometry(self.rect())
        self._overlay.show()

    def hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()

    def show_success(self, message: str) -> None:
        QtWidgets.QMessageBox.information(self, "Thành công", message)

    def show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.warning(self, "Lỗi", message)

    def set_refresh_enabled(self, enabled: bool) -> None:
        self.refresh_btn.setEnabled(enabled)
        self.stats_btn.setEnabled(enabled)
        self.video_refresh_btn.setEnabled(enabled)

    def set_videos_loading(self, loading: bool) -> None:
        self.video_refresh_btn.setEnabled(not loading)
        self.video_refresh_btn.setText("Đang tải video..." if loading else "Tải lại danh sách")
        if loading:
            self.video_count_label.setText("Đang tải danh sách video...")

    def set_stats_loading(self, loading: bool) -> None:
        self.stats_btn.setEnabled(not loading)
        self.stats_btn.setText("Đang cập nhật..." if loading else "Cập nhật số liệu")
        if loading:
            self.updated_at_label.setText("Đang cập nhật số liệu...")

    def set_refresh_text(self, text: str) -> None:
        self.refresh_btn.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._overlay is not None and self._overlay.isVisible():
            self._overlay.setGeometry(self.rect())
            if self._overlay_label:
                self._overlay_label.setGeometry(0, 0, self.width(), self.height())
