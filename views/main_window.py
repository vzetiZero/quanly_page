import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from views.widgets import (
    SelectablePageTable,
    apply_small_button_style,
    MAIN_STYLESHEET,
    PAGE_HEADERS,
    PAGE_COL_SELECT,
    PAGE_COL_INDEX,
    PAGE_COL_NAME,
    PAGE_COL_TOKEN_STATUS,
    PAGE_COL_POST_STATUS,
    PAGE_COL_ACCOUNT,
    PAGE_COL_ID,
    PAGE_COL_OPEN,
    PAGE_COL_ACCESS_TOKEN,
    PAGE_COL_INFO,
)
from views.log_tab import LogTab
from views.recent_posts_tab import RecentPostsTab
from views.settings_tab import SettingsTab
from views.license_dialog import TrialLicenseDialog
from views.stats_tab import StatsTab

CONFIG_HEADERS = ["Page", "Tiêu đề", "Mô tả", "Đường dẫn video", "Comment", "Đường dẫn ảnh comment", "Loại đăng", "Thời gian đăng", "Trạng thái", "Link"]
CONFIG_PAGE_COL = 0
CONFIG_TITLE_COL = 1
CONFIG_DESCRIPTION_COL = 2
CONFIG_VIDEO_COL = 3
CONFIG_COMMENT_COL = 4
CONFIG_STATUS_COL = 8
CONFIG_LINK_COL = 9
CONFIG_COMMENT_IMAGE_COL = 5
CONFIG_POST_TYPE_COL = 6
CONFIG_SCHEDULE_COL = 7

TOKEN_STATUS_LABELS = {
    "Valid": "Còn hiệu lực",
    "Cache": "Cache",
    "Lỗi": "Hết hiệu lực",
    "Hết hạn": "Hết hiệu lực",
}
TOKEN_STATUS_COLORS = {
    "Valid": "#16a34a",
    "Cache": "#2563eb",
    "Lỗi": "#dc2626",
    "Hết hạn": "#dc2626",
}

# Màu trạng thái cho hàng đợi đăng: (màu chữ, màu nền)
CONFIG_STATUS_STYLES = {
    "Thành công": ("#15803d", "#dcfce7"),
    "Thất bại": ("#b91c1c", "#fee2e2"),
    "Lỗi": ("#b91c1c", "#fee2e2"),
    "Đang đăng": ("#b45309", "#fef3c7"),
    "Đang chờ lịch": ("#1d4ed8", "#dbeafe"),
    "Chờ đăng": ("#1d4ed8", "#dbeafe"),
    "Đã đăng trước đó": ("#64748b", "#f1f5f9"),
    "Bỏ qua": ("#64748b", "#f1f5f9"),
    "Hết video mới": ("#b91c1c", "#fee2e2"),
    "Không có video": ("#b91c1c", "#fee2e2"),
}


class FacebookPageManagerWindow(QtWidgets.QMainWindow):
    config_status_changed = QtCore.pyqtSignal(str, str)
    config_link_changed = QtCore.pyqtSignal(str, str)

    def __init__(self, container: Any) -> None:
        super().__init__()
        self._container = container
        self._presenter = None
        self.setWindowTitle("Quản lý Page Facebook đa tài khoản")
        self.resize(1400, 950)
        self.setStyleSheet(MAIN_STYLESHEET)

        self.status_label = QtWidgets.QLabel("Sẵn sàng")
        self.trial_status_label = QtWidgets.QLabel("Trial: đang kiểm tra...")
        self.trial_status_label.setStyleSheet("background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 6px 10px; color: #334155; font-weight: 800;")

        self._last_applied_comment = ""

        self._build_ui()
        self.config_status_changed.connect(self._apply_config_status)
        self.config_link_changed.connect(self._apply_config_link)

    def set_presenter(self, presenter: Any) -> None:
        self._presenter = presenter
        self._bind_events()

    # ── Luôn đọc/ghi trực tiếp vào presenter (tránh tham chiếu cũ khi dict bị gán lại) ──
    @property
    def page_selection_states(self) -> Dict[str, bool]:
        presenter = getattr(self, "_presenter", None)
        if presenter is None:
            return {}
        return presenter.page_list.page_selection_states

    @page_selection_states.setter
    def page_selection_states(self, value: Dict[str, bool]) -> None:
        presenter = getattr(self, "_presenter", None)
        if presenter is not None:
            presenter.page_list.page_selection_states = value

    def _update_page_selection_summary(self) -> None:
        if self._presenter:
            total = len(self._presenter.page_list.pages)
            selected = sum(
                1 for p in self._presenter.page_list.pages
                if self.page_selection_states.get(self._presenter.page_list.page_key(p), False)
            )
            self.page_selection_summary_label.setText(f"Đã chọn {selected}/{total} page")

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self.tabs)

        self._build_post_tab()
        self._build_config_tab()
        self._build_stats_tab()
        self._build_system_tabs()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.statusBar().addPermanentWidget(self.trial_status_label)

    def _build_post_tab(self) -> None:
        post_tab = QtWidgets.QWidget()
        post_layout = QtWidgets.QVBoxLayout(post_tab)
        post_layout.setContentsMargins(0, 0, 0, 0)
        post_layout.setSpacing(3)

        token_row = QtWidgets.QHBoxLayout()
        self.token_input = QtWidgets.QPlainTextEdit()
        self.token_input.setPlaceholderText("Nhập access_token hoặc dán từ file txt (mỗi token 1 dòng)")
        self.token_input.setMaximumHeight(40)
        token_row.addWidget(self.token_input)
        self.load_tokens_btn = QtWidgets.QPushButton("Nhập file txt")
        token_row.addWidget(self.load_tokens_btn)
        self.load_pages_btn = QtWidgets.QPushButton("Lấy danh sách page")
        token_row.addWidget(self.load_pages_btn)
        post_layout.addLayout(token_row)

        middle = QtWidgets.QGroupBox("Danh sách page")
        middle_layout = QtWidgets.QVBoxLayout(middle)
        middle_layout.setContentsMargins(6, 6, 6, 6)
        middle_layout.setSpacing(4)

        search_row = QtWidgets.QHBoxLayout()
        self.page_search_input = QtWidgets.QLineEdit()
        self.page_search_input.setPlaceholderText("Tìm page theo tên hoặc ID")
        search_row.addWidget(self.page_search_input)
        clear_search_btn = QtWidgets.QPushButton("Xóa")
        apply_small_button_style(clear_search_btn)
        search_row.addWidget(clear_search_btn)
        middle_layout.addLayout(search_row)

        self.page_table = SelectablePageTable(self)
        self.page_table.window_ref = self
        self.page_table.setColumnCount(len(PAGE_HEADERS))
        self.page_table.setHorizontalHeaderLabels(PAGE_HEADERS)
        self.page_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.page_table.setAlternatingRowColors(True)
        self.page_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.page_table.verticalHeader().setDefaultSectionSize(26)
        self.page_table.verticalHeader().setVisible(False)
        page_header = self.page_table.horizontalHeader()
        page_header.setStretchLastSection(False)
        page_header.setSectionResizeMode(PAGE_COL_NAME, QtWidgets.QHeaderView.Stretch)
        self.page_table.setColumnWidth(PAGE_COL_SELECT, 52)
        self.page_table.setColumnWidth(PAGE_COL_INDEX, 44)
        self.page_table.setColumnWidth(PAGE_COL_TOKEN_STATUS, 118)
        self.page_table.setColumnWidth(PAGE_COL_POST_STATUS, 118)
        self.page_table.setColumnWidth(PAGE_COL_ACCOUNT, 150)
        self.page_table.setColumnWidth(PAGE_COL_ID, 170)
        self.page_table.setColumnWidth(PAGE_COL_OPEN, 74)
        self.page_table.setColumnWidth(PAGE_COL_ACCESS_TOKEN, 170)
        self.page_table.setColumnWidth(PAGE_COL_INFO, 120)
        self.page_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.page_table.cellDoubleClicked.connect(self._on_page_double_clicked)
        self.page_table.cellClicked.connect(self._on_page_cell_clicked)
        middle_layout.addWidget(self.page_table)

        pag_row = QtWidgets.QHBoxLayout()
        self.page_prev_btn = QtWidgets.QPushButton("‹ Trước")
        apply_small_button_style(self.page_prev_btn)
        self.page_next_btn = QtWidgets.QPushButton("Sau ›")
        apply_small_button_style(self.page_next_btn)
        self.page_size_combo = QtWidgets.QComboBox()
        self.page_size_combo.addItems(["10", "20", "50"])
        self.page_size_combo.setCurrentText("20")
        self.page_page_label = QtWidgets.QLabel("Trang 1 / 1")
        self.page_selection_summary_label = QtWidgets.QLabel("Đã chọn 0/0 page")
        self.page_selection_summary_label.setStyleSheet("color: #0284c7; font-weight: 800;")
        pag_row.addWidget(self.page_prev_btn)
        pag_row.addWidget(self.page_next_btn)
        pag_row.addStretch()
        pag_row.addWidget(self.page_selection_summary_label)
        pag_row.addWidget(QtWidgets.QLabel("Hiển thị"))
        pag_row.addWidget(self.page_size_combo)
        pag_row.addWidget(self.page_page_label)
        middle_layout.addLayout(pag_row)

        ctrl_row = QtWidgets.QHBoxLayout()
        self.select_all_btn = QtWidgets.QPushButton("Chọn tất cả")
        apply_small_button_style(self.select_all_btn)
        ctrl_row.addWidget(self.select_all_btn)
        self.clear_btn = QtWidgets.QPushButton("Bỏ chọn")
        apply_small_button_style(self.clear_btn)
        ctrl_row.addWidget(self.clear_btn)
        ctrl_row.addStretch(1)
        middle_layout.addLayout(ctrl_row)

        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self.status_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(middle)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, True)
        post_layout.addWidget(splitter)
        self.tabs.addTab(post_tab, "Đăng bài")

    def _build_config_tab(self) -> None:
        self.config_tab = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(self.config_tab)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(6)
        config_layout.addWidget(self._build_content_group())
        config_layout.addWidget(self._build_queue_group(), 1)
        self.tabs.addTab(self.config_tab, "Cấu hình")

    def _build_content_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Nội dung & nguồn video")
        cf_layout = QtWidgets.QGridLayout(group)
        cf_layout.setContentsMargins(6, 6, 6, 6)
        cf_layout.setSpacing(4)

        cf_layout.addWidget(QtWidgets.QLabel("Nội dung bài đăng:"), 0, 0)
        self.quick_post_content_input = QtWidgets.QPlainTextEdit()
        self.quick_post_content_input.setPlaceholderText("Nội dung bài đăng chung")
        self.quick_post_content_input.setMaximumHeight(44)
        cf_layout.addWidget(self.quick_post_content_input, 0, 1, 1, 3)

        cf_layout.addWidget(QtWidgets.QLabel("Nội dung comment:"), 1, 0)
        self.quick_comment_content_input = QtWidgets.QPlainTextEdit()
        self.quick_comment_content_input.setPlaceholderText("Nội dung comment chung")
        self.quick_comment_content_input.setMaximumHeight(44)
        cf_layout.addWidget(self.quick_comment_content_input, 1, 1, 1, 3)

        cf_layout.addWidget(QtWidgets.QLabel("Thư mục video:"), 2, 0)
        self.video_folder_input = QtWidgets.QLineEdit()
        self.video_folder_input.setPlaceholderText("Chọn thư mục chứa video")
        cf_layout.addWidget(self.video_folder_input, 2, 1)
        self.select_video_folder_btn = QtWidgets.QPushButton("Chọn")
        apply_small_button_style(self.select_video_folder_btn)
        cf_layout.addWidget(self.select_video_folder_btn, 2, 2)

        self.choose_videos_btn = QtWidgets.QPushButton("Chọn video")
        apply_small_button_style(self.choose_videos_btn)
        cf_layout.addWidget(self.choose_videos_btn, 2, 3)

        cf_layout.addWidget(QtWidgets.QLabel("Thư mục ảnh comment:"), 3, 0)
        self.comment_image_folder_input = QtWidgets.QLineEdit()
        self.comment_image_folder_input.setPlaceholderText("Chọn thư mục chứa ảnh comment")
        cf_layout.addWidget(self.comment_image_folder_input, 3, 1)
        self.select_comment_image_folder_btn = QtWidgets.QPushButton("Chọn")
        apply_small_button_style(self.select_comment_image_folder_btn)
        cf_layout.addWidget(self.select_comment_image_folder_btn, 3, 2)

        self.posted_videos_btn = QtWidgets.QPushButton("Video đã đăng")
        apply_small_button_style(self.posted_videos_btn)
        cf_layout.addWidget(self.posted_videos_btn, 3, 3)

        self.skip_missing_checkbox = QtWidgets.QCheckBox("Bỏ qua page khi video không đủ")
        self.skip_missing_checkbox.setChecked(True)
        cf_layout.addWidget(self.skip_missing_checkbox, 4, 0, 1, 2)

        self.use_comment_images_checkbox = QtWidgets.QCheckBox("Dùng ảnh comment")
        self.use_comment_images_checkbox.setChecked(False)
        cf_layout.addWidget(self.use_comment_images_checkbox, 4, 2)

        self.apply_content_btn = QtWidgets.QPushButton("Áp dụng nội dung vào hàng đợi")
        apply_small_button_style(self.apply_content_btn)
        cf_layout.addWidget(self.apply_content_btn, 5, 2, 1, 2)

        return group

    def _build_queue_group(self) -> QtWidgets.QGroupBox:
        queue_group = QtWidgets.QGroupBox("Hàng đợi đăng (xem lại & đăng)")
        queue_layout = QtWidgets.QVBoxLayout(queue_group)
        queue_layout.setContentsMargins(6, 6, 6, 6)
        queue_layout.setSpacing(4)

        ctrl_row = QtWidgets.QHBoxLayout()
        self.add_row_btn = QtWidgets.QPushButton("Thêm dòng")
        apply_small_button_style(self.add_row_btn)
        ctrl_row.addWidget(self.add_row_btn)
        self.clear_config_btn = QtWidgets.QPushButton("Xóa tất cả dòng")
        apply_small_button_style(self.clear_config_btn)
        ctrl_row.addWidget(self.clear_config_btn)
        ctrl_row.addStretch(1)
        self.stop_btn = QtWidgets.QPushButton("Dừng")
        apply_small_button_style(self.stop_btn)
        self.stop_btn.setEnabled(False)
        ctrl_row.addWidget(self.stop_btn)
        self.post_config_btn = QtWidgets.QPushButton("Đăng")
        apply_small_button_style(self.post_config_btn)
        ctrl_row.addWidget(self.post_config_btn)
        queue_layout.addLayout(ctrl_row)

        self.config_table = QtWidgets.QTableWidget(0, len(CONFIG_HEADERS))
        self.config_table.setHorizontalHeaderLabels(CONFIG_HEADERS)
        self.config_table.setAlternatingRowColors(True)
        self.config_table.setEditTriggers(QtWidgets.QAbstractItemView.AllEditTriggers)
        self.config_table.setColumnWidth(2, 200)
        self.config_table.setColumnWidth(3, 220)
        self.config_table.setColumnWidth(CONFIG_POST_TYPE_COL, 110)
        self.config_table.setColumnWidth(CONFIG_STATUS_COL, 110)
        self.config_table.setColumnWidth(CONFIG_LINK_COL, 220)
        self.config_table.verticalHeader().setDefaultSectionSize(26)
        self.config_table.verticalHeader().setVisible(False)
        self.config_table.setMinimumHeight(160)
        self.config_table.horizontalHeader().setStretchLastSection(True)
        self.config_table.cellDoubleClicked.connect(self._on_config_cell_double_clicked)
        queue_layout.addWidget(self.config_table)
        return queue_group

    def _build_stats_tab(self) -> None:
        self.stats_tab = StatsTab()
        self.stats_tab.set_callbacks(self._on_refresh_stats_all, self._on_refresh_stats_selected)
        self.tabs.addTab(self.stats_tab, "Thống kê")

    def _build_system_tabs(self) -> None:
        self.log_tab = LogTab()
        self.tabs.addTab(self.log_tab, "Log")

        self.recent_posts_tab = RecentPostsTab()
        self.tabs.addTab(self.recent_posts_tab, "Lịch sử đăng")

        self.settings_tab = SettingsTab()
        self.tabs.addTab(self.settings_tab, "Cài đặt chung")

    def _bind_events(self) -> None:
        p = self._presenter
        self.load_tokens_btn.clicked.connect(self._on_load_tokens)
        self.load_pages_btn.clicked.connect(p.on_load_pages)
        self.select_all_btn.clicked.connect(p.page_list.select_all)
        self.clear_btn.clicked.connect(p.page_list.clear_selection)
        self.stop_btn.clicked.connect(p.on_stop)
        self.page_prev_btn.clicked.connect(p.page_list.go_prev)
        self.page_next_btn.clicked.connect(p.page_list.go_next)
        self.page_size_combo.currentTextChanged.connect(lambda v: p.page_list.set_page_size(int(v)))
        self.page_search_input.textChanged.connect(p.page_list.filter_pages)
        self.settings_tab.check_tokens_btn.clicked.connect(lambda: p.settings.check_tokens(p.page_list.get_token_input()))
        self.settings_tab.check_permissions_btn.clicked.connect(self._on_check_permissions)
        self.settings_tab.activate_trial_btn.clicked.connect(self._on_trial_dialog)
        self.settings_tab.refresh_token_btn.clicked.connect(p.on_refresh_tokens)
        self.settings_tab.clear_cache_btn.clicked.connect(p.on_clear_cache)
        self.add_row_btn.clicked.connect(lambda: p.config.add_row("video"))
        self.clear_config_btn.clicked.connect(self._on_clear_config)
        self.post_config_btn.clicked.connect(self._on_post_from_config)
        self.page_table.customContextMenuRequested.connect(self._on_page_context_menu)
        self.select_video_folder_btn.clicked.connect(self._select_video_folder)
        self.select_comment_image_folder_btn.clicked.connect(self._select_comment_image_folder)
        self.choose_videos_btn.clicked.connect(self._on_choose_videos)
        self.posted_videos_btn.clicked.connect(self._on_view_posted_videos)
        self.apply_content_btn.clicked.connect(self._on_apply_quick_content)

    def _on_load_tokens(self) -> None:
        dialog = QtWidgets.QFileDialog(self)
        dialog.setWindowTitle("Chọn file txt")
        dialog.setNameFilter("Text files (*.txt)")
        dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        if dialog.exec_():
            paths = dialog.selectedFiles()
            if paths:
                self._presenter.on_tokens_file_loaded(paths[0])

    def _on_trial_dialog(self) -> None:
        dialog = TrialLicenseDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._presenter.on_trial_activate(dialog.code)
        else:
            self._presenter._lock_trial_expired()

    def _on_check_permissions(self) -> None:
        from services.page_service import PERMISSION_LABELS, REQUIRED_PERMISSIONS

        tokens = [t.strip() for t in self.get_token_input().splitlines() if t.strip()]
        if not tokens:
            self.show_warning("Thiếu token", "Vui lòng nhập ít nhất 1 access token")
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            results = [(token, self._container.page_service.check_permissions(token)) for token in tokens]
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        lines = []
        for token, result in results:
            prefix = token[:10] + "…"
            if not result.get("ok"):
                lines.append(f"• {prefix}: LỖI – {result.get('error')}")
                continue
            missing = result.get("missing", [])
            if missing:
                labels = ", ".join(f"{PERMISSION_LABELS.get(p, p)} [{p}]" for p in missing)
                lines.append(f"• {prefix}: thiếu {len(missing)}/{len(REQUIRED_PERMISSIONS)} quyền\n   {labels}")
            else:
                lines.append(f"• {prefix}: đủ {len(REQUIRED_PERMISSIONS)}/{len(REQUIRED_PERMISSIONS)} quyền ✔")
        self.show_info("Kiểm tra quyền token", "\n".join(lines) if lines else "Không có kết quả")

    def _on_clear_config(self) -> None:
        self.config_table.setRowCount(0)

    def _config_cell_text(self, row: int, col: int) -> str:
        item = self.config_table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _on_config_cell_double_clicked(self, row: int, col: int) -> None:
        """Nhấp đôi vào cột Link để mở bài đăng trên trình duyệt."""
        if col != CONFIG_LINK_COL:
            return
        link = self._config_cell_text(row, CONFIG_LINK_COL)
        if not link:
            self.show_warning("Chưa có link", "Dòng này chưa đăng thành công nên chưa có link.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))

    def _sync_queue_titles(self) -> int:
        """Đặt cột 'Tiêu đề' theo Nội dung bài đăng.

        - Có "Nội dung bài đăng" -> Tiêu đề = nội dung đó.
        - Không có -> Tiêu đề = tên file video (fallback).
        Chỉ ghi đè ô đang trống hoặc đang là tiêu đề tự sinh từ tên video,
        không đụng tới tiêu đề người dùng tự sửa.
        """
        post_content = self.quick_post_content_input.toPlainText().strip()
        changed = 0
        for row_idx in range(self.config_table.rowCount()):
            video_path = self._config_cell_text(row_idx, CONFIG_VIDEO_COL)
            auto_title = Path(video_path).stem if video_path else ""
            current = self._config_cell_text(row_idx, CONFIG_TITLE_COL)
            if post_content:
                if not current or current == auto_title:
                    self._set_table_value(row_idx, CONFIG_TITLE_COL, post_content)
                    changed += 1
            elif not current and auto_title:
                self._set_table_value(row_idx, CONFIG_TITLE_COL, auto_title)
                changed += 1
        return changed

    def _on_apply_quick_content(self) -> None:
        """Nút 'Áp dụng nội dung vào hàng đợi' (ghi đè có chủ đích).

        - Nội dung bài đăng -> cột **Tiêu đề** (KHÔNG điền Mô tả/Comment).
        - Nội dung comment -> cột **Comment**; nếu ô này để trống thì các
          comment do lần áp dụng trước điền sẽ được xoá, còn ô người dùng
          tự gõ thì giữ nguyên.
        """
        post_content = self.quick_post_content_input.toPlainText().strip()
        comment_content = self.quick_comment_content_input.toPlainText().strip()
        if not post_content and not comment_content:
            self.show_warning("Thiếu nội dung", "Vui lòng nhập 'Nội dung bài đăng' hoặc 'Nội dung comment'.")
            return
        row_count = self.config_table.rowCount()
        if row_count == 0:
            self.show_warning("Hàng đợi trống", "Chưa có page trong hàng đợi. Hãy chọn page rồi bấm 'Chọn video' trước.")
            return

        previous_auto_comment = self._last_applied_comment
        title_changed = 0
        comment_changed = 0
        for row_idx in range(row_count):
            if post_content:
                self._set_table_value(row_idx, CONFIG_TITLE_COL, post_content)
                title_changed += 1
            # Comment chỉ nhận đúng nội dung comment; nếu để trống thì xoá
            # các giá trị do lần áp dụng trước điền vào (không xoá ô tự gõ).
            current_comment = self._config_cell_text(row_idx, CONFIG_COMMENT_COL)
            if comment_content:
                if current_comment != comment_content:
                    self._set_table_value(row_idx, CONFIG_COMMENT_COL, comment_content)
                    comment_changed += 1
            elif previous_auto_comment and current_comment == previous_auto_comment:
                self._set_table_value(row_idx, CONFIG_COMMENT_COL, "")
                comment_changed += 1

        self._last_applied_comment = comment_content
        self.show_status(
            f"Đã áp dụng vào {row_count} dòng: {title_changed} tiêu đề, {comment_changed} comment"
        )

    def _fill_empty_content_from_quick(self) -> None:
        """Khi bấm Đăng: tự điền nội dung chung vào ô còn trống (không ghi đè ô đã có)."""
        post_content = self.quick_post_content_input.toPlainText().strip()
        comment_content = self.quick_comment_content_input.toPlainText().strip()
        self._sync_queue_titles()
        if not comment_content:
            return
        for row_idx in range(self.config_table.rowCount()):
            if not self._config_cell_text(row_idx, CONFIG_COMMENT_COL):
                self._set_table_value(row_idx, CONFIG_COMMENT_COL, comment_content)

    def _select_video_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục video")
        if folder:
            self.video_folder_input.setText(folder)

    def _select_comment_image_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục ảnh comment")
        if folder:
            self.comment_image_folder_input.setText(folder)

    def _on_choose_videos(self) -> None:
        from services.config_service import VIDEO_EXTENSIONS
        from views.video_selection_dialog import VideoSelectionDialog

        folder = self.video_folder_input.text().strip()
        if not folder:
            self.show_warning("Thiếu thư mục", "Vui lòng chọn thư mục chứa video trước.")
            return

        videos = self._container.config_service.collect_media_files(folder, VIDEO_EXTENSIONS)
        if not videos:
            self.show_warning("Không có video", "Không tìm thấy video nào trong thư mục đã chọn.")
            return

        pages = self._presenter.page_list.get_selected_pages(use_all=False)
        if not pages:
            self.show_warning("Chưa chọn page", "Vui lòng chọn ít nhất 1 page (hoặc bấm 'Chọn tất cả').")
            return

        counts = self._container.post_service.summarize_posted_for_pages(videos, pages)
        dialog = VideoSelectionDialog(videos, counts, len(pages), self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        selected = dialog.selected_videos()
        if not selected:
            self.show_warning("Chưa chọn video", "Vui lòng chọn ít nhất 1 video để chia.")
            return

        entries = self._container.post_service.plan_video_assignment(pages, selected)
        self._presenter.config.populate_assignment_rows(entries)
        self._sync_queue_titles()
        self.tabs.setCurrentWidget(self.config_tab)

        ready = sum(1 for entry in entries if entry.get("video_path"))
        skipped = len(entries) - ready
        message = f"Đã chia video: {ready}/{len(entries)} page có video mới"
        if skipped:
            message += f", {skipped} page hết video mới"
        self.show_status(message)

    def _on_view_posted_videos(self) -> None:
        from views.video_selection_dialog import PostedVideosDialog

        pages = self._presenter.page_list.get_selected_pages(use_all=False)
        page_keys = [p.get("name") or p.get("id") for p in pages] if pages else None
        rows = self._container.db.load_video_history(page_keys)
        PostedVideosDialog(rows, self).exec_()

    def _on_auto_map(self) -> None:
        video_folder = self.video_folder_input.text().strip()
        comment_folder = self.comment_image_folder_input.text().strip()
        if not video_folder and not comment_folder:
            self.show_warning("Thiếu dữ liệu", "Vui lòng chọn thư mục video hoặc ảnh comment")
            return

        from services.config_service import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
        video_files = self._container.config_service.collect_media_files(video_folder, VIDEO_EXTENSIONS) if video_folder else []
        comment_files = self._container.config_service.collect_media_files(comment_folder, IMAGE_EXTENSIONS) if comment_folder else []

        rows = []
        for row_idx in range(self.config_table.rowCount()):
            page_name = self.config_table.item(row_idx, 0).text().strip() if self.config_table.item(row_idx, 0) else ""
            rows.append({"page": page_name, "row_idx": row_idx})

        for i, row in enumerate(rows):
            if video_files and i < len(video_files):
                self._set_table_value(row["row_idx"], CONFIG_VIDEO_COL, video_files[i])
            if comment_files and i < len(comment_files):
                self._set_table_value(row["row_idx"], CONFIG_COMMENT_IMAGE_COL, comment_files[i])
        # Tiêu đề lấy từ Nội dung bài đăng, thiếu thì mới lấy tên file video.
        self._sync_queue_titles()

    def _on_post_from_config(self) -> None:
        self._fill_empty_content_from_quick()
        self.stop_btn.setEnabled(True)
        self._presenter.config.start_posting(
            pages=self._presenter.page_list.pages,
            base_url=self._container.base_url,
            concurrency_enabled=self.settings_tab.concurrent_enabled_checkbox.isChecked(),
            concurrency_threads=self.settings_tab.concurrent_threads_spin.value(),
            concurrency_delay=self.settings_tab.concurrent_delay_spin.value(),
            on_complete=self._on_posting_complete,
            on_link=self.update_config_link,
        )

    def _on_posting_complete(self, success: int, fail: int, failures: Optional[List[tuple]] = None) -> None:
        self.stop_btn.setEnabled(False)
        self.post_config_btn.setEnabled(True)
        self.post_config_btn.setText("Đăng")
        self.status_label.setText(f"Hoàn tất: thành công {success}, thất bại {fail}")
        self._load_local_stats()
        if failures:
            # Ghi lý do vào tooltip của ô Trạng thái + log ra màn hình để biết
            # chính xác page nào hỏng vì gì.
            lines = []
            for page_name, reason in failures:
                text = str(reason or "").strip() or "Lỗi không xác định"
                lines.append(f"• {page_name}: {text}")
                self._set_config_status_tooltip(page_name, text)
                logger.error("Đăng thất bại | page=%s | %s", page_name, text)
            self.show_warning(
                f"Đăng xong: {success} thành công, {fail} thất bại",
                "\n".join(lines),
            )

    def _set_config_status_tooltip(self, page_name: str, detail: str) -> None:
        for row in range(self.config_table.rowCount()):
            item = self.config_table.item(row, CONFIG_PAGE_COL)
            if item is not None and item.text().strip() == page_name:
                status_item = self.config_table.item(row, CONFIG_STATUS_COL)
                if status_item is not None:
                    status_item.setToolTip(detail)
                return

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is getattr(self, "stats_tab", None):
            self._load_local_stats()

    def _load_local_stats(self) -> None:
        try:
            pages = self._presenter.page_list.pages
            rows = self._container.stats_service.local_rows(pages)
            self.stats_tab.load_rows(rows)
            self.stats_tab.set_status(f"{len(rows)} page")
        except Exception:
            pass

    def _on_refresh_stats_all(self) -> None:
        self._refresh_stats(use_selected=False)

    def _on_refresh_stats_selected(self) -> None:
        self._refresh_stats(use_selected=True)

    def _refresh_stats(self, use_selected: bool) -> None:
        if use_selected:
            pages = self._presenter.page_list.get_selected_pages(use_all=False)
        else:
            pages = self._presenter.page_list.pages
        if not pages:
            self.show_warning("Chưa có page", "Vui lòng chọn ít nhất 1 page hoặc lấy danh sách page trước.")
            return
        self.stats_tab.set_busy(True)
        self.stats_tab.mark_checking([p.get("name") or p.get("id") for p in pages])

        def worker():
            try:
                rows = self._container.stats_service.refresh(
                    pages,
                    on_page=lambda row: self.stats_tab.stats_progress.emit(row),
                )
                self.stats_tab.stats_ready.emit(rows)
            except Exception:
                self.stats_tab.stats_ready.emit([])

        threading.Thread(target=worker, daemon=True).start()

    def _on_page_double_clicked(self, row: int, col: int) -> None:
        page_id = self._page_cell_text(row, PAGE_COL_ID)
        if not page_id:
            return
        page_name = self._page_cell_text(row, PAGE_COL_NAME) or page_id
        page = self._presenter.page_list.get_page_by_key(self._page_row_key(row)) or {}
        access_token = page.get("access_token") or self._page_cell_text(row, PAGE_COL_ACCESS_TOKEN)
        self._show_page_detail(page_id, page_name, access_token)

    def _on_page_cell_clicked(self, row: int, col: int) -> None:
        if col == PAGE_COL_OPEN:
            page_id = self._page_cell_text(row, PAGE_COL_ID)
            if page_id:
                url = f"https://www.facebook.com/{page_id}"
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _page_cell_text(self, row: int, col: int) -> str:
        item = self.page_table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _page_row_key(self, row: int) -> str:
        item = self.page_table.item(row, PAGE_COL_SELECT)
        if item is not None:
            key = item.data(QtCore.Qt.UserRole)
            if key:
                return str(key)
        page_id = self._page_cell_text(row, PAGE_COL_ID)
        return f"|{page_id}" if page_id else ""

    def _page_targets(self, row: int) -> List[Dict[str, Any]]:
        """Page mục tiêu: nếu page chuột phải đang được tick thì lấy tất cả page đang tick."""
        key = self._page_row_key(row)
        if not key:
            return []
        clicked_selected = self._presenter.page_list.page_selection_states.get(key, False)
        selected = self._presenter.page_list.get_selected_pages(use_all=False)
        if clicked_selected and len(selected) > 1:
            return selected
        page = self._presenter.page_list.get_page_by_key(key) or {}
        page_id = page.get("id") or self._page_cell_text(row, PAGE_COL_ID)
        return [{
            "key": key,
            "id": page_id,
            "name": page.get("name") or self._page_cell_text(row, PAGE_COL_NAME) or page_id,
            "access_token": page.get("access_token") or self._page_cell_text(row, PAGE_COL_ACCESS_TOKEN),
            "account": page.get("account_label", ""),
        }]

    def _on_page_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.page_table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        targets = self._page_targets(row)
        if not targets:
            return
        clicked_key = self._page_row_key(row)
        clicked_page = self._presenter.page_list.get_page_by_key(clicked_key) or {}
        clicked = {
            "id": clicked_page.get("id") or self._page_cell_text(row, PAGE_COL_ID),
            "name": clicked_page.get("name") or self._page_cell_text(row, PAGE_COL_NAME) or self._page_cell_text(row, PAGE_COL_ID),
            "access_token": clicked_page.get("access_token") or self._page_cell_text(row, PAGE_COL_ACCESS_TOKEN),
        }
        suffix = f" ({len(targets)} page)" if len(targets) > 1 else ""

        menu = QtWidgets.QMenu(self)
        act_video = menu.addAction("Chọn Video đăng" + suffix)
        act_detail = menu.addAction("Chi tiết page")
        act_token = menu.addAction("Kiểm tra token" + suffix)
        menu.addSeparator()
        act_delete = menu.addAction("Xoá page" + suffix)
        chosen = menu.exec_(self.page_table.viewport().mapToGlobal(pos))

        if chosen == act_video:
            self._choose_videos_for_pages(targets)
        elif chosen == act_detail:
            self._show_page_detail(clicked["id"], clicked["name"], clicked["access_token"])
        elif chosen == act_token:
            self._check_pages_tokens(targets)
        elif chosen == act_delete:
            self._remove_pages(targets)

    def _show_page_detail(self, page_id: str, page_name: str, access_token: str) -> None:
        if not access_token:
            self.show_warning("Thiếu token", "Page này không có access token.")
            return
        from views.page_detail_dialog import PageDetailDialog
        from presenters.page_detail_presenter import PageDetailPresenter

        dialog = PageDetailDialog(page_id, page_name, access_token, self)
        presenter = PageDetailPresenter(view=dialog, page_detail_service=self._container.page_detail_service)
        dialog.set_presenter(presenter)
        presenter.load_data(page_id, page_name, access_token)
        dialog.exec_()

    def _choose_videos_for_pages(self, pages: List[Dict[str, Any]]) -> None:
        from services.config_service import VIDEO_EXTENSIONS
        from views.video_selection_dialog import VideoSelectionDialog

        if not pages:
            return
        folder = self.video_folder_input.text().strip()
        if not folder:
            self.show_warning("Thiếu thư mục", "Vui lòng chọn thư mục chứa video trước.")
            return
        videos = self._container.config_service.collect_media_files(folder, VIDEO_EXTENSIONS)
        if not videos:
            self.show_warning("Không có video", "Không tìm thấy video nào trong thư mục đã chọn.")
            return

        counts = self._container.post_service.summarize_posted_for_pages(videos, pages)
        dialog = VideoSelectionDialog(videos, counts, len(pages), self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        selected = dialog.selected_videos()
        if not selected:
            self.show_warning("Chưa chọn video", "Vui lòng chọn ít nhất 1 video.")
            return

        entries = self._container.post_service.plan_video_assignment(pages, selected)
        added, updated, skipped = self._presenter.config.append_assignment_rows(entries)
        self._sync_queue_titles()
        self.show_status(
            f"Đã chia video cho {len(pages)} page: thêm {added}, cập nhật {updated}, bỏ qua {skipped}"
        )

    def _find_page_row_by_key(self, key: str) -> int:
        target = str(key or "")
        if not target:
            return -1
        for row in range(self.page_table.rowCount()):
            item = self.page_table.item(row, PAGE_COL_SELECT)
            if item is not None and str(item.data(QtCore.Qt.UserRole) or "") == target:
                return row
        return -1

    def _check_pages_tokens(self, pages: List[Dict[str, Any]]) -> None:
        if not pages:
            return
        results = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for page in pages:
                key = str(page.get("key") or "")
                page_id = str(page.get("id") or "")
                page_name = page.get("name") or page_id
                token = page.get("access_token") or ""
                if not token:
                    results.append((page_name, False, "Thiếu access token"))
                    continue
                result = self._container.page_service.validate_token(token)
                valid = bool(result.get("valid"))
                results.append((page_name, valid, result.get("error")))
                status = "Valid" if valid else "Lỗi"
                if key:
                    self._presenter.page_list.set_page_status(key, status)
                    row = self._find_page_row_by_key(key)
                else:
                    row = -1
                if row >= 0:
                    self._set_page_token_status(row, status)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if len(results) == 1:
            name, valid, error = results[0]
            if valid:
                self.show_info("Token hợp lệ", f"{name}: token còn hiệu lực.")
            else:
                self.show_warning("Token lỗi", f"{name}: {error or 'Token không hợp lệ'}")
            return
        ok = sum(1 for _, valid, _ in results if valid)
        bad = [name for name, valid, _ in results if not valid]
        message = f"Hợp lệ: {ok}/{len(results)} page"
        if bad:
            message += "\nKhông hợp lệ: " + ", ".join(bad)
        self.show_info("Kết quả kiểm tra token", message)

    def _remove_pages(self, pages: List[Dict[str, Any]]) -> None:
        keys = [str(p.get("key") or "") for p in pages if p.get("key")]
        keys = [k for k in keys if k]
        if not keys:
            return
        label = f"{len(keys)} page" if len(keys) > 1 else (pages[0].get("name") or keys[0])
        if not self.confirm("Xoá page", f"Xoá {label} khỏi danh sách hiện tại?"):
            return
        self._presenter.page_list.remove_pages(keys)
        self.show_status(f"Đã xoá {len(keys)} page khỏi danh sách")

    def _token_status_display(self, status: str) -> tuple:
        label = TOKEN_STATUS_LABELS.get(status, status)
        color = TOKEN_STATUS_COLORS.get(status, TOKEN_STATUS_COLORS["Valid"])
        return label, color

    def _set_page_token_status(self, row: int, status: str) -> None:
        label, color = self._token_status_display(status)
        item = self.page_table.item(row, PAGE_COL_TOKEN_STATUS)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.page_table.setItem(row, PAGE_COL_TOKEN_STATUS, item)
        item.setText(label)
        item.setForeground(QtGui.QColor(color))
        font = item.font()
        font.setBold(True)
        item.setFont(font)

    # ── IMainView interface implementation ────────────────────────

    def show_status(self, text: str) -> None:
        self.status_label.setText(text)

    def show_error(self, title: str, message: str) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.exec_()

    def show_info(self, title: str, message: str) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QtWidgets.QMessageBox.Information)
        msg.exec_()

    def show_warning(self, title: str, message: str) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.exec_()

    def confirm(self, title: str, message: str) -> bool:
        reply = QtWidgets.QMessageBox.question(self, title, message, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        return reply == QtWidgets.QMessageBox.Yes

    def set_controls_enabled(self, enabled: bool) -> None:
        for w in [self.load_tokens_btn, self.load_pages_btn, self.select_all_btn, self.clear_btn, self.token_input, self.page_table, self.config_table, self.add_row_btn, self.clear_config_btn, self.post_config_btn]:
            w.setEnabled(enabled)

    def update_trial_status(self, label: str, style: str) -> None:
        self.trial_status_label.setText(label)
        self.trial_status_label.setStyleSheet(style)

    def get_token_input(self) -> str:
        return self.token_input.toPlainText()

    def set_token_input(self, text: str) -> None:
        self.token_input.setPlainText(text)

    def log(self, message: str, level: str = "info") -> None:
        self.log_tab.append_log(message, level)

    # ── IPageListView interface implementation ────────────────────

    def populate_page_table(self, pages: List[Dict[str, Any]]) -> None:
        self.page_table.blockSignals(True)
        self.page_table.setRowCount(0)
        start = (self._presenter.page_list.current_page - 1) * self._presenter.page_list.page_size
        end = start + self._presenter.page_list.page_size
        paged = pages[start:end]
        self.page_table.setRowCount(len(paged))
        for row, page in enumerate(paged):
            key = self._presenter.page_list.page_key(page)
            selected = self._presenter.page_list.page_selection_states.get(key, False)
            check_item = QtWidgets.QTableWidgetItem()
            check_item.setFlags(check_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            check_item.setCheckState(QtCore.Qt.Checked if selected else QtCore.Qt.Unchecked)
            check_item.setTextAlignment(QtCore.Qt.AlignCenter)
            check_item.setData(QtCore.Qt.UserRole, key)
            self.page_table.setItem(row, PAGE_COL_SELECT, check_item)
            self.page_table.setItem(row, PAGE_COL_INDEX, QtWidgets.QTableWidgetItem(str(start + row + 1)))
            self.page_table.setItem(row, PAGE_COL_NAME, QtWidgets.QTableWidgetItem(page.get("name", "")))
            status = page.get("status", "Valid")
            status_label, status_color = self._token_status_display(status)
            status_item = QtWidgets.QTableWidgetItem(status_label)
            status_item.setForeground(QtGui.QColor(status_color))
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)
            self.page_table.setItem(row, PAGE_COL_TOKEN_STATUS, status_item)
            post_status = self._presenter.page_list.page_post_statuses.get(key, "Chưa đăng")
            post_item = QtWidgets.QTableWidgetItem(post_status)
            if "Success" in post_status:
                post_item.setForeground(QtGui.QColor("#22c55e"))
            elif "Failed" in post_status:
                post_item.setForeground(QtGui.QColor("#ef4444"))
            else:
                post_item.setForeground(QtGui.QColor("#cbd5e1"))
            self.page_table.setItem(row, PAGE_COL_POST_STATUS, post_item)
            self.page_table.setItem(row, PAGE_COL_ACCOUNT, QtWidgets.QTableWidgetItem(page.get("account_label", "")))
            page_id_item = QtWidgets.QTableWidgetItem(str(page.get("id", "")))
            page_id_item.setForeground(QtGui.QColor("#2563eb"))
            self.page_table.setItem(row, PAGE_COL_ID, page_id_item)
            open_item = QtWidgets.QTableWidgetItem("Mở")
            open_item.setForeground(QtGui.QColor("#0284c7"))
            open_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.page_table.setItem(row, PAGE_COL_OPEN, open_item)
            self.page_table.setItem(row, PAGE_COL_ACCESS_TOKEN, QtWidgets.QTableWidgetItem(page.get("access_token", "")))
            self.page_table.setItem(row, PAGE_COL_INFO, QtWidgets.QTableWidgetItem("N/A"))
        self.page_table.blockSignals(False)

    def update_pagination(self, current: int, total: int) -> None:
        self.page_page_label.setText(f"Trang {current} / {total}")
        self.page_prev_btn.setEnabled(current > 1)
        self.page_next_btn.setEnabled(current < total)

    def update_selection_summary(self, selected: int, total: int) -> None:
        self.page_selection_summary_label.setText(f"Đã chọn {selected}/{total} page")

    def update_page_info(self, page_id: str, info: Dict[str, Any]) -> None:
        for row in range(self.page_table.rowCount()):
            item = self.page_table.item(row, PAGE_COL_ID)
            if item and item.text() == page_id:
                display = f"{info.get('followers', 'N/A')} / {info.get('views', 'N/A')}"
                cell = self.page_table.item(row, PAGE_COL_INFO)
                if cell is None:
                    cell = QtWidgets.QTableWidgetItem(display)
                    self.page_table.setItem(row, PAGE_COL_INFO, cell)
                else:
                    cell.setText(display)
                if info.get("followers") not in (None, "N/A"):
                    cell.setForeground(QtGui.QColor("#22c55e"))
                return

    def set_page_post_status(self, page_id: str, status: str) -> None:
        self._presenter.page_list.page_post_statuses[page_id] = status

    # ── IConfigView interface implementation ──────────────────────

    def clear_config_table(self) -> None:
        self.config_table.setRowCount(0)

    def find_config_row(self, page_name: str) -> int:
        target = (page_name or "").strip()
        if not target:
            return -1
        for row in range(self.config_table.rowCount()):
            item = self.config_table.item(row, 0)
            if item is not None and item.text().strip() == target:
                return row
        return -1

    def update_config_row(self, row_idx: int, row_data: Dict[str, Any]) -> None:
        """Cập nhật dòng đã có: chỉ đổi video/tiêu đề/loại/trạng thái, giữ nội dung người dùng đã sửa."""
        if row_idx < 0 or row_idx >= self.config_table.rowCount():
            return
        self._set_table_value(row_idx, 0, row_data.get("page_name", row_data.get("page", "")))
        title = row_data.get("title", "")
        if title:
            self._set_table_value(row_idx, 1, title)
        video_path = row_data.get("video_path", "")
        if video_path:
            self._set_table_value(row_idx, 3, video_path)
        post_type = row_data.get("post_type", "")
        if post_type:
            type_item = self.config_table.item(row_idx, CONFIG_POST_TYPE_COL)
            if type_item is None:
                type_item = QtWidgets.QTableWidgetItem("")
                self.config_table.setItem(row_idx, CONFIG_POST_TYPE_COL, type_item)
            type_item.setText("Feed" if post_type == "feed" else "Reel / video")
            type_item.setTextAlignment(QtCore.Qt.AlignCenter)
        self._set_table_value(row_idx, CONFIG_STATUS_COL, row_data.get("status", "Chờ đăng"))
        link = row_data.get("link", "")
        if link:
            self._set_table_value(row_idx, CONFIG_LINK_COL, link)

    def add_config_row(self, row_data: Dict[str, Any]) -> None:
        row_idx = self.config_table.rowCount()
        self.config_table.insertRow(row_idx)
        page_name = str(row_data.get("page_name") or row_data.get("page") or "")
        self._set_table_value(row_idx, 0, page_name)
        page_item = self.config_table.item(row_idx, 0)
        if page_item is not None:
            page_item.setData(QtCore.Qt.UserRole, str(row_data.get("page_id") or ""))
            page_item.setToolTip(page_name)
        self._set_table_value(row_idx, 1, row_data.get("title", ""))
        self._set_table_value(row_idx, 2, row_data.get("description", ""))
        self._set_table_value(row_idx, 3, row_data.get("video_path", ""))
        self._set_table_value(row_idx, 4, row_data.get("comment", ""))
        self._set_table_value(row_idx, CONFIG_COMMENT_IMAGE_COL, row_data.get("comment_images", ""))
        post_type = row_data.get("post_type", "video")
        type_item = self.config_table.item(row_idx, CONFIG_POST_TYPE_COL)
        if type_item is None:
            type_item = QtWidgets.QTableWidgetItem("")
            self.config_table.setItem(row_idx, CONFIG_POST_TYPE_COL, type_item)
        type_item.setText("Feed" if post_type == "feed" else "Reel / video")
        type_item.setTextAlignment(QtCore.Qt.AlignCenter)
        self._set_table_value(row_idx, CONFIG_SCHEDULE_COL, row_data.get("schedule_time", ""))
        initial_status = row_data.get("status", "Chờ đăng")
        self._set_table_value(row_idx, CONFIG_STATUS_COL, initial_status)
        self._apply_config_row_color(row_idx, initial_status)
        self._set_table_value(row_idx, CONFIG_LINK_COL, row_data.get("link", ""))
        self.config_table.setRowHeight(row_idx, 26)

    def _apply_config_row_color(self, row_idx: int, status: str) -> None:
        style = CONFIG_STATUS_STYLES.get(status)
        if not style:
            return
        foreground, background = style
        for col in (0, CONFIG_STATUS_COL):
            item = self.config_table.item(row_idx, col)
            if item is not None:
                item.setForeground(QtGui.QColor(foreground))
                item.setBackground(QtGui.QColor(background))

    def _apply_config_status(self, page_name: str, status: str) -> None:
        """Cập nhật trạng thái + màu ở luồng chính (được gọi qua signal)."""
        for row in range(self.config_table.rowCount()):
            item = self.config_table.item(row, 0)
            if item and item.text().strip() == page_name:
                self._set_table_value(row, CONFIG_STATUS_COL, status)
                self._apply_config_row_color(row, status)
                return

    def update_config_status(self, page_name: str, status: str) -> None:
        # Phát signal để tô màu ở luồng chính (an toàn khi đăng đồng thời).
        self.config_status_changed.emit(page_name, status)

    def update_config_link(self, page_name: str, link: str) -> None:
        """Được gọi từ luồng đăng -> phát signal để cập nhật ở luồng chính."""
        self.config_link_changed.emit(str(page_name or ""), str(link or ""))

    def _apply_config_link(self, page_name: str, link: str) -> None:
        if not link:
            return
        for row in range(self.config_table.rowCount()):
            item = self.config_table.item(row, CONFIG_PAGE_COL)
            if item is not None and item.text().strip() == page_name:
                self._set_table_value(row, CONFIG_LINK_COL, link)
                link_item = self.config_table.item(row, CONFIG_LINK_COL)
                link_item.setToolTip(link)
                link_item.setForeground(QtGui.QColor("#1d4ed8"))
                link_item.setTextAlignment(QtCore.Qt.AlignCenter)
                return

    def get_config_rows(self) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for row_idx in range(self.config_table.rowCount()):
            values = []
            for col_idx in range(len(CONFIG_HEADERS)):
                item = self.config_table.item(row_idx, col_idx)
                values.append(item.text().strip() if item is not None else "")
            if not any(values):
                continue
            page_item = self.config_table.item(row_idx, CONFIG_PAGE_COL)
            rows.append({
                "page": values[0],
                # page_id lưu ở UserRole để khớp chính xác, tránh nhầm khi
                # 2 tài khoản cùng quản lý 1 tên page.
                "page_id": str(page_item.data(QtCore.Qt.UserRole) or "") if page_item is not None else "",
                "title": values[1], "description": values[2],
                "video_path": values[3], "comment": values[4],
                "comment_images": values[CONFIG_COMMENT_IMAGE_COL],
                "post_type": values[CONFIG_POST_TYPE_COL],
                "schedule_time": values[CONFIG_SCHEDULE_COL],
                "status": values[CONFIG_STATUS_COL], "link": values[CONFIG_LINK_COL],
            })
        return rows

    def set_post_config_button_enabled(self, enabled: bool) -> None:
        self.post_config_btn.setEnabled(enabled)

    def set_post_config_button_text(self, text: str) -> None:
        self.post_config_btn.setText(text)

    def _set_table_value(self, row_idx: int, col_idx: int, value: str) -> None:
        item = self.config_table.item(row_idx, col_idx)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.config_table.setItem(row_idx, col_idx, item)
        item.setText(str(value))

    # ── ISettingsView interface implementation ────────────────────

    def get_proxy_config(self) -> Dict[str, Any]:
        return self.settings_tab.get_proxy_config()

    def get_concurrency_config(self) -> Dict[str, Any]:
        return self.settings_tab.get_concurrency_config()

    def load_proxy_config(self, data: Dict[str, Any]) -> None:
        self.settings_tab.load_proxy_config(data)

    def load_concurrency_config(self, data: Dict[str, Any]) -> None:
        self.settings_tab.load_concurrency_config(data)
