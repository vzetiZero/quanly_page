from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtWidgets


class PageDetailDialog(QtWidgets.QDialog):
    details_ready = QtCore.pyqtSignal(dict)
    details_error = QtCore.pyqtSignal(str)

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

        self.setWindowTitle(f"Chi tiết Page: {page_name}")
        self.resize(800, 650)
        self.setMinimumSize(700, 500)
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
            """
        )
        self._build_ui()
        self.details_ready.connect(self._on_details_ready)
        self.details_error.connect(self._on_details_error)

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
            ("were_here_count", "Were Here"), ("overall_star_rating", "Đánh giá"),
            ("rating_count", "Số đánh giá"),
        ]:
            lbl = QtWidgets.QLabel("N/A")
            lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            counts_layout.addRow(f"{display_name}:", lbl)
            self.labels[field_name] = lbl
        info_splitter.addWidget(counts_group)
        info_splitter.setStretchFactor(0, 3)
        info_splitter.setStretchFactor(1, 2)
        layout.addWidget(info_splitter)

        history_group = QtWidgets.QGroupBox("Lịch sử cập nhật")
        history_layout = QtWidgets.QVBoxLayout(history_group)
        self.history_table = QtWidgets.QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Thời gian", "Followers", "Likes", "Talking", "Were Here", "Đánh giá"])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setMaximumHeight(200)
        history_layout.addWidget(self.history_table)
        layout.addWidget(history_group)

        close_btn = QtWidgets.QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        close_btn.setMaximumWidth(120)
        close_layout = QtWidgets.QHBoxLayout()
        close_layout.addStretch(1)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)

    def _on_refresh_clicked(self) -> None:
        if self._presenter:
            self._presenter.refresh(self.page_id, self.page_name, self.access_token)

    # ── Nhận kết quả từ luồng nền qua signal (an toàn luồng) ──────
    def _on_details_ready(self, data: Dict[str, Any]) -> None:
        self.hide_overlay()
        self.set_refresh_enabled(True)
        self.set_refresh_text("Cập nhật")
        if data:
            self.populate_info(data)
        self._show_field_errors(data)
        if self._presenter:
            self._presenter.load_history(self.page_id)

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

        for key in ["followers_count", "fan_count", "likes_count", "talking_about_count", "were_here_count", "overall_star_rating", "rating_count"]:
            if key in self.labels:
                self.labels[key].setText(_fmt(data.get(key)))

    def populate_history(self, history: List[Dict[str, Any]]) -> None:
        self.history_table.setRowCount(0)
        for row_data in history:
            row_idx = self.history_table.rowCount()
            self.history_table.insertRow(row_idx)
            fetched_at = str(row_data.get("fetched_at", ""))[:19].replace("T", " ")
            def _safe_int(val: Any) -> str:
                if val is None:
                    return "N/A"
                try:
                    return f"{int(val):,}"
                except (ValueError, TypeError):
                    return str(val)
            def _safe_float(val: Any) -> str:
                if val is None:
                    return "N/A"
                try:
                    return f"{float(val):.1f}"
                except (ValueError, TypeError):
                    return str(val)
            values = [
                fetched_at, _safe_int(row_data.get("followers_count")),
                _safe_int(row_data.get("likes_count")), _safe_int(row_data.get("talking_about_count")),
                _safe_int(row_data.get("were_here_count")), _safe_float(row_data.get("overall_star_rating")),
            ]
            for col_idx, value in enumerate(values):
                self.history_table.setItem(row_idx, col_idx, QtWidgets.QTableWidgetItem(value))

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

    def set_refresh_text(self, text: str) -> None:
        self.refresh_btn.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._overlay is not None and self._overlay.isVisible():
            self._overlay.setGeometry(self.rect())
            if self._overlay_label:
                self._overlay_label.setGeometry(0, 0, self.width(), self.height())
