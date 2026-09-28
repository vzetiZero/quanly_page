import csv
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod

from license_manager import TrialLicenseManager
from facebook_reels_uploader import FacebookReelsUploader
import requests
from PyQt5 import QtCore, QtGui, QtWidgets
from dotenv import load_dotenv


CONFIG_HEADERS = ["Page", "Tiêu đề", "Mô tả", "Đường dẫn video", "Comment", "Đường dẫn ảnh comment", "Loại đăng", "Thời gian đăng", "Trạng thái", "Link"]
CONFIG_STATUS_COL = 8
CONFIG_LINK_COL = 9
CONFIG_COMMENT_IMAGE_COL = 5
CONFIG_POST_TYPE_COL = 6
CONFIG_SCHEDULE_COL = 7
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".image"}


def _chunked_batches(items: List[Any], size: int) -> List[List[Any]]:
    if size <= 1:
        return [list(items)]
    return [list(items[index:index + size]) for index in range(0, len(items), size)]


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", _runtime_dir())).resolve()


def _ensure_runtime_files(runtime_dir: Path) -> None:
    source_dir = _bundled_dir()
    for relative_path in (
        "facebook_config.json",
        "resources/logo.svg",
        "resources/icon.png",
        "resources/icon.ico",
    ):
        source = source_dir / relative_path
        target = runtime_dir / relative_path
        if target.exists() or not source.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except Exception:
            pass


load_dotenv(_runtime_dir() / ".env")


def _setup_ui_logger() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(_runtime_dir() / "facebook_scraper.log", encoding="utf-8", mode="a"),
            logging.StreamHandler(),
        ],
    )

_setup_ui_logger()
logger = logging.getLogger("facebook_ui")


def _fetch_paginated_graph_data(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    next_url = url
    next_params: Optional[Dict[str, Any]] = dict(params or {})
    if "limit" not in next_params:
        next_params["limit"] = 100

    while next_url:
        try:
            response = session.get(next_url, params=next_params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            results.extend(payload.get("data", []))
            paging = payload.get("paging", {})
            next_url = paging.get("next") or ""
            if not next_url:
                after = (paging.get("cursors") or {}).get("after")
                if after and url:
                    next_url = url
                    next_params = dict(params or {})
                    next_params["limit"] = next_params.get("limit", 100)
                    next_params["after"] = after
                else:
                    next_params = None
            else:
                next_params = None
        except requests.RequestException as exc:
            raise RuntimeError(f"Facebook Graph API request failed: {exc}") from exc

    return results


class ProxyProvider(ABC):
    def __init__(self, api_key: str, *, region: str = "random") -> None:
        self.api_key = api_key
        self.region = region

    @abstractmethod
    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        raise NotImplementedError


class KiotProxyProvider(ProxyProvider):
    def __init__(self, api_key: str, *, region: str = "random") -> None:
        super().__init__(api_key, region=region)

    def _request(self, endpoint: str, *, method: str = "GET", params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        try:
            url = f"https://api.kiotproxy.com/api/v1/proxies/{endpoint}"
            if method.upper() == "POST":
                response = requests.post(url, params=params or {}, timeout=30)
            else:
                response = requests.get(url, params=params or {}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, dict):
                    return data
                if isinstance(payload.get("proxy"), dict):
                    return payload["proxy"]
                if isinstance(payload.get("result"), dict):
                    return payload["result"]
            return None
        except Exception as exc:
            logger.warning("Lỗi KiotProxy endpoint %s: %s", endpoint, exc)
            return None

    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        params = {"key": self.api_key, "region": self.region}
        current_proxy = self._request("current", params=params)
        if isinstance(current_proxy, dict) and current_proxy.get("http"):
            return current_proxy
        new_proxy = self._request("new", params=params)
        if isinstance(new_proxy, dict) and new_proxy.get("http"):
            return new_proxy
        return None

    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        if not proxy_data:
            return
        params = {"key": self.api_key}
        proxy_id = proxy_data.get("id") or proxy_data.get("proxy_id") or proxy_data.get("uuid")
        if proxy_id:
            params["id"] = proxy_id
        self._request("out", method="POST", params=params)


class GenericProxyProvider(ProxyProvider):
    def __init__(self, api_key: str, *, region: str = "random", endpoint_template: str = "") -> None:
        super().__init__(api_key, region=region)
        self.endpoint_template = endpoint_template.strip()

    def _build_url(self) -> str:
        if self.endpoint_template:
            return self.endpoint_template.format(key=self.api_key, region=self.region)
        return ""

    def _parse_proxy_payload(self, payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("proxy"), dict):
                return payload["proxy"]
            if isinstance(payload.get("data"), dict):
                return payload["data"]
            if isinstance(payload.get("result"), dict):
                return payload["result"]
            if payload.get("http") or payload.get("https"):
                return payload
        if isinstance(payload, str):
            return {"http": payload, "https": payload}
        return None

    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        url = self._build_url()
        if not url:
            return None
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
            parsed = self._parse_proxy_payload(payload)
            if isinstance(parsed, dict) and parsed.get("http"):
                return parsed
            if isinstance(parsed, dict) and parsed.get("proxy"):
                return {"http": parsed["proxy"], "https": parsed["proxy"]}
        except Exception as exc:
            logger.warning("Lỗi GenericProxyProvider: %s", exc)
        return None

    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        return None


def _qt_message_handler(mode, context, message):
    if "Cannot queue arguments of type 'QVector<int>'" in message:
        return
    if hasattr(QtCore, "qInstallMessageHandler"):
        pass


if hasattr(QtCore, "qInstallMessageHandler"):
    QtCore.qInstallMessageHandler(_qt_message_handler)


class TrialLicenseDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kích hoạt bản dùng thử")
        self.resize(420, 220)
        self.setModal(True)
        self.code = ""
        self.setStyleSheet(
            """
            * { font-family: "Nunito"; font-size: 9pt; }
            QDialog { background: #f8fafc; color: #0f172a; }
            QLabel { color: #334155; font-weight: 700; }
            QLineEdit { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 8px; padding: 8px; font-weight: 650; }
            QPushButton { background: #0284c7; color: #ffffff; border: 1px solid #0284c7; border-radius: 8px; padding: 8px 12px; font-weight: 800; }
            QPushButton:hover { background: #0ea5e9; }
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QtWidgets.QLabel("Nhập mã kích hoạt để sử dụng ứng dụng trong thời gian dùng thử."))
        layout.addWidget(QtWidgets.QLabel(""))

        self.code_input = QtWidgets.QLineEdit()
        self.code_input.setPlaceholderText("Nhập mã:")
        layout.addWidget(self.code_input)

        self.error_label = QtWidgets.QLabel("")
        self.error_label.setStyleSheet("color: #f87171;")
        layout.addWidget(self.error_label)

        buttons = QtWidgets.QHBoxLayout()
        self.ok_btn = QtWidgets.QPushButton("Xác nhận")
        self.ok_btn.clicked.connect(self._validate)
        buttons.addWidget(self.ok_btn)
        self.cancel_btn = QtWidgets.QPushButton("Đóng")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    def _validate(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            self.error_label.setText("Vui lòng nhập mã")
            return
        if re.fullmatch(r"\s*(\d+)\s*([dD])\s*", code) or code.strip().lower() == "vietboss1998":
            self.code = code
            self.accept()
        else:
            self.error_label.setText("Mã không hợp lệ.")


class SelectablePageTable(QtWidgets.QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_selecting = False
        self._drag_last_row: Optional[int] = None
        self._drag_base_state: Optional[bool] = None
        self._drag_moved = False

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            row = self.rowAt(event.pos().y())
            col = self.columnAt(event.pos().x())
            if row >= 0 and col == 2:
                self._drag_selecting = True
                self._drag_last_row = row
                self._drag_base_state = self._is_row_selected(row)
                self._drag_moved = False
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_selecting:
            row = self.rowAt(event.pos().y())
            col = self.columnAt(event.pos().x())
            if row >= 0 and col == 2 and row != self._drag_last_row:
                self._apply_toggle_drag_selection(row)
                self._drag_moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._drag_selecting:
            if not self._drag_moved and self._drag_last_row is not None:
                self._toggle_row_selection(self._drag_last_row)
            self._drag_selecting = False
            self._drag_last_row = None
            self._drag_base_state = None
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _is_row_selected(self, row: int) -> bool:
        item = self.item(row, 2)
        return item is not None and item.checkState() == QtCore.Qt.Checked

    def _set_row_selection(self, row: int, checked: bool) -> None:
        if row < 0 or row >= self.rowCount():
            return
        item = self.item(row, 2)
        if item is None:
            return
        self.blockSignals(True)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.blockSignals(False)
        page_id_item = self.item(row, 6)
        if page_id_item:
            table_window = getattr(self, "window_ref", None)
            if table_window is not None and hasattr(table_window, "page_selection_states"):
                table_window.page_selection_states[page_id_item.text()] = checked
            if table_window is not None and hasattr(table_window, "_update_page_selection_summary"):
                table_window._update_page_selection_summary()

    def _toggle_row_selection(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        self._set_row_selection(row, not self._is_row_selected(row))

    def _on_page_table_cell_clicked(self, row: int, col: int) -> None:
        if col != 2:
            return
        self._toggle_row_selection(row)

    def _apply_toggle_drag_selection(self, row: int) -> None:
        if self._drag_last_row is None or self._drag_base_state is None:
            return
        start_row = min(self._drag_last_row, row)
        end_row = max(self._drag_last_row, row)
        for target_row in range(start_row, end_row + 1):
            self._set_row_selection(target_row, not self._drag_base_state)
        self._drag_last_row = row


class FacebookPageManagerWindow(QtWidgets.QMainWindow):
    status_changed = QtCore.pyqtSignal(str, str, str)
    posting_completed = QtCore.pyqtSignal(int, int)
    pages_loaded = QtCore.pyqtSignal(object)
    page_info_updated = QtCore.pyqtSignal(str, dict)
    config_row_append = QtCore.pyqtSignal(dict)
    config_populate_done = QtCore.pyqtSignal()
    error_occurred = QtCore.pyqtSignal(str)
    page_status_requested = QtCore.pyqtSignal()
    config_status_changed = QtCore.pyqtSignal(str, str)
    config_link_changed = QtCore.pyqtSignal(int, str)
    recent_posts_refresh_requested = QtCore.pyqtSignal()
    accounts_refresh_requested = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quản lý Page Facebook đa tài khoản")
        self.resize(1400, 950)

        self.api_version = os.getenv("FB_API_VERSION", "v25.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self.project_dir = _runtime_dir()
        _ensure_runtime_files(self.project_dir)
        self.db_path = self.project_dir / "facebook_cache.sqlite3"
        self.config_path = self.project_dir / "facebook_config.json"
        self.pages: List[Dict[str, Any]] = []
        self.account_pages: Dict[str, List[Dict[str, Any]]] = {}
        self.token_statuses: Dict[str, Dict[str, Any]] = {}
        self.page_selection_states: Dict[str, bool] = {}
        self.page_post_statuses: Dict[str, str] = {}
        self.current_page = 1
        self.page_size = 20
        self.stop_requested = False
        self.concurrency_enabled = False
        self.concurrency_threads = 2
        self.concurrency_delay = 2.0
        self.post_retry_count = 2
        self.post_retry_delay_base = 10
        self.proxy_api_key = os.getenv("KIOTPROXY_API_KEY", "")
        self.proxy_region = os.getenv("KIOTPROXY_REGION", "random")
        self.proxy_provider_name = os.getenv("PROXY_PROVIDER", "kiotproxy")
        self.proxy_endpoint_template = os.getenv("PROXY_ENDPOINT_TEMPLATE", "")
        self.proxy_enabled = bool(self.proxy_api_key)
        self.proxy_provider: Optional[ProxyProvider] = None
        self.post_thread: Optional[threading.Thread] = None
        self._rotation_lock = threading.Lock()
        self._bypass_schedule_wait_once = False
        self.config_repeat_timer = QtCore.QTimer(self)
        self.config_repeat_timer.setSingleShot(True)
        self.config_repeat_timer.timeout.connect(self._run_scheduled_config_post)
        self._periodic_check_running = False
        self.license_manager = TrialLicenseManager(self.project_dir)
        self.license_manager.hide_state_file()
        self.license_locked = False
        self.logger = logger
        self.reels_uploader = FacebookReelsUploader(self.base_url, logger=self.logger, append_log=self._append_log)

        self.status_changed.connect(self._update_status_row)
        self.posting_completed.connect(self._finish_posting)
        self.pages_loaded.connect(self._populate_page_table)
        self.page_info_updated.connect(self._on_page_info_updated)
        self.config_row_append.connect(self._on_config_row_append)
        self.config_populate_done.connect(self._on_config_populate_done)
        self.error_occurred.connect(self._show_error)
        self.page_status_requested.connect(self._update_page_statuses)
        self.config_status_changed.connect(self._update_config_status_row)
        self.config_link_changed.connect(self._update_config_link_row)
        self.recent_posts_refresh_requested.connect(self._refresh_recent_posts)
        self.accounts_refresh_requested.connect(self._refresh_accounts_view)
        self._init_database()
        self._build_ui()
        self._set_main_controls_enabled(False)
        self._load_saved_config()
        self._load_pages_from_database()
        self._refresh_recent_posts()
        self.tabs.currentChanged.connect(self._on_config_tab_changed)
        QtCore.QTimer.singleShot(300, self._ensure_trial_access)

        self.trial_timer = QtCore.QTimer(self)
        self.trial_timer.timeout.connect(self._update_trial_status)
        self.trial_timer.start(1000)

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self._periodic_token_check)
        self.refresh_timer.start(30000)

    def _init_database(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    token TEXT PRIMARY KEY,
                    token_prefix TEXT,
                    last_checked TEXT,
                    is_valid INTEGER,
                    expires_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pages (
                    token TEXT,
                    page_id TEXT,
                    page_name TEXT,
                    page_access_token TEXT,
                    account_label TEXT,
                    page_type TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (token, page_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    token TEXT PRIMARY KEY,
                    token_prefix TEXT,
                    account_id TEXT,
                    account_name TEXT,
                    account_label TEXT,
                    last_checked TEXT,
                    is_valid INTEGER,
                    expires_at TEXT,
                    page_count INTEGER,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_media_rotation (
                    page_key TEXT PRIMARY KEY,
                    next_video_index INTEGER,
                    last_video_path TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_name TEXT,
                    content TEXT,
                    post_type TEXT,
                    posted_at TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_video_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_key TEXT NOT NULL,
                    page_name TEXT,
                    video_path TEXT NOT NULL,
                    video_name TEXT,
                    posted_at TEXT,
                    post_id TEXT,
                    permalink_url TEXT,
                    status TEXT,
                    UNIQUE(page_key, video_path, status)
                )
                """
            )
            conn.commit()

    def _apply_small_button_style(self, button: QtWidgets.QPushButton) -> None:
        button.setStyleSheet(
            """
            QPushButton {
                background: #0284c7;
                color: #ffffff;
                border: 1px solid #0284c7;
                border-radius: 8px;
                padding: 6px 10px;
                min-height: 28px;
                min-width: 80px;
                font-weight: 800;
            }
            QPushButton:hover { background: #0ea5e9; border-color: #0ea5e9; }
            QPushButton:pressed { background: #0369a1; }
            QPushButton:disabled { background: #e2e8f0; border-color: #cbd5e1; color: #64748b; }
            """
        )

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self.setStyleSheet(
            """
            * { font-family: "Nunito"; font-size: 9pt; }
            QMainWindow { background: #f8fafc; color: #0f172a; }
            QWidget { color: #0f172a; }
            QGroupBox {
                background: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 12px;
                font-weight: 800;
            }
            QLabel { color: #334155; font-weight: 650; }
            QLineEdit, QPlainTextEdit, QTextEdit, QDateTimeEdit, QTimeEdit, QDoubleSpinBox, QSpinBox, QComboBox {
                background: #ffffff;
                color: #0f172a;
                selection-background-color: #38bdf8;
                selection-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 8px;
                font-weight: 650;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QComboBox:focus {
                border: 1px solid #0284c7;
            }
            QPushButton {
                background: #0284c7;
                color: #ffffff;
                border: 1px solid #0284c7;
                border-radius: 8px;
                padding: 10px 14px;
                min-height: 34px;
                min-width: 110px;
                font-weight: 800;
            }
            QPushButton:hover { background: #0ea5e9; border-color: #0ea5e9; }
            QPushButton:pressed { background: #0369a1; }
            QPushButton:disabled { background: #e2e8f0; border-color: #cbd5e1; color: #64748b; }
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                color: #0f172a;
                gridline-color: #e2e8f0;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            QTableWidget::item { color: #0f172a; padding: 4px; }
            QTableWidget::item:selected { background: #dbeafe; color: #0f172a; }
            QHeaderView::section { background: #e2e8f0; color: #0f172a; padding: 7px; border: 1px solid #cbd5e1; font-weight: 800; }
            QHeaderView::section:vertical { background: #f8fafc; color: #334155; padding: 6px; border: 1px solid #e2e8f0; }
            QTableCornerButton::section { background: #e2e8f0; border: 1px solid #cbd5e1; }
            QTableWidget QHeaderView::vertical { background: transparent; }
            QCheckBox { color: #334155; font-weight: 700; spacing: 8px; }
            QTabWidget::pane { border: 1px solid #cbd5e1; background: #f8fafc; border-radius: 10px; }
            QTabBar::tab {
                background: #f1f5f9;
                color: #334155;
                padding: 10px 16px;
                min-height: 34px;
                min-width: 120px;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                margin-right: 6px;
                font-weight: 800;
                text-align: center;
            }
            QTabBar::tab:hover { background: #e2e8f0; }
            QTabBar::tab:selected { background: #0284c7; color: #ffffff; border-color: #0284c7; }
            QStatusBar { background: #f8fafc; color: #0f172a; }
            QToolTip { background: #ffffff; color: #0f172a; border: 1px solid #0284c7; padding: 6px; }
            """
        )



        self.tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self.tabs)

        post_tab = QtWidgets.QWidget(self)
        post_layout = QtWidgets.QVBoxLayout(post_tab)
        post_layout.setContentsMargins(0, 0, 0, 0)
        post_layout.setSpacing(3)
        self.tabs.addTab(post_tab, "Đăng bài")

        post_layout.addSpacing(50)
        token_controls = QtWidgets.QHBoxLayout()
        token_controls.setContentsMargins(0, 0, 0, 0)
        token_controls.setSpacing(8)
        self.token_input = QtWidgets.QPlainTextEdit()
        self.token_input.setPlaceholderText("Nhập access_token hoặc dán từ file txt (mỗi token 1 dòng)")
        self.token_input.setMaximumHeight(50)
        token_controls.addWidget(self.token_input)
        self.load_tokens_btn = QtWidgets.QPushButton("Nhập file txt")
        self.load_tokens_btn.clicked.connect(self.load_tokens_from_file)
        token_controls.addWidget(self.load_tokens_btn)
        self.load_pages_btn = QtWidgets.QPushButton("Lấy danh sách page")
        self.load_pages_btn.clicked.connect(self.load_all_pages)
        token_controls.addWidget(self.load_pages_btn)
        post_layout.addLayout(token_controls)

        middle = QtWidgets.QGroupBox("Danh sách page")
        middle_layout = QtWidgets.QVBoxLayout(middle)
        search_layout = QtWidgets.QHBoxLayout()
        self.page_search_input = QtWidgets.QLineEdit()
        self.page_search_input.setPlaceholderText("Tìm page theo tên hoặc ID")
        self.page_search_input.textChanged.connect(self._filter_page_table)
        search_layout.addWidget(self.page_search_input)
        self.clear_search_btn = QtWidgets.QPushButton("Xóa")
        self.clear_search_btn.clicked.connect(lambda: (self.page_search_input.clear(), self._filter_page_table("")))
        self._apply_small_button_style(self.clear_search_btn)
        search_layout.addWidget(self.clear_search_btn)
        middle_layout.addLayout(search_layout)

        self.page_table = SelectablePageTable(self)
        self.page_table.window_ref = self
        self.page_table.setColumnCount(10)
        self.page_table.setHorizontalHeaderLabels(["STT", "Tên Page", "Chọn", "Trạng thái token", "Trạng thái đăng", "Tài khoản", "Page ID", "Open trang", "Page Access Token", "Follow/View"])
        self.page_table.horizontalHeader().setStretchLastSection(True)
        self.page_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.page_table.setAlternatingRowColors(True)
        self.page_table.itemChanged.connect(self._on_page_table_item_changed)
        self.page_table.itemClicked.connect(self._on_page_table_item_clicked)
        self.page_table.cellClicked.connect(self.page_table._on_page_table_cell_clicked)
        # Improve vertical header (row numbers) appearance and spacing
        try:
            self.page_table.verticalHeader().setDefaultAlignment(QtCore.Qt.AlignCenter)
            self.page_table.verticalHeader().setDefaultSectionSize(34)
            self.page_table.verticalHeader().setStyleSheet("color: #334155; background: transparent; border: none; font-family: Nunito; font-weight: 800;")
        except Exception:
            pass
        self.page_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        middle_layout.addWidget(self.page_table)

        pagination_layout = QtWidgets.QHBoxLayout()
        self.page_prev_btn = QtWidgets.QPushButton("‹ Trước")
        self.page_prev_btn.clicked.connect(self.page_prev)
        self._apply_small_button_style(self.page_prev_btn)
        self.page_next_btn = QtWidgets.QPushButton("Sau ›")
        self.page_next_btn.clicked.connect(self.page_next)
        self._apply_small_button_style(self.page_next_btn)
        self.page_size_combo = QtWidgets.QComboBox()
        self.page_size_combo.addItems(["10", "20", "50"])
        self.page_size_combo.setCurrentText(str(self.page_size))
        self.page_size_combo.currentTextChanged.connect(self._on_page_size_changed)
        self.page_page_label = QtWidgets.QLabel("Trang 1 / 1")
        self.page_selection_summary_label = QtWidgets.QLabel("Đã chọn 0/0 page")
        self.page_selection_summary_label.setStyleSheet("color: #0284c7; font-weight: 800;")
        pagination_layout.addWidget(self.page_prev_btn)
        pagination_layout.addWidget(self.page_next_btn)
        pagination_layout.addStretch()
        pagination_layout.addWidget(self.page_selection_summary_label)
        pagination_layout.addWidget(QtWidgets.QLabel("Hiển thị"))
        pagination_layout.addWidget(self.page_size_combo)
        pagination_layout.addWidget(self.page_page_label)
        middle_layout.addLayout(pagination_layout)

        table_controls = QtWidgets.QHBoxLayout()
        self.select_all_btn = QtWidgets.QPushButton("Chọn tất cả")
        self.select_all_btn.clicked.connect(self.select_all_pages)
        self._apply_small_button_style(self.select_all_btn)
        table_controls.addWidget(self.select_all_btn)
        self.clear_btn = QtWidgets.QPushButton("Bỏ chọn")
        self.clear_btn.clicked.connect(self.clear_selection)
        self._apply_small_button_style(self.clear_btn)
        table_controls.addWidget(self.clear_btn)
        self.post_selected_btn = QtWidgets.QPushButton("Đăng bài đã chọn")
        self.post_selected_btn.clicked.connect(self.post_selected_pages)
        self._apply_small_button_style(self.post_selected_btn)
        table_controls.addWidget(self.post_selected_btn)
        self.post_all_btn = QtWidgets.QPushButton("Đăng tất cả")
        self.post_all_btn.clicked.connect(self.post_all_pages)
        self._apply_small_button_style(self.post_all_btn)
        table_controls.addWidget(self.post_all_btn)
        self.stop_btn = QtWidgets.QPushButton("Dừng")
        self.stop_btn.clicked.connect(self.stop_posting)
        self.stop_btn.setEnabled(False)
        self._apply_small_button_style(self.stop_btn)
        table_controls.addWidget(self.stop_btn)
        middle_layout.addLayout(table_controls)

        bottom_widget = QtWidgets.QWidget(self)
        bottom_layout = QtWidgets.QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(14)
        self.message_input = QtWidgets.QPlainTextEdit()
        self.message_input.setPlaceholderText("Nội dung bài viết")
        self.message_input.setMaximumHeight(140)
        self.post_type_combo = QtWidgets.QComboBox()
        self.post_type_combo.addItem("Reel / video", "video")
        self.post_type_combo.addItem("Bài đăng thường", "feed")
        self.post_type_combo.setToolTip("Chọn cách Facebook sẽ đăng nội dung: Reel/video hoặc bài đăng thường")
        self.image_paths_input = QtWidgets.QLineEdit()
        self.image_paths_input.setPlaceholderText("Đường dẫn ảnh, cách nhau bằng ;")
        self.video_paths_input = QtWidgets.QLineEdit()
        self.video_paths_input.setPlaceholderText("Đường dẫn video, cách nhau bằng ;")

        self.status_label = QtWidgets.QLabel("Sẵn sàng")
        self.status_label.setWordWrap(True)
        bottom_layout.addWidget(self.status_label)

        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        content_splitter.addWidget(middle)
        content_splitter.addWidget(bottom_widget)
        content_splitter.setStretchFactor(0, 2)
        content_splitter.setStretchFactor(1, 1)
        post_layout.addWidget(content_splitter)

        self.trial_status_label = QtWidgets.QLabel("Trial: đang kiểm tra...")
        self.trial_status_label.setStyleSheet("background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 6px 10px; color: #334155; font-weight: 800;")
        self.statusBar().addPermanentWidget(self.trial_status_label)

        self.config_tab = QtWidgets.QWidget(self)
        config_layout = QtWidgets.QVBoxLayout(self.config_tab)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(14)
        self.tabs.addTab(self.config_tab, "Cấu hình")

        self.quick_config_tab = QtWidgets.QWidget(self)
        quick_config_layout = QtWidgets.QVBoxLayout(self.quick_config_tab)
        quick_config_layout.setContentsMargins(0, 0, 0, 0)
        quick_config_layout.setSpacing(14)
        quick_config_group = QtWidgets.QGroupBox("Cấu hình nhanh cho toàn bộ page")
        quick_config_group.setToolTip("Nhập một nội dung chung cho bài đăng và comment, rồi áp dụng cho toàn bộ page đã chọn")
        quick_config_group_layout = QtWidgets.QVBoxLayout(quick_config_group)
        quick_config_desc = QtWidgets.QLabel("Dùng khi nội dung đăng và nội dung comment giống nhau cho toàn bộ page. Mỗi page sẽ nhận cùng một nội dung chung.")
        quick_config_desc.setWordWrap(True)
        quick_config_desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        quick_config_group_layout.addWidget(quick_config_desc)

        self.quick_post_content_input = QtWidgets.QPlainTextEdit()
        self.quick_post_content_input.setPlaceholderText("Nội dung bài đăng chung")
        self.quick_post_content_input.setMaximumHeight(140)
        quick_config_group_layout.addWidget(QtWidgets.QLabel("Nội dung bài đăng chung:"))
        quick_config_group_layout.addWidget(self.quick_post_content_input)

        self.quick_comment_content_input = QtWidgets.QPlainTextEdit()
        self.quick_comment_content_input.setPlaceholderText("Nội dung comment chung")
        self.quick_comment_content_input.setMaximumHeight(140)
        quick_config_group_layout.addWidget(QtWidgets.QLabel("Nội dung comment chung:"))
        quick_config_group_layout.addWidget(self.quick_comment_content_input)

        schedule_group = QtWidgets.QGroupBox("Lịch đăng và lặp lại")
        schedule_group_layout = QtWidgets.QVBoxLayout(schedule_group)
        self.schedule_checkbox = QtWidgets.QCheckBox("Bật lịch đăng")
        self.schedule_checkbox.toggled.connect(self._on_schedule_settings_changed)
        schedule_group_layout.addWidget(self.schedule_checkbox)

        schedule_mode_row = QtWidgets.QHBoxLayout()
        schedule_mode_row.addWidget(QtWidgets.QLabel("Chế độ:"))
        self.schedule_mode_combo = QtWidgets.QComboBox()
        self.schedule_mode_combo.addItem("Một lần", "once")
        self.schedule_mode_combo.addItem("Lặp sau X giờ", "interval")
        self.schedule_mode_combo.addItem("Mỗi ngày vào giờ", "daily")
        self.schedule_mode_combo.addItem("Theo thứ", "weekly")
        self.schedule_mode_combo.currentIndexChanged.connect(self._on_schedule_settings_changed)
        schedule_mode_row.addWidget(self.schedule_mode_combo)
        schedule_mode_row.addStretch(1)
        schedule_group_layout.addLayout(schedule_mode_row)

        self.schedule_once_row = QtWidgets.QWidget()
        once_row_layout = QtWidgets.QHBoxLayout(self.schedule_once_row)
        once_row_layout.setContentsMargins(0, 0, 0, 0)
        once_row_layout.addWidget(QtWidgets.QLabel("Ngày giờ:"))
        self.schedule_datetime = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.schedule_datetime.setCalendarPopup(True)
        self.schedule_datetime.dateTimeChanged.connect(self._on_schedule_settings_changed)
        once_row_layout.addWidget(self.schedule_datetime)
        once_row_layout.addStretch(1)
        schedule_group_layout.addWidget(self.schedule_once_row)

        self.schedule_interval_row = QtWidgets.QWidget()
        interval_row_layout = QtWidgets.QHBoxLayout(self.schedule_interval_row)
        interval_row_layout.setContentsMargins(0, 0, 0, 0)
        interval_row_layout.addWidget(QtWidgets.QLabel("Sau X giờ:"))
        self.schedule_interval_spin = QtWidgets.QDoubleSpinBox()
        self.schedule_interval_spin.setRange(0.25, 168.0)
        self.schedule_interval_spin.setSingleStep(0.25)
        self.schedule_interval_spin.setValue(24.0)
        self.schedule_interval_spin.valueChanged.connect(self._on_schedule_settings_changed)
        interval_row_layout.addWidget(self.schedule_interval_spin)
        interval_row_layout.addStretch(1)
        schedule_group_layout.addWidget(self.schedule_interval_row)

        self.schedule_daily_row = QtWidgets.QWidget()
        daily_row_layout = QtWidgets.QHBoxLayout(self.schedule_daily_row)
        daily_row_layout.setContentsMargins(0, 0, 0, 0)
        daily_row_layout.addWidget(QtWidgets.QLabel("Giờ chạy:"))
        self.schedule_time_of_day = QtWidgets.QTimeEdit(QtCore.QTime.currentTime())
        self.schedule_time_of_day.setDisplayFormat("HH:mm")
        self.schedule_time_of_day.timeChanged.connect(self._on_schedule_settings_changed)
        daily_row_layout.addWidget(self.schedule_time_of_day)
        daily_row_layout.addStretch(1)
        schedule_group_layout.addWidget(self.schedule_daily_row)

        self.schedule_weekday_row = QtWidgets.QWidget()
        weekday_row_layout = QtWidgets.QGridLayout(self.schedule_weekday_row)
        weekday_row_layout.setContentsMargins(0, 0, 0, 0)
        weekday_row_layout.setSpacing(6)
        weekday_row_layout.addWidget(QtWidgets.QLabel("Chọn thứ:"), 0, 0)
        self.schedule_weekday_checks: List[QtWidgets.QCheckBox] = []
        weekday_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"]
        for idx, name in enumerate(weekday_names):
            chk = QtWidgets.QCheckBox(name)
            chk.toggled.connect(self._on_schedule_settings_changed)
            self.schedule_weekday_checks.append(chk)
            weekday_row_layout.addWidget(chk, 0, idx + 1)
        schedule_group_layout.addWidget(self.schedule_weekday_row)

        self.schedule_repeat_hint = QtWidgets.QLabel("Mô tả: một lần, lặp theo giờ, mỗi ngày vào giờ cố định, hoặc chọn ngày trong tuần.")
        self.schedule_repeat_hint.setWordWrap(True)
        self.schedule_repeat_hint.setStyleSheet("color: #0284c7; font-size: 11px; font-weight: 700;")
        schedule_group_layout.addWidget(self.schedule_repeat_hint)
        quick_config_group_layout.addWidget(schedule_group)
        self._update_schedule_controls_visibility()

        quick_actions = QtWidgets.QHBoxLayout()
        self.apply_quick_config_btn = QtWidgets.QPushButton("Áp dụng cho toàn bộ dòng")
        self.apply_quick_config_btn.clicked.connect(self._apply_quick_content_to_config)
        quick_actions.addWidget(self.apply_quick_config_btn)
        quick_actions.addStretch(1)
        quick_config_group_layout.addLayout(quick_actions)
        quick_config_layout.addWidget(quick_config_group)
        self.tabs.addTab(self.quick_config_tab, "Cấu hình nhanh")

        recent_tab = QtWidgets.QWidget(self)
        recent_layout = QtWidgets.QVBoxLayout(recent_tab)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.setSpacing(10)
        self.tabs.addTab(recent_tab, "Lịch sử đăng")
        recent_label = QtWidgets.QLabel("Danh sách các bài đăng thành công gần đây")
        recent_label.setWordWrap(True)
        recent_layout.addWidget(recent_label)

        recent_controls = QtWidgets.QHBoxLayout()
        self.clear_recent_posts_btn = QtWidgets.QPushButton("Xóa lịch sử")
        self.clear_recent_posts_btn.clicked.connect(self._clear_recent_posts_history)
        self._apply_small_button_style(self.clear_recent_posts_btn)
        recent_controls.addWidget(self.clear_recent_posts_btn)
        recent_controls.addStretch(1)
        self.recent_page_label = QtWidgets.QLabel("Trang 1")
        self.recent_page_label.setStyleSheet("color: #334155; font-weight: 800;")
        recent_controls.addWidget(self.recent_page_label)
        self.recent_prev_btn = QtWidgets.QPushButton("‹")
        self.recent_prev_btn.clicked.connect(lambda: self._load_recent_posts_page(self.recent_page - 1))
        self._apply_small_button_style(self.recent_prev_btn)
        recent_controls.addWidget(self.recent_prev_btn)
        self.recent_next_btn = QtWidgets.QPushButton("›")
        self.recent_next_btn.clicked.connect(lambda: self._load_recent_posts_page(self.recent_page + 1))
        self._apply_small_button_style(self.recent_next_btn)
        recent_controls.addWidget(self.recent_next_btn)
        recent_layout.addLayout(recent_controls)

        self.recent_posts_table = QtWidgets.QTableWidget(0, 4)
        self.recent_posts_table.setHorizontalHeaderLabels(["Page", "Nội dung", "Loại", "Thời gian"])
        self.recent_posts_table.setAlternatingRowColors(True)
        self.recent_posts_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.recent_posts_table.horizontalHeader().setStretchLastSection(True)
        self.recent_posts_table.setColumnWidth(1, 420)
        recent_layout.addWidget(self.recent_posts_table)

        self.recent_page_size = 20
        self.recent_page = 1
        self.recent_total_pages = 1

        config_group = QtWidgets.QGroupBox("Cấu hình đăng video theo từng dòng")
        config_inner_layout = QtWidgets.QVBoxLayout(config_group)
        info_label = QtWidgets.QLabel(
            "Sau khi bấm Đăng bài đã chọn hoặc Đăng tất cả, chương trình sẽ chuyển sang tab Cấu hình.\n"
            "Hãy chọn thư mục video, thư mục ảnh comment (nếu cần) và bấm nút tự động gán trước khi bấm Đăng."
        )
        info_label.setWordWrap(True)
        config_inner_layout.addWidget(info_label)
        config_controls = QtWidgets.QHBoxLayout()
        self.add_row_btn = QtWidgets.QPushButton("Thêm dòng")
        self.add_row_btn.clicked.connect(self.add_config_row)
        self._apply_small_button_style(self.add_row_btn)
        config_controls.addWidget(self.add_row_btn)
        self.feed_mode_btn = QtWidgets.QPushButton("Đăng dạng feed")
        self.feed_mode_btn.clicked.connect(lambda: self._set_post_type_for_all_rows("feed"))
        self._apply_small_button_style(self.feed_mode_btn)
        config_controls.addWidget(self.feed_mode_btn)
        self.post_config_btn = QtWidgets.QPushButton("Đăng")
        self.post_config_btn.clicked.connect(self._start_posting_from_config)
        self._apply_small_button_style(self.post_config_btn)
        config_controls.addWidget(self.post_config_btn)
        config_inner_layout.addLayout(config_controls)

        folder_controls = QtWidgets.QGridLayout()
        folder_controls.setSpacing(8)
        video_folder_label = QtWidgets.QLabel("Thư mục video:")
        video_folder_label.setStyleSheet("font-weight: 600;")
        folder_controls.addWidget(video_folder_label, 0, 0)
        self.video_folder_input = QtWidgets.QLineEdit()
        self.video_folder_input.setPlaceholderText("Chọn thư mục chứa video")
        self.video_folder_input.editingFinished.connect(self._on_folder_setting_changed)
        folder_controls.addWidget(self.video_folder_input, 0, 1)
        self.select_video_folder_btn = QtWidgets.QPushButton("Chọn")
        self.select_video_folder_btn.clicked.connect(self._select_video_folder)
        self._apply_small_button_style(self.select_video_folder_btn)
        folder_controls.addWidget(self.select_video_folder_btn, 0, 2)

        comment_folder_label = QtWidgets.QLabel("Thư mục ảnh comment:")
        comment_folder_label.setStyleSheet("font-weight: 600;")
        folder_controls.addWidget(comment_folder_label, 1, 0)
        self.comment_image_folder_input = QtWidgets.QLineEdit()
        self.comment_image_folder_input.setPlaceholderText("Chọn thư mục chứa ảnh comment")
        self.comment_image_folder_input.editingFinished.connect(self._on_folder_setting_changed)
        folder_controls.addWidget(self.comment_image_folder_input, 1, 1)
        self.select_comment_image_folder_btn = QtWidgets.QPushButton("Chọn")
        self.select_comment_image_folder_btn.clicked.connect(self._select_comment_image_folder)
        self._apply_small_button_style(self.select_comment_image_folder_btn)
        folder_controls.addWidget(self.select_comment_image_folder_btn, 1, 2)

        self.auto_map_btn = QtWidgets.QPushButton("Tự động gán theo thứ tự page")
        self.auto_map_btn.clicked.connect(self._apply_folder_mapping_to_config)
        self._apply_small_button_style(self.auto_map_btn)
        folder_controls.addWidget(self.auto_map_btn, 0, 3, 2, 1)
        config_inner_layout.addLayout(folder_controls)

        self.skip_missing_videos_checkbox = QtWidgets.QCheckBox("Bỏ qua page khi video không đủ")
        self.skip_missing_videos_checkbox.setChecked(True)
        config_inner_layout.addWidget(self.skip_missing_videos_checkbox)

        self.use_comment_images_checkbox = QtWidgets.QCheckBox("Dùng ảnh comment")
        self.use_comment_images_checkbox.setChecked(False)
        self.use_comment_images_checkbox.toggled.connect(self._apply_folder_mapping_to_config)
        config_inner_layout.addWidget(self.use_comment_images_checkbox)

        self.config_table = QtWidgets.QTableWidget(0, len(CONFIG_HEADERS))
        self.config_table.setHorizontalHeaderLabels(CONFIG_HEADERS)
        self.config_table.setAlternatingRowColors(True)
        self.config_table.setEditTriggers(QtWidgets.QAbstractItemView.AllEditTriggers)
        self.config_table.itemChanged.connect(self._on_config_table_item_changed)
        self.config_table.cellClicked.connect(self._on_config_post_type_cell_clicked)
        self.config_table.setColumnWidth(2, 220)
        self.config_table.setColumnWidth(3, 260)
        self.config_table.setColumnWidth(4, 220)
        self.config_table.setColumnWidth(5, 260)
        self.config_table.setColumnWidth(CONFIG_POST_TYPE_COL, 120)
        self.config_table.setColumnWidth(CONFIG_SCHEDULE_COL, 180)
        self.config_table.setColumnWidth(CONFIG_STATUS_COL, 110)
        self.config_table.setColumnWidth(CONFIG_LINK_COL, 260)
        self.config_table.verticalHeader().setDefaultSectionSize(42)
        self.config_table.verticalHeader().setMinimumSectionSize(42)
        self.config_table.setMinimumHeight(360)
        self.config_table.horizontalHeader().setStretchLastSection(True)
        config_inner_layout.addWidget(self.config_table)
        config_layout.addWidget(config_group)

        self.log_tab = QtWidgets.QWidget(self)
        log_layout = QtWidgets.QVBoxLayout(self.log_tab)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(10)
        log_hint = QtWidgets.QLabel("Nhật ký chi tiết cho từng lần đăng, kèm trạng thái và thông tin phản hồi từ Facebook")
        log_hint.setWordWrap(True)
        log_hint.setStyleSheet("color: #334155; font-weight: 650;")
        log_layout.addWidget(log_hint)
        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Đợi quá trình đăng để xem log...")
        self.log_output.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_output)
        self.tabs.addTab(self.log_tab, "Log")

        self.settings_tab = QtWidgets.QWidget(self)
        settings_layout = QtWidgets.QVBoxLayout(self.settings_tab)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(14)

        utility_group = QtWidgets.QGroupBox("Tiện ích")
        utility_group_layout = QtWidgets.QVBoxLayout(utility_group)
        utility_desc = QtWidgets.QLabel("Các thao tác kiểm tra, kích hoạt và dọn cache được gom vào đây để tab Đăng bài tập trung vào page và nội dung đăng.")
        utility_desc.setWordWrap(True)
        utility_desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        utility_group_layout.addWidget(utility_desc)

        utility_actions = QtWidgets.QHBoxLayout()
        self.check_tokens_btn = QtWidgets.QPushButton("Kiểm tra token ngay")
        self.check_tokens_btn.clicked.connect(self.check_tokens_now)
        self._apply_small_button_style(self.check_tokens_btn)
        utility_actions.addWidget(self.check_tokens_btn)
        self.activate_trial_btn = QtWidgets.QPushButton("Kích hoạt mã")
        self.activate_trial_btn.clicked.connect(self.open_trial_dialog)
        self._apply_small_button_style(self.activate_trial_btn)
        utility_actions.addWidget(self.activate_trial_btn)
        self.refresh_token_btn = QtWidgets.QPushButton("Refresh token")
        self.refresh_token_btn.clicked.connect(self.refresh_token)
        self._apply_small_button_style(self.refresh_token_btn)
        utility_actions.addWidget(self.refresh_token_btn)
        self.clear_cache_btn = QtWidgets.QPushButton("Xóa cache")
        self.clear_cache_btn.clicked.connect(self.clear_cache)
        self._apply_small_button_style(self.clear_cache_btn)
        utility_actions.addWidget(self.clear_cache_btn)
        self.reset_license_btn = QtWidgets.QPushButton("Xóa license & nhập lại")
        self.reset_license_btn.clicked.connect(self.reset_license_from_ui)
        self._apply_small_button_style(self.reset_license_btn)
        utility_actions.addWidget(self.reset_license_btn)
        utility_group_layout.addLayout(utility_actions)
        settings_layout.addWidget(utility_group)

        settings_group = QtWidgets.QGroupBox("Proxy và đăng đồng thời")
        settings_group.setToolTip("Quản lý proxy và cấu hình đăng đồng thời")
        settings_group_layout = QtWidgets.QVBoxLayout(settings_group)

        proxy_group = QtWidgets.QGroupBox("KiotProxy")
        proxy_group.setToolTip("Nhập API key KiotProxy để tự động cấp và thu hồi proxy cho từng luồng đăng")
        proxy_group_layout = QtWidgets.QVBoxLayout(proxy_group)
        proxy_desc = QtWidgets.QLabel("Nhập API key và bật chức năng để hệ thống tự động gọi /current và /out cho từng lần sử dụng proxy.")
        proxy_desc.setWordWrap(True)
        proxy_desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        proxy_group_layout.addWidget(proxy_desc)

        self.proxy_enabled_checkbox = QtWidgets.QCheckBox("Dùng proxy")
        self.proxy_enabled_checkbox.toggled.connect(self._on_proxy_settings_changed)
        proxy_group_layout.addWidget(self.proxy_enabled_checkbox)

        provider_layout = QtWidgets.QHBoxLayout()
        provider_label = QtWidgets.QLabel("Nhà cung cấp:")
        provider_label.setStyleSheet("font-weight: 600;")
        provider_layout.addWidget(provider_label)
        self.proxy_provider_combo = QtWidgets.QComboBox()
        self.proxy_provider_combo.addItems(["kiotproxy", "generic"])
        self.proxy_provider_combo.currentTextChanged.connect(self._on_proxy_settings_changed)
        provider_layout.addWidget(self.proxy_provider_combo)
        proxy_group_layout.addLayout(provider_layout)

        proxy_key_layout = QtWidgets.QHBoxLayout()
        proxy_key_label = QtWidgets.QLabel("API key:")
        proxy_key_label.setStyleSheet("font-weight: 600;")
        proxy_key_layout.addWidget(proxy_key_label)
        self.proxy_key_input = QtWidgets.QLineEdit()
        self.proxy_key_input.setPlaceholderText("Nhập API key hoặc token")
        self.proxy_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.proxy_key_input.textChanged.connect(self._on_proxy_settings_changed)
        proxy_key_layout.addWidget(self.proxy_key_input)
        proxy_group_layout.addLayout(proxy_key_layout)

        self.proxy_endpoint_input = QtWidgets.QLineEdit()
        self.proxy_endpoint_input.setPlaceholderText("Ví dụ: https://api.zingproxy.com/open/change-ip/{key}")
        self.proxy_endpoint_input.textChanged.connect(self._on_proxy_settings_changed)
        proxy_group_layout.addWidget(QtWidgets.QLabel("Mẫu endpoint:"))
        proxy_group_layout.addWidget(self.proxy_endpoint_input)

        settings_group_layout.addWidget(proxy_group)

        concurrency_group = QtWidgets.QGroupBox("Đăng đồng thời")
        concurrency_group.setToolTip("Cấu hình đa luồng cho việc đăng nhiều page cùng lúc")
        concurrency_layout = QtWidgets.QVBoxLayout(concurrency_group)
        concurrency_desc = QtWidgets.QLabel(
            "Bật chế độ đăng đồng thời để xử lý nhiều page cùng lúc. "
            "Hệ thống sẽ dùng số luồng bạn chọn và chờ một khoảng thời gian trước mỗi lần đăng tiếp theo."
        )
        concurrency_desc.setWordWrap(True)
        concurrency_desc.setStyleSheet("color: #334155; margin-bottom: 6px; font-weight: 650;")
        concurrency_layout.addWidget(concurrency_desc)

        concurrency_header = QtWidgets.QHBoxLayout()
        self.concurrent_enabled_checkbox = QtWidgets.QCheckBox("Bật đăng đồng thời")
        self.concurrent_enabled_checkbox.toggled.connect(self._on_concurrency_setting_changed)
        concurrency_header.addWidget(self.concurrent_enabled_checkbox)
        concurrency_header.addStretch(1)
        concurrency_layout.addLayout(concurrency_header)

        concurrency_controls = QtWidgets.QGridLayout()
        concurrency_controls.setSpacing(10)
        threads_label = QtWidgets.QLabel("Số luồng:")
        threads_label.setStyleSheet("font-weight: 600;")
        concurrency_controls.addWidget(threads_label, 0, 0)
        self.concurrent_threads_spin = QtWidgets.QSpinBox()
        self.concurrent_threads_spin.setRange(1, 10)
        self.concurrent_threads_spin.setValue(2)
        self.concurrent_threads_spin.setToolTip("Số luồng chạy đồng thời")
        self.concurrent_threads_spin.valueChanged.connect(self._on_concurrency_setting_changed)
        concurrency_controls.addWidget(self.concurrent_threads_spin, 0, 1)

        delay_label = QtWidgets.QLabel("Độ trễ giữa các lần đăng (giây):")
        delay_label.setStyleSheet("font-weight: 600;")
        concurrency_controls.addWidget(delay_label, 1, 0)
        self.concurrent_delay_spin = QtWidgets.QDoubleSpinBox()
        self.concurrent_delay_spin.setRange(0.0, 300.0)
        self.concurrent_delay_spin.setSingleStep(0.5)
        self.concurrent_delay_spin.setValue(2.0)
        self.concurrent_delay_spin.setToolTip("Chờ trước khi đăng lần tiếp theo")
        self.concurrent_delay_spin.valueChanged.connect(self._on_concurrency_setting_changed)
        concurrency_controls.addWidget(self.concurrent_delay_spin, 1, 1)
        concurrency_layout.addLayout(concurrency_controls)

        concurrency_hint = QtWidgets.QLabel("Mẹo: dùng 2-4 luồng nếu mạng ổn định, tránh tăng quá cao để giảm rủi ro bị chặn bởi Facebook.")
        concurrency_hint.setWordWrap(True)
        concurrency_hint.setStyleSheet("color: #0284c7; font-size: 11px; font-weight: 700;")
        concurrency_layout.addWidget(concurrency_hint)

        settings_layout.addWidget(settings_group)
        settings_layout.addWidget(concurrency_group)
        settings_layout.insertWidget(1, proxy_group)
        settings_layout.insertWidget(2, concurrency_group)
        self.tabs.addTab(self.settings_tab, "Cài đặt chung")

    def _validate_license_code(self, code: str) -> Optional[datetime]:
        return self.license_manager.validate_code(code)

    def _on_concurrency_setting_changed(self) -> None:
        self.concurrency_enabled = self.concurrent_enabled_checkbox.isChecked()
        self.concurrency_threads = max(1, self.concurrent_threads_spin.value())
        self.concurrency_delay = float(self.concurrent_delay_spin.value())
        self._save_config()

    def _on_proxy_settings_changed(self) -> None:
        self.proxy_api_key = self.proxy_key_input.text().strip()
        self.proxy_provider_name = self.proxy_provider_combo.currentText().strip() or "kiotproxy"
        self.proxy_endpoint_template = self.proxy_endpoint_input.text().strip()
        self.proxy_enabled = self.proxy_enabled_checkbox.isChecked() and bool(self.proxy_api_key)
        os.environ["KIOTPROXY_API_KEY"] = self.proxy_api_key
        os.environ["KIOTPROXY_REGION"] = self.proxy_region
        os.environ["PROXY_PROVIDER"] = self.proxy_provider_name
        os.environ["PROXY_ENDPOINT_TEMPLATE"] = self.proxy_endpoint_template
        self._save_config()

    def _on_schedule_settings_changed(self, *_args) -> None:
        self._update_schedule_controls_visibility()
        self._save_config()

    def _update_schedule_controls_visibility(self, *_args) -> None:
        enabled = self.schedule_checkbox.isChecked() if hasattr(self, "schedule_checkbox") else False
        mode = str(self.schedule_mode_combo.currentData() or "once") if hasattr(self, "schedule_mode_combo") else "once"
        if hasattr(self, "schedule_once_row"):
            self.schedule_once_row.setVisible(enabled and mode == "once")
        if hasattr(self, "schedule_interval_row"):
            self.schedule_interval_row.setVisible(enabled and mode == "interval")
        if hasattr(self, "schedule_daily_row"):
            self.schedule_daily_row.setVisible(enabled and mode in {"daily", "weekly"})
        if hasattr(self, "schedule_weekday_row"):
            self.schedule_weekday_row.setVisible(enabled and mode == "weekly")
        if hasattr(self, "schedule_repeat_hint"):
            self.schedule_repeat_hint.setVisible(enabled)

    def _load_schedule_settings(self, schedule_data: Optional[Dict[str, Any]]) -> None:
        payload = schedule_data or {}
        enabled = bool(payload.get("enabled", False))
        mode = str(payload.get("mode") or "").strip().lower()
        if not mode and payload.get("datetime"):
            mode = "once"
        if hasattr(self, "schedule_checkbox"):
            self.schedule_checkbox.setChecked(enabled)
        if hasattr(self, "schedule_mode_combo"):
            target_index = self.schedule_mode_combo.findData(mode or "once")
            if target_index >= 0:
                self.schedule_mode_combo.setCurrentIndex(target_index)
        once_value = payload.get("once_datetime") or payload.get("datetime")
        if once_value and hasattr(self, "schedule_datetime"):
            try:
                parsed = datetime.fromisoformat(str(once_value))
                self.schedule_datetime.setDateTime(QtCore.QDateTime(parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second))
            except Exception:
                pass
        if hasattr(self, "schedule_interval_spin"):
            try:
                self.schedule_interval_spin.setValue(float(payload.get("interval_hours", 24.0)))
            except Exception:
                self.schedule_interval_spin.setValue(24.0)
        if hasattr(self, "schedule_time_of_day"):
            time_value = payload.get("time_of_day") or payload.get("daily_time")
            if time_value:
                qtime = QtCore.QTime.fromString(str(time_value), "HH:mm")
                if qtime.isValid():
                    self.schedule_time_of_day.setTime(qtime)
        weekdays = payload.get("weekdays", [])
        if isinstance(weekdays, str):
            weekdays = [int(part.strip()) for part in weekdays.split(",") if part.strip().isdigit()]
        weekday_set = {int(value) for value in weekdays if str(value).isdigit() or isinstance(value, int)}
        if hasattr(self, "schedule_weekday_checks"):
            for idx, chk in enumerate(self.schedule_weekday_checks):
                chk.setChecked(idx in weekday_set)
        self._update_schedule_controls_visibility()

    def _build_schedule_data(self) -> Dict[str, Any]:
        schedule_checkbox = self._safe_get_attr("schedule_checkbox")
        schedule_mode_combo = self._safe_get_attr("schedule_mode_combo")
        schedule_interval_spin = self._safe_get_attr("schedule_interval_spin")
        schedule_time_of_day = self._safe_get_attr("schedule_time_of_day")
        schedule_weekday_checks = self._safe_get_attr("schedule_weekday_checks", [])
        schedule_datetime = self._safe_get_attr("schedule_datetime")

        mode = str(schedule_mode_combo.currentData() or "once") if schedule_mode_combo is not None else "once"
        payload: Dict[str, Any] = {
            "enabled": schedule_checkbox.isChecked() if schedule_checkbox is not None else False,
            "mode": mode,
            "once_datetime": None,
            "interval_hours": float(schedule_interval_spin.value()) if schedule_interval_spin is not None else 24.0,
            "time_of_day": schedule_time_of_day.time().toString("HH:mm") if schedule_time_of_day is not None else None,
            "weekdays": [idx for idx, chk in enumerate(schedule_weekday_checks) if chk.isChecked()],
        }
        if schedule_checkbox is not None and schedule_checkbox.isChecked() and schedule_datetime is not None:
            payload["once_datetime"] = schedule_datetime.dateTime().toPyDateTime().isoformat()
        return payload

    def _selected_schedule_base_time(self) -> Optional[datetime]:
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
                weekdays = [idx for idx, chk in enumerate(self.schedule_weekday_checks) if chk.isChecked()]
                if weekdays:
                    return self._next_weekday_datetime(now, weekdays, time_obj)
                return None
            return candidate
        return None

    def _next_weekday_datetime(self, base_time: datetime, weekdays: List[int], time_obj: Any) -> Optional[datetime]:
        if not weekdays:
            return None
        candidates: List[datetime] = []
        for weekday in weekdays:
            days_ahead = (weekday - base_time.weekday()) % 7
            candidate = datetime.combine((base_time + timedelta(days=days_ahead)).date(), time_obj)
            if candidate <= base_time:
                candidate += timedelta(days=7)
            candidates.append(candidate)
        if not candidates:
            return None
        return min(candidates)

    def _next_repeat_run_time(self) -> Optional[datetime]:
        if not self.schedule_checkbox.isChecked():
            return None
        mode = str(self.schedule_mode_combo.currentData() or "once")
        if mode == "once":
            return None
        now = datetime.now()
        if mode == "interval":
            return now + timedelta(hours=float(self.schedule_interval_spin.value()))
        if mode == "daily":
            return self._selected_schedule_base_time()
        if mode == "weekly":
            weekdays = [idx for idx, chk in enumerate(self.schedule_weekday_checks) if chk.isChecked()]
            time_obj = self.schedule_time_of_day.time().toPyTime()
            return self._next_weekday_datetime(now, weekdays, time_obj)
        return None

    def _run_scheduled_config_post(self) -> None:
        if self.stop_requested:
            return
        self._bypass_schedule_wait_once = True
        self._start_posting_from_config()

    @staticmethod
    def _is_facebook_block_error(message: str) -> bool:
        lowered = (message or "").lower()
        return any(token in lowered for token in ["spam", "rate limit", "temporarily blocked", "temporarily limited", "too many requests", "anti-spam", "repeated requests"])

    @staticmethod
    def _retry_delay_for_attempt(attempt: int, base_delay: Optional[int] = None) -> int:
        base = base_delay if base_delay is not None else 10
        return base * max(1, attempt)

    def _build_proxy_provider(self) -> Optional[ProxyProvider]:
        if not self.proxy_enabled or not self.proxy_api_key:
            return None
        if self.proxy_provider_name == "generic":
            return GenericProxyProvider(self.proxy_api_key, region=self.proxy_region, endpoint_template=self.proxy_endpoint_template)
        return KiotProxyProvider(self.proxy_api_key, region=self.proxy_region)

    def _fetch_proxy(self) -> Optional[Dict[str, Any]]:
        provider = self.proxy_provider or self._build_proxy_provider()
        self.proxy_provider = provider
        if not provider:
            return None
        proxy_info = provider.fetch_proxy()
        if isinstance(proxy_info, dict) and proxy_info.get("http"):
            return proxy_info
        return None

    def _release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        if not self.proxy_provider:
            return
        self.proxy_provider.release_proxy(proxy_data)

    def _build_proxy_config(self, proxy_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        if not proxy_data:
            return None
        http_proxy = proxy_data.get("http")
        if not http_proxy:
            return None
        return {"http": f"http://{http_proxy}", "https": f"http://{http_proxy}", "socks5": f"socks5://{proxy_data.get('socks5', http_proxy)}"}

    def _show_message_box(
        self,
        title: str,
        text: str,
        icon: QtWidgets.QMessageBox.Icon,
        buttons: QtWidgets.QMessageBox.StandardButtons = QtWidgets.QMessageBox.Ok,
    ) -> int:
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle(title)
        message.setText(text)
        message.setIcon(icon)
        message.setStandardButtons(buttons)
        message.setStyleSheet(
            "QMessageBox { background-color: #f8fafc; color: #0f172a; font-family: Nunito; }\n"
            "QLabel { color: #334155; background: transparent; font-family: Nunito; font-weight: 700; }\n"
            "QPushButton { background-color: #0284c7; color: #ffffff; border: 1px solid #0284c7; border-radius: 8px; padding: 7px 12px; min-width: 90px; font-family: Nunito; font-weight: 800; }\n"
            "QPushButton:hover { background-color: #0ea5e9; }\n"
            "QPushButton:pressed { background-color: #0369a1; }\n"
            "QPushButton:default { background-color: #0284c7; border: 1px solid #0284c7; }"
        )
        return message.exec_()

    def _show_styled_message(self, title: str, text: str, icon: QtWidgets.QMessageBox.Icon) -> None:
        self._show_message_box(title, text, icon)

    def _append_log(self, message: str, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = {"info": "INFO", "warning": "WARN", "error": "ERROR"}.get(level.lower(), "INFO")
        line = f"{timestamp} [{prefix}] {message}"
        if hasattr(self, "log_output") and self.log_output is not None:
            self.log_output.appendPlainText(line)
            self.log_output.ensureCursorVisible()
        logger_method = getattr(self.logger, level.lower(), None)
        if callable(logger_method):
            logger_method(message)

    def _set_main_controls_enabled(self, enabled: bool) -> None:
        controls = [
            self.load_tokens_btn,
            self.load_pages_btn,
            self.select_all_btn,
            self.clear_btn,
            self.post_selected_btn,
            self.post_all_btn,
            self.stop_btn,
            self.check_tokens_btn,
            self.refresh_token_btn,
            self.clear_cache_btn,
            self.add_row_btn,
            self.message_input,
            self.image_paths_input,
            self.video_paths_input,
            self.schedule_checkbox,
            self.schedule_mode_combo,
            self.schedule_datetime,
            self.schedule_once_row,
            self.schedule_interval_row,
            self.schedule_interval_spin,
            self.schedule_daily_row,
            self.schedule_time_of_day,
            self.schedule_weekday_row,
            *getattr(self, "schedule_weekday_checks", []),
            self.token_input,
            self.page_table,
            self.config_table,
        ]
        for control in controls:
            control.setEnabled(enabled)
        self.activate_trial_btn.setEnabled(True)
        if hasattr(self, "reset_license_btn"):
            self.reset_license_btn.setEnabled(True)

    def _activate_license(self, code: str, expires_at: datetime) -> None:
        try:
            self.license_manager.activate(code, expires_at)
            self.license_manager.hide_state_file()
            self.license_locked = False
            self._set_main_controls_enabled(True)
            self.status_label.setText("Bản dùng thử đã được kích hoạt")
            self._update_trial_status()
        except Exception as exc:
            self.logger.exception("Không thể kích hoạt license: %s", exc)
            self._show_styled_message("Lỗi kích hoạt", f"Không thể lưu trạng thái license: {exc}", QtWidgets.QMessageBox.Critical)
            self._lock_trial_expired()

    def _update_trial_status(self) -> None:
        if self.license_manager.is_active():
            self.trial_status_label.setText(self.license_manager.remaining_label())
            self.trial_status_label.setStyleSheet("background: #052e2b; border: 1px solid #14b8a6; border-radius: 8px; padding: 6px 10px; color: #ccfbf1; font-weight: 800;")
        elif self.license_manager.expires_at is None and self.license_manager.code is None:
            self.trial_status_label.setText("Trial: chưa kích hoạt")
            self.trial_status_label.setStyleSheet("background: #1c1404; border: 1px solid #f59e0b; border-radius: 8px; padding: 6px 10px; color: #fde68a; font-weight: 800;")
        else:
            self.trial_status_label.setText("Trial: đã hết hạn")
            self.trial_status_label.setStyleSheet("background: #1f0707; border: 1px solid #ef4444; border-radius: 8px; padding: 6px 10px; color: #fecaca; font-weight: 800;")
            if not self.license_locked:
                self._lock_trial_expired()

    def _lock_trial_expired(self) -> None:
        self.license_locked = True
        self._set_main_controls_enabled(False)
        self.status_label.setText("Bản dùng thử chưa được kích hoạt hoặc đã hết hạn. Vui lòng kích hoạt mã để tiếp tục.")
        self.trial_status_label.setText("Trial: đã hết hạn")
        self.trial_status_label.setStyleSheet("background: #1f0707; border: 1px solid #ef4444; border-radius: 8px; padding: 6px 10px; color: #fecaca; font-weight: 800;")

    def open_trial_dialog(self) -> None:
        try:
            dialog = TrialLicenseDialog(self)
            if dialog.exec_() == QtWidgets.QDialog.Accepted:
                expires_at = self._validate_license_code(dialog.code)
                if expires_at:
                    if self.license_manager.is_active() and not self.license_manager.is_permanent() and dialog.code.strip().lower() != "vietboss1998":
                        self._show_styled_message("Thông báo", "Bản dùng thử đã được kích hoạt. Chỉ mã `vietboss1998` mới có thể ghi đè trạng thái hiện tại.", QtWidgets.QMessageBox.Information)
                        self._update_trial_status()
                        return
                    self._activate_license(dialog.code, expires_at)
                    if dialog.code.strip().lower() != "vietboss1998":
                        self._show_styled_message("Thành công", f"Đã kích hoạt bản dùng thử cho {dialog.code}", QtWidgets.QMessageBox.Information)
                else:
                    self._show_styled_message("Mã không hợp lệ", "LH zl 0363657998", QtWidgets.QMessageBox.Warning)
                    self._lock_trial_expired()
            else:
                self._lock_trial_expired()
        except Exception as exc:
            self.logger.exception("Lỗi khi mở dialog kích hoạt: %s", exc)
            self._show_styled_message("Lỗi", f"Không thể mở hoặc xử lý mã kích hoạt: {exc}", QtWidgets.QMessageBox.Critical)
            self._lock_trial_expired()

    def reset_license_from_ui(self) -> None:
        reply = self._show_message_box(
            "Xóa license & nhập lại",
            "Bạn có chắc muốn xóa toàn bộ trạng thái license hiện tại và nhập lại mã kích hoạt?",
            QtWidgets.QMessageBox.Question,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.license_manager.clear()
            self.license_manager.hide_state_file()
            self.license_locked = False
            self._lock_trial_expired()
            self.status_label.setText("Đã xóa license. Hãy nhập lại mã kích hoạt.")
            self._show_styled_message("Đã xóa license", "License đã được xóa. Hãy nhập lại mã kích hoạt.", QtWidgets.QMessageBox.Information)
            QtCore.QTimer.singleShot(0, self.open_trial_dialog)
        except Exception as exc:
            self.logger.exception("Không thể reset license: %s", exc)
            self._show_styled_message("Lỗi reset license", f"Không thể xóa trạng thái license: {exc}", QtWidgets.QMessageBox.Critical)

    def _ensure_trial_access(self) -> None:
        try:
            self.license_manager.load_state()
            if self.license_manager.is_active():
                self._activate_license(self.license_manager.code or "1D", self.license_manager.expires_at or datetime(2099, 12, 31, 23, 59, 59))
                return
            self.open_trial_dialog()
        except Exception as exc:
            self.logger.exception("Lỗi kiểm tra trial: %s", exc)
            self._show_styled_message("Lỗi", f"Không thể kiểm tra trạng thái bản dùng thử: {exc}", QtWidgets.QMessageBox.Critical)
            self._lock_trial_expired()

    def _load_saved_config(self) -> None:
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return

        tokens = [str(token).strip() for token in payload.get("tokens", []) if str(token).strip()]
        if tokens:
            self.token_input.setPlainText("\n".join(tokens))
            self.status_label.setText(f"Đã tải {len(tokens)} token từ cấu hình")
            if self.license_manager.is_active():
                QtCore.QTimer.singleShot(300, lambda: self.load_all_pages())

        schedule_data = payload.get("quick_schedule") or payload.get("schedule") or {}
        self._load_schedule_settings(schedule_data)

        concurrency_data = payload.get("concurrency", {})
        self.concurrent_enabled_checkbox.setChecked(bool(concurrency_data.get("enabled", False)))
        self.concurrent_threads_spin.setValue(int(concurrency_data.get("threads", 2)))
        self.concurrent_delay_spin.setValue(float(concurrency_data.get("delay", 2.0)))
        self.concurrency_enabled = self.concurrent_enabled_checkbox.isChecked()
        self.concurrency_threads = max(1, self.concurrent_threads_spin.value())
        self.concurrency_delay = float(self.concurrent_delay_spin.value())

        message = payload.get("message", "")
        if message:
            self.message_input.setPlainText(message)

        images = payload.get("images", [])
        if images:
            self.image_paths_input.setText(";".join(images))

        videos = payload.get("videos", [])
        if videos:
            self.video_paths_input.setText(";".join(videos))

        post_type = self._normalize_post_type(payload.get("post_type", ""))
        index = self.post_type_combo.findData(post_type)
        if index >= 0:
            self.post_type_combo.setCurrentIndex(index)

        video_folder = payload.get("video_folder", "")
        if video_folder:
            self.video_folder_input.setText(str(video_folder))

        comment_image_folder = payload.get("comment_image_folder", "")
        if comment_image_folder:
            self.comment_image_folder_input.setText(str(comment_image_folder))

        use_comment_images = payload.get("use_comment_images", False)
        self.use_comment_images_checkbox.setChecked(bool(use_comment_images))

        skip_missing_videos = payload.get("skip_missing_videos", True)
        self.skip_missing_videos_checkbox.setChecked(bool(skip_missing_videos))

        quick_config = payload.get("quick_config", {})
        self.quick_post_content_input.setPlainText(str(quick_config.get("post_content", "") or ""))
        self.quick_comment_content_input.setPlainText(str(quick_config.get("comment_content", "") or ""))

        proxy_config = payload.get("proxy", {})
        proxy_enabled = bool(proxy_config.get("enabled", False))
        proxy_key = str(proxy_config.get("key", "") or "")
        provider_name = str(proxy_config.get("provider", self.proxy_provider_name) or self.proxy_provider_name)
        endpoint_template = str(proxy_config.get("endpoint_template", self.proxy_endpoint_template) or "")
        self.proxy_enabled_checkbox.setChecked(proxy_enabled)
        self.proxy_provider_combo.setCurrentText(provider_name)
        self.proxy_key_input.setText(proxy_key)
        self.proxy_endpoint_input.setText(endpoint_template)
        self.proxy_api_key = proxy_key
        self.proxy_provider_name = provider_name
        self.proxy_endpoint_template = endpoint_template
        self.proxy_enabled = proxy_enabled and bool(proxy_key)
        os.environ["KIOTPROXY_API_KEY"] = self.proxy_api_key
        os.environ["KIOTPROXY_REGION"] = self.proxy_region
        os.environ["PROXY_PROVIDER"] = self.proxy_provider_name
        os.environ["PROXY_ENDPOINT_TEMPLATE"] = self.proxy_endpoint_template

        self._restore_config_rows(payload.get("config_rows", []))

    def _safe_get_attr(self, name: str, default: Any = None) -> Any:
        try:
            return self.__dict__.get(name, default)
        except Exception:
            return default

    def _save_config(self, tokens: Optional[List[str]] = None, schedule_data: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {}
        if tokens is None:
            token_input = self._safe_get_attr("token_input")
            if token_input is not None:
                tokens = [t.strip() for t in token_input.toPlainText().splitlines() if t.strip()]
            else:
                tokens = []
        payload["tokens"] = tokens
        schedule_payload = schedule_data or self._build_schedule_data()
        payload["schedule"] = schedule_payload
        payload["quick_schedule"] = schedule_payload
        concurrent_enabled_checkbox = self._safe_get_attr("concurrent_enabled_checkbox")
        concurrent_threads_spin = self._safe_get_attr("concurrent_threads_spin")
        concurrent_delay_spin = self._safe_get_attr("concurrent_delay_spin")
        message_input = self._safe_get_attr("message_input")
        image_paths_input = self._safe_get_attr("image_paths_input")
        video_paths_input = self._safe_get_attr("video_paths_input")
        post_type_combo = self._safe_get_attr("post_type_combo")
        video_folder_input = self._safe_get_attr("video_folder_input")
        comment_image_folder_input = self._safe_get_attr("comment_image_folder_input")
        use_comment_images_checkbox = self._safe_get_attr("use_comment_images_checkbox")
        skip_missing_videos_checkbox = self._safe_get_attr("skip_missing_videos_checkbox")
        quick_post_content_input = self._safe_get_attr("quick_post_content_input")
        quick_comment_content_input = self._safe_get_attr("quick_comment_content_input")
        proxy_enabled_checkbox = self._safe_get_attr("proxy_enabled_checkbox")
        proxy_key_input = self._safe_get_attr("proxy_key_input")
        proxy_provider_combo = self._safe_get_attr("proxy_provider_combo")
        proxy_endpoint_input = self._safe_get_attr("proxy_endpoint_input")
        config_table = self._safe_get_attr("config_table")

        payload["concurrency"] = {
            "enabled": concurrent_enabled_checkbox.isChecked() if concurrent_enabled_checkbox is not None else False,
            "threads": max(1, concurrent_threads_spin.value()) if concurrent_threads_spin is not None else 2,
            "delay": float(concurrent_delay_spin.value()) if concurrent_delay_spin is not None else 2.0,
        }
        payload["message"] = message_input.toPlainText().strip() if message_input is not None else ""
        payload["images"] = [p.strip() for p in image_paths_input.text().split(";") if p.strip()] if image_paths_input is not None else []
        payload["videos"] = [p.strip() for p in video_paths_input.text().split(";") if p.strip()] if video_paths_input is not None else []
        payload["post_type"] = self._normalize_post_type(post_type_combo.currentData() or post_type_combo.currentText()) if post_type_combo is not None else "video"
        payload["video_folder"] = video_folder_input.text().strip() if video_folder_input is not None else ""
        payload["comment_image_folder"] = comment_image_folder_input.text().strip() if comment_image_folder_input is not None else ""
        payload["use_comment_images"] = use_comment_images_checkbox.isChecked() if use_comment_images_checkbox is not None else False
        payload["skip_missing_videos"] = skip_missing_videos_checkbox.isChecked() if skip_missing_videos_checkbox is not None else True
        payload["quick_config"] = {
            "post_content": quick_post_content_input.toPlainText().strip() if quick_post_content_input is not None else "",
            "comment_content": quick_comment_content_input.toPlainText().strip() if quick_comment_content_input is not None else "",
        }
        payload["proxy"] = {
            "enabled": proxy_enabled_checkbox.isChecked() if proxy_enabled_checkbox is not None else False,
            "key": proxy_key_input.text().strip() if proxy_key_input is not None else "",
            "region": self._safe_get_attr("proxy_region", "random"),
            "provider": proxy_provider_combo.currentText().strip() or "kiotproxy" if proxy_provider_combo is not None else "kiotproxy",
            "endpoint_template": proxy_endpoint_input.text().strip() if proxy_endpoint_input is not None else "",
        }
        payload["config_rows"] = self._collect_config_rows(config_table) if config_table is not None else []
        config_path = self._safe_get_attr("config_path")
        if config_path is None:
            config_path = _runtime_dir() / "facebook_config.json"
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _collect_config_rows(self, table: Optional[QtWidgets.QTableWidget] = None) -> List[Dict[str, str]]:
        table = table or self._safe_get_attr("config_table")
        if table is None:
            return []
        rows: List[Dict[str, str]] = []
        for row_idx in range(table.rowCount()):
            values = []
            for col_idx in range(len(CONFIG_HEADERS)):
                item = table.item(row_idx, col_idx)
                values.append(item.text().strip() if item is not None else "")
            if not any(values):
                continue
            rows.append({
                "page": values[0],
                "title": values[1],
                "description": values[2],
                "video_path": values[3],
                "comment": values[4],
                "comment_images": values[CONFIG_COMMENT_IMAGE_COL],
                "post_type": self._normalize_post_type(values[CONFIG_POST_TYPE_COL]),
                "schedule_time": values[CONFIG_SCHEDULE_COL],
                "status": values[CONFIG_STATUS_COL],
                "link": values[CONFIG_LINK_COL],
            })
        return rows

    def _restore_config_rows(self, rows: Any) -> None:
        if not isinstance(rows, list) or not rows:
            return
        table = self._safe_get_attr("config_table")
        if table is None:
            return
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            for row_data in rows:
                if not isinstance(row_data, dict):
                    continue
                row_idx = table.rowCount()
                table.insertRow(row_idx)
                self._set_table_value(row_idx, 0, row_data.get("page", ""))
                self._set_table_value(row_idx, 1, row_data.get("title", ""))
                self._set_table_value(row_idx, 2, row_data.get("description", ""))
                self._set_table_value(row_idx, 3, row_data.get("video_path", ""))
                self._set_table_value(row_idx, 4, row_data.get("comment", ""))
                self._set_table_value(row_idx, CONFIG_COMMENT_IMAGE_COL, row_data.get("comment_images", ""))
                self._set_config_row_post_type(row_idx, row_data.get("post_type", "video"))
                self._set_table_value(row_idx, CONFIG_SCHEDULE_COL, row_data.get("schedule_time", ""))
                self._set_table_value(row_idx, CONFIG_STATUS_COL, row_data.get("status", "Chờ đăng"))
                self._set_config_link_cell(row_idx, row_data.get("link", ""))
                table.setRowHeight(row_idx, 42)
        finally:
            table.blockSignals(False)

    def _periodic_token_check(self) -> None:
        if self._periodic_check_running:
            return
        self.check_tokens_now()

    def check_tokens_now(self) -> None:
        tokens = [t.strip() for t in self.token_input.toPlainText().splitlines() if t.strip()]
        if not tokens:
            return
        self._periodic_check_running = True
        threading.Thread(target=self._validate_tokens_thread, args=(tokens,), daemon=True).start()

    def _validate_tokens_thread(self, tokens: List[str]) -> None:
        try:
            for token in tokens:
                validation = self._validate_token(token)
                self.token_statuses[token] = {
                    "valid": validation.get("valid", False),
                    "status": "Valid" if validation.get("valid") else "Invalid",
                    "error": validation.get("error"),
                }
                self._save_token_state(token, validation.get("valid", False), None, validation.get("error"))
                self._save_account_state(
                    token=token,
                    is_valid=validation.get("valid", False),
                    expires_at=validation.get("expires_at"),
                    error=validation.get("error"),
                    account_id=validation.get("account_id"),
                    account_name=validation.get("account_name"),
                    page_count=0,
                )
            self.accounts_refresh_requested.emit()
            self.page_status_requested.emit()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self._periodic_check_running = False

    @QtCore.pyqtSlot()
    def _update_page_statuses(self) -> None:
        if not self.pages:
            return
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        visible_pages = self.pages[start:end]
        for row, page in enumerate(visible_pages):
            token = page.get("account_token") or page.get("access_token")
            info = self.token_statuses.get(token, {"valid": True, "status": "Valid", "error": ""})
            status_text = info.get("status", "Valid")
            status_item = self.page_table.item(row, 2)
            if status_item is None:
                status_item = QtWidgets.QTableWidgetItem(status_text)
                self.page_table.setItem(row, 2, status_item)
            else:
                status_item.setText(status_text)
            if not info.get("valid"):
                status_item.setForeground(QtGui.QColor("#ef4444"))
                status_item.setToolTip("Token không hợp lệ hoặc hết hạn. Vui lòng refresh token.")
            elif status_text == "Cache":
                status_item.setForeground(QtGui.QColor("#3b82f6"))
            else:
                status_item.setForeground(QtGui.QColor("#22c55e"))
        invalid_count = sum(1 for info in self.token_statuses.values() if not info.get("valid"))
        if invalid_count:
            self.status_label.setText(f"Đã kiểm tra token: có {invalid_count} token không hợp lệ hoặc hết hạn")
        else:
            self.status_label.setText("Đã kiểm tra token: tất cả token đang hoạt động")

    def refresh_token(self) -> None:
        tokens = [t.strip() for t in self.token_input.toPlainText().splitlines() if t.strip()]
        if not tokens:
            self._show_message_box("Thiếu token", "Vui lòng nhập token trước", QtWidgets.QMessageBox.Warning)
            return
        invalid_tokens = [token for token in tokens if not self.token_statuses.get(token, {}).get("valid", True)]
        if not invalid_tokens:
            self._show_message_box("Thông báo", "Không có token nào cần refresh", QtWidgets.QMessageBox.Information)
            return
        new_tokens_text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Refresh token",
            "Nhập token mới (mỗi token 1 dòng):",
        )
        if not ok or not new_tokens_text.strip():
            return
        new_tokens = [line.strip() for line in new_tokens_text.splitlines() if line.strip()]
        updated_tokens = list(tokens)
        for index, invalid_token in enumerate(invalid_tokens):
            replacement = new_tokens[index] if index < len(new_tokens) else new_tokens[-1]
            try:
                updated_tokens[updated_tokens.index(invalid_token)] = replacement
            except ValueError:
                continue
        self.token_input.setPlainText("\n".join(updated_tokens))
        self._save_config(tokens=updated_tokens)
        self._clear_cache_for_tokens(invalid_tokens)
        self.status_label.setText("Đang tải lại page với token mới...")
        threading.Thread(target=self._load_all_pages_thread, args=(updated_tokens,), daemon=True).start()

    def clear_cache(self) -> None:
        reply = self._show_message_box(
            "Xóa cache",
            "Bạn có muốn xóa toàn bộ dữ liệu page và trạng thái token đã lưu không?",
            QtWidgets.QMessageBox.Question,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pages")
            conn.execute("DELETE FROM tokens")
            conn.execute("DELETE FROM accounts")
            conn.execute("DELETE FROM page_media_rotation")
            conn.commit()
        self.pages = []
        self.account_pages = {}
        self.token_statuses = {}
        self.page_table.setRowCount(0)
        self.status_label.setText("Đã xóa cache page và trạng thái token")
        self.accounts_refresh_requested.emit()

    def _clear_cache_for_tokens(self, tokens: List[str]) -> None:
        if not tokens:
            return
        with sqlite3.connect(self.db_path) as conn:
            for token in tokens:
                conn.execute("DELETE FROM pages WHERE token = ?", (token,))
            conn.commit()

    def _load_pages_from_database(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT token, page_id, page_name, page_access_token, account_label, page_type, last_updated
                FROM pages
                ORDER BY last_updated DESC, page_name COLLATE NOCASE ASC
                """
            ).fetchall()

        pages: List[Dict[str, Any]] = []
        for row in rows:
            token, page_id, page_name, page_access_token, account_label, page_type, last_updated = row
            pages.append(
                {
                    "id": page_id,
                    "name": page_name,
                    "access_token": page_access_token,
                    "account_label": account_label or str(token or "")[:12],
                    "account_token": token,
                    "page_type": page_type,
                    "last_updated": last_updated,
                    "status": "Cache",
                }
            )

        self.pages = pages
        self.pages_loaded.emit(pages)
        self._update_page_selection_summary()

    def _pick_files(self, title: str, filter_text: str, multiple: bool = False, save: bool = False) -> List[str]:
        dialog = QtWidgets.QFileDialog(self)
        dialog.setWindowTitle(title)
        dialog.setNameFilter(filter_text)
        if multiple:
            dialog.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        else:
            dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        if save:
            dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        if dialog.exec_():
            return dialog.selectedFiles()
        return []

    def load_tokens_from_file(self) -> None:
        paths = self._pick_files("Chọn file txt", "Text files (*.txt)")
        if not paths:
            return
        path = paths[0]
        try:
            content = Path(path).read_text(encoding="utf-8")
            tokens = [line.strip() for line in content.splitlines() if line.strip()]
            current_text = self.token_input.toPlainText().strip()
            if current_text:
                current_text += "\n"
            self.token_input.setPlainText(current_text + "\n".join(tokens))
            self._save_config(tokens=tokens)
            self.status_label.setText(f"Đã nhập {len(tokens)} token từ file")
        except Exception as exc:
            self._show_message_box("Lỗi", str(exc), QtWidgets.QMessageBox.Critical)

    def load_all_pages(self) -> None:
        tokens = [t.strip() for t in self.token_input.toPlainText().splitlines() if t.strip()]
        if not tokens:
            self._show_message_box("Thiếu token", "Vui lòng nhập ít nhất 1 access token", QtWidgets.QMessageBox.Warning)
            return
        self._save_config(tokens=tokens)
        self.status_label.setText("Đang kiểm tra cache và token...")
        threading.Thread(target=self._load_all_pages_thread, args=(tokens,), daemon=True).start()

    def _load_all_pages_thread(self, tokens: List[str]) -> None:
        try:
            self.account_pages = {}
            self.token_statuses = {}
            for token in tokens:
                pages, status_info = self._load_pages_for_token(token)
                account_label = status_info.get("account_name") or token[:12]
                self.account_pages[account_label] = pages
                self.token_statuses[token] = status_info
                self._save_account_state(
                    token=token,
                    is_valid=status_info.get("valid", False),
                    expires_at=status_info.get("expires_at"),
                    error=status_info.get("error"),
                    account_id=status_info.get("account_id"),
                    account_name=account_label,
                    page_count=len(pages),
                )
            self._load_pages_from_database()
            self.accounts_refresh_requested.emit()
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def _load_pages_for_token(self, token: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        cached_pages = self._load_cached_pages(token)
        if cached_pages:
            self._save_token_state(token, True, None, None)
            validation = self._validate_token(token)
            if validation.get("valid"):
                pages = self._fetch_pages(token)
                self._save_pages(token, pages)
                self._save_token_state(token, True, validation.get("expires_at"), None)
                return self._load_cached_pages(token), {
                    "valid": True,
                    "status": "Cache",
                    "error": "",
                    "expires_at": validation.get("expires_at"),
                    "account_id": validation.get("account_id"),
                    "account_name": validation.get("account_name"),
                }
            self._save_token_state(token, False, validation.get("expires_at"), validation.get("error"))
            return cached_pages, {
                "valid": False,
                "status": "Lỗi",
                "error": validation.get("error", "Token invalid"),
                "expires_at": validation.get("expires_at"),
                "account_id": validation.get("account_id"),
                "account_name": validation.get("account_name"),
            }

        validation = self._validate_token(token)
        if not validation["valid"]:
            self._save_token_state(token, False, validation.get("expires_at"), validation.get("error"))
            return [], {
                "valid": False,
                "status": "Lỗi",
                "error": validation.get("error", "Token invalid"),
                "expires_at": validation.get("expires_at"),
                "account_id": validation.get("account_id"),
                "account_name": validation.get("account_name"),
            }

        pages = self._fetch_pages(token)
        self._save_pages(token, pages)
        self._save_token_state(token, True, validation.get("expires_at"), None)
        return self._load_cached_pages(token), {
            "valid": True,
            "status": "Valid",
            "error": "",
            "expires_at": validation.get("expires_at"),
            "account_id": validation.get("account_id"),
            "account_name": validation.get("account_name"),
        }

    def _load_cached_pages(self, token: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT page_id, page_name, page_access_token, account_label, page_type, last_updated FROM pages WHERE token = ?",
                (token,),
            ).fetchall()
        if not rows:
            return []
        pages = []
        for row in rows:
            page_id, page_name, page_access_token, account_label, page_type, last_updated = row
            pages.append({
                "id": page_id,
                "name": page_name,
                "access_token": page_access_token,
                "account_label": account_label or token[:12],
                "page_type": page_type,
                "last_updated": last_updated,
                "status": "Cache",
            })
        return pages

    def _save_pages(self, token: str, pages: List[Dict[str, Any]]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            timestamp = datetime.now().isoformat()
            for page in pages:
                conn.execute(
                    """
                    INSERT INTO pages (token, page_id, page_name, page_access_token, account_label, page_type, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(token, page_id) DO UPDATE SET
                        page_name=excluded.page_name,
                        page_access_token=excluded.page_access_token,
                        account_label=excluded.account_label,
                        page_type=excluded.page_type,
                        last_updated=excluded.last_updated
                    """,
                    (
                        token,
                        page.get("id"),
                        page.get("name"),
                        page.get("access_token", ""),
                        token[:12],
                        page.get("page_type", "me/accounts"),
                        timestamp,
                    ),
                )
            conn.commit()

    def _save_token_state(self, token: str, is_valid: bool, expires_at: Optional[str], error: Optional[str]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tokens (token, token_prefix, last_checked, is_valid, expires_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    token_prefix=excluded.token_prefix,
                    last_checked=excluded.last_checked,
                    is_valid=excluded.is_valid,
                    expires_at=excluded.expires_at,
                    error=excluded.error
                """,
                (
                    token,
                    token[:12],
                    datetime.now().isoformat(),
                    1 if is_valid else 0,
                    expires_at,
                    error,
                ),
            )
            conn.commit()

    def _save_account_state(
        self,
        token: str,
        is_valid: bool,
        expires_at: Optional[str],
        error: Optional[str],
        account_id: Optional[str] = None,
        account_name: Optional[str] = None,
        page_count: int = 0,
    ) -> None:
        label = (account_name or token[:12] or "").strip()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    token,
                    token_prefix,
                    account_id,
                    account_name,
                    account_label,
                    last_checked,
                    is_valid,
                    expires_at,
                    page_count,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    token_prefix=excluded.token_prefix,
                    account_id=excluded.account_id,
                    account_name=excluded.account_name,
                    account_label=excluded.account_label,
                    last_checked=excluded.last_checked,
                    is_valid=excluded.is_valid,
                    expires_at=excluded.expires_at,
                    page_count=excluded.page_count,
                    error=excluded.error
                """,
                (
                    token,
                    token[:12],
                    account_id,
                    account_name,
                    label,
                    datetime.now().isoformat(),
                    1 if is_valid else 0,
                    expires_at,
                    page_count,
                    error,
                ),
            )
            conn.commit()

    def _load_rotation_index(self, rotation_key: str) -> int:
        if not rotation_key:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT next_video_index FROM page_media_rotation WHERE page_key = ?",
                (rotation_key,),
            ).fetchone()
        if not row or row[0] is None:
            return 0
        try:
            return max(0, int(row[0]))
        except Exception:
            return 0

    def _save_rotation_index(self, rotation_key: str, next_video_index: int, last_item_path: str = "") -> None:
        if not rotation_key:
            return
        with self._rotation_lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO page_media_rotation (page_key, next_video_index, last_video_path, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(page_key) DO UPDATE SET
                        next_video_index=excluded.next_video_index,
                        last_video_path=excluded.last_video_path,
                        updated_at=excluded.updated_at
                    """,
                    (rotation_key, max(0, next_video_index), last_item_path, datetime.now().isoformat()),
                )
                conn.commit()

    def _load_video_rotation_index(self) -> int:
        return self._load_rotation_index("video_global")

    def _save_video_rotation_index(self, next_video_index: int, last_video_path: str = "") -> None:
        self._save_rotation_index("video_global", next_video_index, last_video_path)

    def _load_comment_rotation_index(self) -> int:
        return self._load_rotation_index("comment_global")

    def _save_comment_rotation_index(self, next_comment_index: int, last_comment_path: str = "") -> None:
        self._save_rotation_index("comment_global", next_comment_index, last_comment_path)

    def _schedule_save_config(self, delay_ms: int = 800) -> None:
        # Debounce saves so rapid UI updates don't cause repeated disk writes
        try:
            timer = getattr(self, "_save_config_timer", None)
            if timer is not None and timer.isActive():
                timer.stop()
        except Exception:
            pass
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._save_config)
        timer.start(delay_ms)
        self._save_config_timer = timer

    def _history_page_key(self, page_value: str) -> str:
        page_id = self._resolve_config_page_id(page_value)
        return page_id or str(page_value or "").strip()

    def _load_successful_video_paths_for_page(self, page_value: str) -> set:
        page_key = self._history_page_key(page_value)
        if not page_key:
            return set()
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT video_path
                    FROM page_video_history
                    WHERE page_key = ? AND status = ?
                    """,
                    (page_key, "success"),
                ).fetchall()
            return {str(row[0]) for row in rows if row and row[0]}
        except Exception as exc:
            self.logger.warning("Không thể đọc lịch sử video page=%s: %s", page_value, exc)
            return set()

    def _record_successful_page_video(
        self,
        page_name: str,
        video_path: str,
        post_id: str = "",
        permalink_url: str = "",
    ) -> None:
        if not page_name or not video_path:
            return
        page_key = self._history_page_key(page_name)
        if not page_key:
            return
        try:
            resolved_video_path = str(Path(video_path).expanduser().resolve())
        except Exception:
            resolved_video_path = str(video_path)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO page_video_history (
                        page_key,
                        page_name,
                        video_path,
                        video_name,
                        posted_at,
                        post_id,
                        permalink_url,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        page_key,
                        page_name,
                        resolved_video_path,
                        Path(resolved_video_path).name,
                        datetime.now().isoformat(),
                        post_id,
                        permalink_url,
                        "success",
                    ),
                )
                conn.commit()
        except Exception as exc:
            self.logger.warning("Không thể lưu lịch sử video page=%s video=%s: %s", page_name, video_path, exc)

    def _validate_token(self, token: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.base_url}/me",
                params={"access_token": token, "fields": "id,name"},
                timeout=25,
            )
            payload = response.json()
            if response.status_code in {400, 401, 403}:
                error_msg = payload.get("error", {}).get("message", "Token invalid")
                return {
                    "valid": False,
                    "error": error_msg,
                    "expires_at": None,
                    "account_id": None,
                    "account_name": None,
                }
            if payload.get("id"):
                return {
                    "valid": True,
                    "error": None,
                    "expires_at": None,
                    "account_id": payload.get("id"),
                    "account_name": payload.get("name"),
                }
            return {
                "valid": False,
                "error": "Không thể xác thực token",
                "expires_at": None,
                "account_id": None,
                "account_name": None,
            }
        except Exception as exc:
            return {
                "valid": False,
                "error": str(exc),
                "expires_at": None,
                "account_id": None,
                "account_name": None,
            }

    def _fetch_pages(self, token: str) -> List[Dict[str, Any]]:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        pages: List[Dict[str, Any]] = []
        fields = "id,name,access_token,tasks,category,location,about,phone,website,instagram_business_account"

        try:
            account_pages = _fetch_paginated_graph_data(
                session,
                f"{self.base_url}/me/accounts",
                {"access_token": token, "fields": fields},
                timeout=30,
            )
            for page in account_pages:
                pages.append({
                    "id": page.get("id"),
                    "name": page.get("name"),
                    "access_token": page.get("access_token", ""),
                    "tasks": page.get("tasks", []),
                    "page_type": "me/accounts",
                    "status": "Valid",
                })
        except Exception as exc:
            logger.warning("Không thể lấy pages từ me/accounts: %s", exc)

        try:
            businesses = _fetch_paginated_graph_data(
                session,
                f"{self.base_url}/me/businesses",
                {"access_token": token, "fields": "id,name"},
                timeout=30,
            )
            for business in businesses:
                business_id = business.get("id")
                business_name = business.get("name") or "Business Manager"
                if not business_id:
                    continue
                for endpoint in ("owned_pages", "client_pages"):
                    try:
                        business_pages = _fetch_paginated_graph_data(
                            session,
                            f"{self.base_url}/{business_id}/{endpoint}",
                            {"access_token": token, "fields": fields},
                            timeout=30,
                        )
                        for page in business_pages:
                            pages.append({
                                "id": page.get("id"),
                                "name": page.get("name"),
                                "access_token": page.get("access_token", ""),
                                "tasks": page.get("tasks", []),
                                "page_type": endpoint,
                                "business_name": business_name,
                                "business_id": business_id,
                                "status": "Valid",
                            })
                    except Exception as exc:
                        logger.warning("Không thể lấy pages từ Business %s (%s): %s", business_name, endpoint, exc)
        except Exception as exc:
            logger.warning("Không thể lấy danh sách Business Manager: %s", exc)

        unique_pages: Dict[str, Dict[str, Any]] = {}
        for page in pages:
            page_id = page.get("id")
            if page_id:
                unique_pages[page_id] = page

        return list(unique_pages.values())

    def _filter_page_table(self, search_text: str = "") -> None:
        query = (search_text or self.page_search_input.text() or "").strip().lower()
        self.current_page = 1
        if not query:
            self._populate_page_table(self.pages)
            return
        filtered_pages = []
        for page in self.pages:
            page_name = str(page.get("name", "") or "").lower()
            page_id = str(page.get("id", "") or "").lower()
            account_label = str(page.get("account_label", "") or "").lower()
            if query in page_name or query in page_id or query in account_label:
                filtered_pages.append(page)
        self._populate_page_table(filtered_pages)

    @QtCore.pyqtSlot(object)
    def _populate_page_table(self, pages: List[Dict[str, Any]]) -> None:
        self.page_table.blockSignals(True)
        self.page_table.setRowCount(0)
        self.page_table.setColumnCount(10)
        self.page_table.setHorizontalHeaderLabels(["STT", "Tên Page", "Chọn", "Trạng thái token", "Trạng thái đăng", "Tài khoản", "Page ID", "Open trang", "Page Access Token", "Follow/View"])
        invalid_tokens = [token for token, info in self.token_statuses.items() if not info.get("valid")]
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        paged_pages = pages[start:end]
        self.page_table.setRowCount(len(paged_pages))
        for row, page in enumerate(paged_pages):
            key = page.get("id", "")
            selected = self.page_selection_states.get(key, False)
            self.page_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(start + row + 1)))
            self.page_table.setItem(row, 1, QtWidgets.QTableWidgetItem(page.get("name", "")))
            check_item = QtWidgets.QTableWidgetItem()
            check_item.setFlags(check_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            check_item.setCheckState(QtCore.Qt.Checked if selected else QtCore.Qt.Unchecked)
            self.page_table.setItem(row, 2, check_item)

            status = page.get("status", "Valid")
            status_item = QtWidgets.QTableWidgetItem(status)
            if status in {"Lỗi", "Hết hạn"} or page.get("account_token") in invalid_tokens:
                status_item.setForeground(QtGui.QColor("#ef4444"))
                status_item.setToolTip("Token không hợp lệ hoặc hết hạn. Cần nhập lại access token mới.")
            elif status == "Cache":
                status_item.setForeground(QtGui.QColor("#3b82f6"))
            else:
                status_item.setForeground(QtGui.QColor("#22c55e"))
            self.page_table.setItem(row, 3, status_item)

            post_status = self.page_post_statuses.get(key, "Chưa đăng")
            post_status_item = QtWidgets.QTableWidgetItem(post_status)
            if "Success" in post_status:
                post_status_item.setForeground(QtGui.QColor("#22c55e"))
            elif "Failed" in post_status or "Stopped" in post_status:
                post_status_item.setForeground(QtGui.QColor("#ef4444"))
            elif "Waiting" in post_status:
                post_status_item.setForeground(QtGui.QColor("#f59e0b"))
            else:
                post_status_item.setForeground(QtGui.QColor("#cbd5e1"))
            self.page_table.setItem(row, 4, post_status_item)

            self.page_table.setItem(row, 5, QtWidgets.QTableWidgetItem(page.get("account_label", "")))
            page_id = str(page.get("id", "") or "")
            page_id_item = QtWidgets.QTableWidgetItem(page_id)
            page_id_item.setForeground(QtGui.QColor("#2563eb"))
            page_id_item.setToolTip("Nhấn để mở trang Facebook của page này")
            page_id_item.setFlags(page_id_item.flags() | QtCore.Qt.ItemIsEditable)
            self.page_table.setItem(row, 6, page_id_item)

            open_item = QtWidgets.QTableWidgetItem("Mở")
            open_item.setForeground(QtGui.QColor("#0284c7"))
            open_item.setToolTip("Nhấn để mở trang Facebook của page này")
            open_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.page_table.setItem(row, 7, open_item)
            self.page_table.setItem(row, 8, QtWidgets.QTableWidgetItem(page.get("access_token", "")))
            # placeholder for followers/views
            self.page_table.setItem(row, 9, QtWidgets.QTableWidgetItem("N/A"))
            header_item = QtWidgets.QTableWidgetItem(page.get("name", page.get("id", "")))
            header_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.page_table.setVerticalHeaderItem(row, header_item)
        self.page_table.blockSignals(False)
        self._update_page_pagination_label()
        self._update_page_selection_summary()
        invalid_count = sum(1 for info in self.token_statuses.values() if not info.get("valid"))
        if invalid_count:
            self.status_label.setText(f"Đã lấy {len(pages)} page. Có {invalid_count} token không hợp lệ hoặc hết hạn, cần cập nhật lại.")
        else:
            self.status_label.setText(f"Đã lấy {len(pages)} page từ cache/điều kiện mới")
        # Start background fetch for followers/views for the currently visible pages
        try:
            threading.Thread(target=self._fetch_page_infos_for_pages, args=(paged_pages,), daemon=True).start()
        except Exception:
            pass

    def _update_page_pagination_label(self) -> None:
        total_pages = max(1, math.ceil(len(self.pages) / self.page_size))
        self.page_page_label.setText(f"Trang {self.current_page} / {total_pages}")
        self.page_prev_btn.setEnabled(self.current_page > 1)
        self.page_next_btn.setEnabled(self.current_page < total_pages)

    def _update_page_selection_summary(self) -> None:
        total_pages = len(self.pages)
        selected_count = sum(1 for page in self.pages if self.page_selection_states.get(page.get("id", ""), False))
        self.page_selection_summary_label.setText(f"Đã chọn {selected_count}/{total_pages} page")

    def _fetch_page_infos_for_pages(self, pages: List[Dict[str, Any]]) -> None:
        if not pages:
            return
        max_workers = min(8, max(1, len(pages)))
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_page = {executor.submit(self._fetch_page_info, page): page for page in pages}
                for future in as_completed(future_to_page):
                    page = future_to_page[future]
                    try:
                        info = future.result()
                    except Exception:
                        info = {"followers": "N/A", "views": "N/A"}
                    page_id = str(page.get("id", "") or "")
                    if page_id:
                        self.page_info_updated.emit(page_id, info)
        except Exception:
            # keep silent on thread-pool level errors
            for page in pages:
                try:
                    info = self._fetch_page_info(page)
                    page_id = str(page.get("id", "") or "")
                    if page_id:
                        self.page_info_updated.emit(page_id, info)
                except Exception:
                    continue

    def _fetch_page_info(self, page: Dict[str, Any]) -> Dict[str, Any]:
        page_id = str(page.get("id", "") or "")
        access_token = page.get("access_token") or ""
        result = {"followers": "N/A", "views": "N/A"}
        if not page_id or not access_token:
            return result
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        # Try to read follower/fan count from the Page node
        try:
            resp = session.get(f"{self.base_url}/{page_id}", params={"access_token": access_token, "fields": "fan_count,followers_count"}, timeout=15)
            payload = resp.json()
            # prefer fan_count, then followers_count
            followers = payload.get("fan_count") or payload.get("followers_count") or payload.get("followers")
            if isinstance(followers, (int, float)):
                result["followers"] = str(int(followers))
            elif followers:
                result["followers"] = str(followers)
        except Exception:
            result["followers"] = "N/A"

        # Try to read simple insights (page views/impressions). If permissions missing, return N/A.
        try:
            resp = session.get(f"{self.base_url}/{page_id}/insights", params={"access_token": access_token, "metric": "page_views_total,page_impressions", "period": "day", "limit": 1}, timeout=15)
            payload = resp.json()
            data = payload.get("data") or []
            # pick first metric value available
            views_val = None
            for metric in data:
                values = metric.get("values") or []
                if values:
                    latest = values[-1]
                    if isinstance(latest, dict):
                        v = latest.get("value")
                    else:
                        v = latest
                    if v is not None:
                        views_val = v
                        break
            if isinstance(views_val, (int, float)):
                result["views"] = str(int(views_val))
            elif views_val:
                result["views"] = str(views_val)
        except Exception:
            result["views"] = "N/A"

        return result

    @QtCore.pyqtSlot(str, dict)
    def _on_page_info_updated(self, page_id: str, info: Dict[str, Any]) -> None:
        # find the visible row for this page_id and update the Follow/View column
        for row in range(self.page_table.rowCount()):
            item = self.page_table.item(row, 6)
            if item is None:
                continue
            if item.text() == page_id:
                display = f"{info.get('followers','N/A')} / {info.get('views','N/A')}"
                cell = self.page_table.item(row, 9)
                if cell is None:
                    cell = QtWidgets.QTableWidgetItem(display)
                    self.page_table.setItem(row, 9, cell)
                else:
                    cell.setText(display)
                # colorize if available
                if info.get("followers") not in (None, "N/A"):
                    cell.setForeground(QtGui.QColor("#22c55e"))
                else:
                    cell.setForeground(QtGui.QColor("#64748b"))
                return

    def _create_overlay(self) -> None:
        if hasattr(self, "_overlay") and self._overlay is not None:
            return
        parent = self.centralWidget() or self
        overlay = QtWidgets.QWidget(parent)
        overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        overlay.setStyleSheet("background: rgba(0,0,0,0.25);")
        overlay.setGeometry(parent.rect())
        overlay_label = QtWidgets.QLabel("", overlay)
        overlay_label.setStyleSheet("color: white; font-weight: 800; background: transparent;")
        overlay_label.setAlignment(QtCore.Qt.AlignCenter)
        overlay_label.setWordWrap(True)
        overlay_label.setGeometry(0, 0, parent.width(), parent.height())
        overlay.hide()
        self._overlay = overlay
        self._overlay_label = overlay_label

    def _show_overlay(self, text: str = "Đang xử lý...") -> None:
        try:
            self._create_overlay()
            self._overlay_label.setText(text)
            self._overlay.setGeometry((self.centralWidget() or self).rect())
            self._overlay.show()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        except Exception:
            pass

    def _hide_overlay(self) -> None:
        try:
            if hasattr(self, "_overlay") and self._overlay is not None:
                self._overlay.hide()
            QtWidgets.QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def _on_config_row_append(self, row_data: Dict[str, Any]) -> None:
        # Insert one row on the UI thread using existing helpers
        config_table = self._safe_get_attr("config_table")
        if config_table is None:
            return
        row_idx = config_table.rowCount()
        config_table.insertRow(row_idx)
        page_name = row_data.get("page_name", "")
        self._set_table_value(row_idx, 0, page_name)
        self._set_table_value(row_idx, 1, "")
        self._set_table_value(row_idx, 2, "")
        self._set_table_value(row_idx, 3, "")
        self._set_table_value(row_idx, 4, "")
        self._set_table_value(row_idx, 5, "")
        try:
            self._set_config_row_post_type(row_idx, self._normalize_post_type(row_data.get("post_type", "video")))
        except Exception:
            self._set_config_row_post_type(row_idx, "video")
        self._set_table_value(row_idx, CONFIG_SCHEDULE_COL, "")
        self._set_table_value(row_idx, CONFIG_STATUS_COL, "Chờ đăng")
        self._set_config_link_cell(row_idx, "")
        config_table.setRowHeight(row_idx, 42)

        # debounce save: schedule save after short delay
        try:
            self._schedule_save_config()
        except Exception:
            pass

    def _on_config_populate_done(self) -> None:
        try:
            # apply folder mapping once at end
            self._apply_folder_mapping_to_config()
        except Exception:
            pass
        try:
            self._hide_overlay()
        except Exception:
            pass

    def page_prev(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self._populate_page_table(self.pages)

    def page_next(self) -> None:
        total_pages = max(1, math.ceil(len(self.pages) / self.page_size))
        if self.current_page < total_pages:
            self.current_page += 1
            self._populate_page_table(self.pages)

    def _on_page_size_changed(self, value: str) -> None:
        self.page_size = int(value)
        self.current_page = 1
        self._populate_page_table(self.pages)

    def _on_page_table_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 2:
            return
        row = item.row()
        page_id_item = self.page_table.item(row, 6)
        if not page_id_item:
            return
        page_id = page_id_item.text()
        self.page_selection_states[page_id] = item.checkState() == QtCore.Qt.Checked
        self._update_page_selection_summary()

    def _on_page_table_cell_clicked(self, row: int, col: int) -> None:
        if col != 2:
            return
        item = self.page_table.item(row, 2)
        if item is None:
            return
        new_state = QtCore.Qt.Checked if item.checkState() != QtCore.Qt.Checked else QtCore.Qt.Unchecked
        item.setCheckState(new_state)
        page_id_item = self.page_table.item(row, 6)
        if page_id_item:
            self.page_selection_states[page_id_item.text()] = new_state == QtCore.Qt.Checked
        self._update_page_selection_summary()

    def scan_select_pages(self) -> None:
        query = self.page_scan_input.text().strip().lower()
        if not query:
            return
        for page in self.pages:
            page_id = page.get("id", "")
            page_name = str(page.get("name", "")).lower()
            if query in page_id.lower() or query in page_name:
                self.page_selection_states[page_id] = True
        self._populate_page_table(self.pages)

    @QtCore.pyqtSlot(str)
    def _show_error(self, message: str) -> None:
        self.status_label.setText("Lỗi")
        self._show_message_box("Lỗi", message, QtWidgets.QMessageBox.Critical)

    def select_all_pages(self) -> None:
        for page in self.pages:
            page_id = page.get("id", "")
            if page_id:
                self.page_selection_states[page_id] = True
        self._populate_page_table(self.pages)

    def clear_selection(self) -> None:
        for page in self.pages:
            page_id = page.get("id", "")
            if page_id:
                self.page_selection_states[page_id] = False
        self._populate_page_table(self.pages)

    def select_image_files(self) -> None:
        paths = self._pick_files("Chọn nhiều ảnh", "Images (*.png *.jpg *.jpeg *.gif *.webp)", multiple=True)
        if paths:
            self.image_paths_input.setText(";".join(paths))
            self.video_paths_input.clear()
            self._save_config()

    def select_video_files(self) -> None:
        paths = self._pick_files("Chọn nhiều video", "Videos (*.mp4 *.mov *.avi *.mkv)", multiple=True)
        if paths:
            self.video_paths_input.setText(";".join(paths))
            self.image_paths_input.clear()
            self._save_config()

    def _selected_pages(self, use_all: bool = False) -> List[Dict[str, Any]]:
        selected = []
        for page in self.pages:
            page_id = page.get("id", "")
            if use_all or self.page_selection_states.get(page_id, False):
                selected.append({
                    "account": page.get("account_label", ""),
                    "id": page.get("id", ""),
                    "name": page.get("name", ""),
                    "access_token": page.get("access_token", ""),
                })
        return selected

    def _open_page_url(self, page_id: str) -> None:
        page_id = str(page_id or "").strip()
        if not page_id:
            self._show_message_box("Thiếu page ID", "Page này chưa có ID để mở", QtWidgets.QMessageBox.Warning)
            return
        url = f"https://www.facebook.com/{page_id}"
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        except Exception as exc:
            self._show_message_box("Lỗi mở trang", f"Không thể mở trang Facebook: {exc}", QtWidgets.QMessageBox.Critical)

    def _on_page_table_item_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() not in {6, 7}:
            return
        page_id_item = self.page_table.item(item.row(), 6)
        if page_id_item is None:
            return
        self._open_page_url(page_id_item.text())

    def _on_config_tab_changed(self, index: int) -> None:
        return

    def add_config_row(self) -> None:
        row = self.config_table.rowCount()
        self.config_table.insertRow(row)
        self.config_table.setItem(row, 0, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, 1, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, 2, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, 3, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, 4, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, 5, QtWidgets.QTableWidgetItem(""))
        self._set_config_row_post_type(row, self._normalize_post_type(self.post_type_combo.currentData() or self.post_type_combo.currentText()))
        self.config_table.setItem(row, CONFIG_SCHEDULE_COL, QtWidgets.QTableWidgetItem(""))
        self.config_table.setItem(row, CONFIG_STATUS_COL, QtWidgets.QTableWidgetItem("Chờ đăng"))
        self._set_config_link_cell(row, "")
        self.config_table.setRowHeight(row, 42)
        self._apply_folder_mapping_to_config()
        self._save_config()

    @staticmethod
    def _format_post_type_label(post_type: str) -> str:
        return "Feed" if post_type == "feed" else "Reel / video"

    def _set_config_row_post_type(self, row_idx: int, post_type: Optional[str]) -> None:
        normalized = self._normalize_post_type(post_type)
        item = self.config_table.item(row_idx, CONFIG_POST_TYPE_COL)
        if item is None:
            item = QtWidgets.QTableWidgetItem(self._format_post_type_label(normalized))
            self.config_table.setItem(row_idx, CONFIG_POST_TYPE_COL, item)
        else:
            item.setText(self._format_post_type_label(normalized))
        item.setToolTip("Click để đổi kiểu đăng cho dòng này")
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        if normalized == "feed":
            item.setForeground(QtGui.QColor("#2563eb"))
        else:
            item.setForeground(QtGui.QColor("#0f766e"))

    def _get_config_row_post_type(self, row_idx: int) -> str:
        item = self.config_table.item(row_idx, CONFIG_POST_TYPE_COL)
        if item is not None:
            return self._normalize_post_type(item.text())
        return "video"

    def _toggle_config_row_post_type(self, row_idx: int) -> None:
        current = self._get_config_row_post_type(row_idx)
        next_type = "feed" if current != "feed" else "video"
        self._set_config_row_post_type(row_idx, next_type)
        self._save_config()

    def _on_config_post_type_cell_clicked(self, row_idx: int, col_idx: int) -> None:
        if col_idx == CONFIG_LINK_COL:
            item = self.config_table.item(row_idx, CONFIG_LINK_COL)
            link = item.text().strip() if item is not None else ""
            if link:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))
            return
        if col_idx != CONFIG_POST_TYPE_COL:
            return
        self._toggle_config_row_post_type(row_idx)

    def _on_folder_setting_changed(self) -> None:
        self._save_config()
        self._apply_folder_mapping_to_config()

    def _set_post_type_for_all_rows(self, post_type: str) -> None:
        for row_idx in range(self.config_table.rowCount()):
            self._set_config_row_post_type(row_idx, post_type)
        self._save_config()

    def _pick_folder(self, title: str) -> Optional[str]:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, title)
        return folder or None

    def _select_video_folder(self) -> None:
        folder = self._pick_folder("Chọn thư mục video")
        if folder:
            self.video_folder_input.setText(folder)
            self._save_config()
            self._apply_folder_mapping_to_config()

    def _select_comment_image_folder(self) -> None:
        folder = self._pick_folder("Chọn thư mục ảnh comment")
        if folder:
            self.comment_image_folder_input.setText(folder)
            self._save_config()
            self._apply_folder_mapping_to_config()

    def _collect_media_files(self, folder: Optional[str], extensions: Optional[set] = None) -> List[str]:
        if not folder:
            return []
        path = Path(folder).expanduser()
        if not path.exists() or not path.is_dir():
            return []
        files = []
        for candidate in path.iterdir():
            if not candidate.is_file():
                continue
            if extensions and candidate.suffix.lower() not in extensions:
                continue
            files.append(str(candidate.resolve()))
        files.sort(key=lambda value: Path(value).name.lower())
        return files

    def _apply_folder_mapping_to_config(self) -> None:
        video_folder = self.video_folder_input.text().strip()
        comment_image_folder = self.comment_image_folder_input.text().strip()
        if not video_folder and not comment_image_folder:
            return

        video_files = self._collect_media_files(video_folder, VIDEO_EXTENSIONS)
        comment_image_files = self._collect_media_files(comment_image_folder, IMAGE_EXTENSIONS)
        start_index = self._load_video_rotation_index() if video_files else 0
        comment_start_index = self._load_comment_rotation_index() if (self.use_comment_images_checkbox.isChecked() and comment_image_files) else 0
        assigned_videos_by_page: Dict[str, set] = {}
        video_history_cache: Dict[str, set] = {}
        for row_idx in range(self.config_table.rowCount()):
            if video_files:
                page_name = self.config_table.item(row_idx, 0).text().strip() if self.config_table.item(row_idx, 0) else ""
                page_key = self._history_page_key(page_name)
                if page_key not in video_history_cache:
                    video_history_cache[page_key] = self._load_successful_video_paths_for_page(page_name)
                posted_video_paths = video_history_cache[page_key]
                already_assigned = assigned_videos_by_page.setdefault(page_key, set())
                video_path = ""
                for offset in range(len(video_files)):
                    candidate = video_files[(start_index + row_idx + offset) % len(video_files)]
                    if candidate in posted_video_paths or candidate in already_assigned:
                        continue
                    video_path = candidate
                    break

                if video_path:
                    already_assigned.add(video_path)
                    self._set_table_value(row_idx, 3, video_path)
                    status_item = self.config_table.item(row_idx, CONFIG_STATUS_COL)
                    if status_item and status_item.text().strip() == "Hết video mới":
                        self._set_table_value(row_idx, CONFIG_STATUS_COL, "Chờ đăng")
                    current_title = self.config_table.item(row_idx, 1).text().strip() if self.config_table.item(row_idx, 1) else ""
                    if not current_title:
                        self._set_table_value(row_idx, 1, self._derive_config_title(video_path, "", page_name))
                else:
                    self._set_table_value(row_idx, 3, "")
                    self._set_table_value(row_idx, CONFIG_STATUS_COL, "Hết video mới")
            elif self.skip_missing_videos_checkbox.isChecked():
                self._set_table_value(row_idx, 3, "")

            if self.use_comment_images_checkbox.isChecked() and comment_image_files:
                comment_image_path = comment_image_files[(comment_start_index + row_idx) % len(comment_image_files)]
                self._set_table_value(row_idx, CONFIG_COMMENT_IMAGE_COL, comment_image_path)
            elif not self.use_comment_images_checkbox.isChecked():
                self._set_table_value(row_idx, CONFIG_COMMENT_IMAGE_COL, "")

    def _set_table_value(self, row_idx: int, col_idx: int, value: str) -> None:
        item = self.config_table.item(row_idx, col_idx)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.config_table.setItem(row_idx, col_idx, item)
        item.setText(str(value))

    def _set_config_link_cell(self, row_idx: int, link: str) -> None:
        item = self.config_table.item(row_idx, CONFIG_LINK_COL)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.config_table.setItem(row_idx, CONFIG_LINK_COL, item)
        item.setText(str(link or ""))
        item.setToolTip(str(link or ""))
        item.setForeground(QtGui.QColor("#2563eb") if link else QtGui.QColor("#64748b"))
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)

    @QtCore.pyqtSlot(int, str)
    def _update_config_link_row(self, row_idx: int, link: str) -> None:
        if row_idx < 0 or row_idx >= self.config_table.rowCount():
            return
        self._set_config_link_cell(row_idx, link)
        self._save_config()

    def _normalize_content_value(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _apply_quick_content_to_config(self) -> None:
        post_content = self.quick_post_content_input.toPlainText().strip()
        comment_content = self.quick_comment_content_input.toPlainText().strip()
        if not post_content and not comment_content:
            return
        for row_idx in range(self.config_table.rowCount()):
            if post_content:
                self._set_table_value(row_idx, 1, post_content)
                self._set_table_value(row_idx, 2, post_content)
            if comment_content:
                self._set_table_value(row_idx, 4, comment_content)
            else:
                self._set_table_value(row_idx, 4, post_content)
        self._save_config()

    def _resolve_config_page_name(self, page_value: str) -> str:
        value = (page_value or "").strip()
        if not value:
            return ""
        for page in self.pages:
            if str(page.get("id", "")).strip() == value:
                return str(page.get("name", value) or value)
            if str(page.get("name", "")).strip() == value:
                return str(page.get("name", value) or value)
        return value

    def _resolve_config_page_id(self, page_value: str) -> str:
        value = (page_value or "").strip()
        if not value:
            return ""
        for page in self.pages:
            if str(page.get("id", "")).strip() == value:
                return str(page.get("id", "")).strip()
            if str(page.get("name", "")).strip() == value:
                return str(page.get("id", "")).strip()
        return ""

    def _on_config_table_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() in set(range(len(CONFIG_HEADERS))) - {CONFIG_POST_TYPE_COL, CONFIG_LINK_COL}:
            self._save_config()
        if item.column() not in {1, 3}:
            return
        row = item.row()
        page_name = self.config_table.item(row, 0).text().strip() if self.config_table.item(row, 0) else ""
        title_value = self.config_table.item(row, 1).text().strip() if self.config_table.item(row, 1) else ""
        video_value = self.config_table.item(row, 3).text().strip() if self.config_table.item(row, 3) else ""
        if item.column() == 3 and title_value:
            return
        if item.column() == 1 and title_value:
            return
        if not video_value:
            return
        auto_title = self._derive_config_title(video_value, "", page_name)
        current_title = self.config_table.item(row, 1).text().strip() if self.config_table.item(row, 1) else ""
        if current_title and current_title != auto_title:
            return
        self._set_table_value(row, 1, auto_title)

    def _prepare_config_from_selected_pages(self, selected_pages: List[Dict[str, Any]]) -> None:
        config_table = self._safe_get_attr("config_table")
        if config_table is None:
            return
        post_type_combo = self._safe_get_attr("post_type_combo")
        if post_type_combo is None:
            selected_post_type = "video"
        else:
            selected_post_type = self._normalize_post_type(post_type_combo.currentData() or post_type_combo.currentText())
        # Switch to Config tab first so UI is responsive, then populate rows in background
        tabs = self._safe_get_attr("tabs")
        config_tab = self._safe_get_attr("config_tab")
        if tabs is not None and config_tab is not None:
            tabs.setCurrentWidget(config_tab)
            QtWidgets.QApplication.processEvents()

        # clear table immediately on UI thread
        config_table.setRowCount(0)

        # show overlay / busy indicator
        try:
            self._show_overlay("Chuẩn bị dữ liệu...")
        except Exception:
            pass

        # spawn background worker to prepare rows and emit signals in chunks
        def worker(pages_list: List[Dict[str, Any]], post_type: str) -> None:
            chunk_size = 20
            items = []
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(pages_list)))) as ex:
                futures = {ex.submit(lambda p: {"page_name": str((p.get("name") or p.get("id") or "")).strip(), "post_type": post_type} , p): p for p in pages_list}
                for fut in as_completed(futures):
                    try:
                        data = fut.result()
                    except Exception:
                        data = {"page_name": "", "post_type": post_type}
                    items.append(data)
                    if len(items) >= chunk_size:
                        # emit chunk
                        for it in items:
                            self.config_row_append.emit(it)
                        items = []
                        time.sleep(0.02)
            # emit remaining
            for it in items:
                self.config_row_append.emit(it)
            # notify done
            self.config_populate_done.emit()

        threading.Thread(target=worker, args=(selected_pages, selected_post_type), daemon=True).start()

    def post_selected_pages(self) -> None:
        selected = self._selected_pages(use_all=False)
        if not selected:
            self._show_message_box("Chưa chọn page", "Vui lòng chọn ít nhất 1 page", QtWidgets.QMessageBox.Warning)
            return
        QtCore.QTimer.singleShot(50, lambda: self._prepare_config_from_selected_pages(selected))

    def post_all_pages(self) -> None:
        selected = self._selected_pages(use_all=True)
        if not selected:
            self._show_message_box("Chưa chọn page", "Vui lòng chọn ít nhất 1 page", QtWidgets.QMessageBox.Warning)
            return
        QtCore.QTimer.singleShot(50, lambda: self._prepare_config_from_selected_pages(selected))

    def _start_posting_from_config(self) -> None:
        if hasattr(self, "config_repeat_timer"):
            self.config_repeat_timer.stop()
        if self.video_folder_input.text().strip() or self.comment_image_folder_input.text().strip():
            self._apply_folder_mapping_to_config()
        self._save_config()
        bypass_schedule = self._bypass_schedule_wait_once
        scheduled_base_time = None if bypass_schedule else self._selected_schedule_base_time()
        self._bypass_schedule_wait_once = False
        rows = []
        for row in range(self.config_table.rowCount()):
            page_name = self.config_table.item(row, 0).text().strip() if self.config_table.item(row, 0) else ""
            title = self.config_table.item(row, 1).text().strip() if self.config_table.item(row, 1) else ""
            description = self.config_table.item(row, 2).text().strip() if self.config_table.item(row, 2) else ""
            video_path = self.config_table.item(row, 3).text().strip() if self.config_table.item(row, 3) else ""
            comment_text = self.config_table.item(row, 4).text().strip() if self.config_table.item(row, 4) else ""
            comment_image_paths = self.config_table.item(row, CONFIG_COMMENT_IMAGE_COL).text().strip() if self.config_table.item(row, CONFIG_COMMENT_IMAGE_COL) else ""
            schedule_time = self.config_table.item(row, CONFIG_SCHEDULE_COL).text().strip() if self.config_table.item(row, CONFIG_SCHEDULE_COL) else ""
            if bypass_schedule:
                schedule_time = ""
            elif not schedule_time and scheduled_base_time is not None:
                schedule_time = scheduled_base_time.isoformat()
            if not page_name:
                continue

            if not video_path:
                if self.skip_missing_videos_checkbox.isChecked():
                    continue
                self._show_message_box(
                    "Thiếu video",
                    f"Page {page_name} chưa có video được gán. Hãy chọn lại thư mục hoặc bỏ tích 'Bỏ qua page khi video không đủ'.",
                    QtWidgets.QMessageBox.Warning,
                )
                return

            default_title = self._derive_config_title(video_path, title, page_name)
            rows.append({
                "row_index": row,
                "page_name": page_name,
                "title": default_title,
                "description": description,
                "video_path": video_path,
                "schedule_time": schedule_time,
                "comment_text": comment_text,
                "comment_image_paths": comment_image_paths,
                "post_type": self._get_config_row_post_type(row),
            })

        if not rows:
            self._show_message_box(
                "Thiếu dữ liệu",
                "Không có page nào đủ điều kiện để đăng. Hãy chọn thư mục video khác hoặc tắt tùy chọn bỏ qua page.",
                QtWidgets.QMessageBox.Warning,
            )
            return

        self.logger.info("Bắt đầu đăng từ cấu hình với %s dòng", len(rows))
        self.stop_requested = False
        self.post_selected_btn.setEnabled(False)
        self.post_all_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.post_config_btn.setEnabled(False)
        self.post_config_btn.setText("Đang đăng...")
        self.add_row_btn.setEnabled(False)
        self.status_label.setText("Đang xử lý hàng đợi đăng video từ cấu hình...")
        self.post_thread = threading.Thread(target=self._run_config_post_queue, args=(rows,), daemon=True)
        self.post_thread.start()

    def _set_config_status(self, page_name: str, status: str) -> None:
        self.config_status_changed.emit(page_name, status)

    @QtCore.pyqtSlot(str, str)
    def _update_config_status_row(self, page_name: str, status: str) -> None:
        for row in range(self.config_table.rowCount()):
            item = self.config_table.item(row, 0)
            if item and item.text().strip() == page_name:
                status_item = self.config_table.item(row, CONFIG_STATUS_COL)
                if status_item is None:
                    status_item = QtWidgets.QTableWidgetItem(status)
                    self.config_table.setItem(row, CONFIG_STATUS_COL, status_item)
                else:
                    status_item.setText(status)
                return

    def _log_successful_post(self, page_name: str, content: str, post_type: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO recent_posts (page_name, content, post_type, posted_at, status) VALUES (?, ?, ?, ?, ?)",
                (page_name, content[:400], post_type, timestamp, "Thành công"),
            )
            conn.commit()
        self.recent_posts_refresh_requested.emit()

    def _clear_recent_posts_history(self) -> None:
        reply = self._show_message_box(
            "Xóa lịch sử",
            "Bạn có chắc muốn xóa toàn bộ lịch sử đăng gần đây?",
            QtWidgets.QMessageBox.Question,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM recent_posts")
            conn.commit()
        self.recent_page = 1
        self._load_recent_posts_page(1)
        self._show_message_box("Đã xóa", "Đã xóa toàn bộ lịch sử đăng gần đây", QtWidgets.QMessageBox.Information)

    def _load_recent_posts_page(self, page: int) -> None:
        if page < 1:
            page = 1
        with sqlite3.connect(self.db_path) as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM recent_posts").fetchone()[0]
            self.recent_total_pages = max(1, math.ceil(total_count / self.recent_page_size))
            if page > self.recent_total_pages:
                page = self.recent_total_pages
            self.recent_page = page
            offset = (page - 1) * self.recent_page_size
            rows = conn.execute(
                "SELECT page_name, content, post_type, posted_at FROM recent_posts ORDER BY id DESC LIMIT ? OFFSET ?",
                (self.recent_page_size, offset),
            ).fetchall()

        self.recent_posts_table.setRowCount(0)
        for row_idx, row in enumerate(rows):
            self.recent_posts_table.insertRow(row_idx)
            self.recent_posts_table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(str(row[0] or "")))
            self.recent_posts_table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(row[1] or "")))
            self.recent_posts_table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(str(row[2] or "")))
            self.recent_posts_table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(str(row[3] or "")))

        self.recent_page_label.setText(f"Trang {self.recent_page}/{self.recent_total_pages}")
        self.recent_prev_btn.setEnabled(self.recent_page > 1)
        self.recent_next_btn.setEnabled(self.recent_page < self.recent_total_pages)

    def _refresh_recent_posts(self) -> None:
        self._load_recent_posts_page(self.recent_page)

    @QtCore.pyqtSlot()
    def _refresh_accounts_view(self) -> None:
        if not hasattr(self, "accounts_table"):
            return
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT token_prefix, account_name, account_id, is_valid, expires_at, page_count, last_checked, error
                FROM accounts
                ORDER BY last_checked DESC, account_name COLLATE NOCASE ASC
                """
            ).fetchall()

        self.accounts_table.setRowCount(0)
        for row_idx, row in enumerate(rows):
            self.accounts_table.insertRow(row_idx)
            token_prefix, account_name, account_id, is_valid, expires_at, page_count, last_checked, error = row
            values = [
                token_prefix or "",
                account_name or "",
                account_id or "",
                "Valid" if is_valid else "Invalid",
                expires_at or "",
                str(page_count or 0),
                last_checked or "",
                error or "",
            ]
            for col_idx, value in enumerate(values):
                self.accounts_table.setItem(row_idx, col_idx, QtWidgets.QTableWidgetItem(str(value)))

            status_item = self.accounts_table.item(row_idx, 3)
            if status_item is not None:
                status_item.setForeground(QtGui.QColor("#22c55e" if is_valid else "#ef4444"))

        self.accounts_summary_label.setText(f"{len(rows)} account")

    def _run_config_post_queue(self, rows: List[Dict[str, str]]) -> None:
        success_count = 0
        fail_count = 0
        use_concurrency = self.concurrency_enabled and len(rows) > 1
        max_workers = self.concurrency_threads if use_concurrency else 1
        video_files = self._collect_media_files(self.video_folder_input.text().strip(), VIDEO_EXTENSIONS)
        comment_files = self._collect_media_files(self.comment_image_folder_input.text().strip(), IMAGE_EXTENSIONS) if self.use_comment_images_checkbox.isChecked() else []

        def worker(entry: Dict[str, str]) -> None:
            nonlocal success_count, fail_count
            if self.stop_requested:
                return
            page_name = entry["page_name"]
            title = entry["title"]
            description = entry.get("description", "")
            video_path = entry["video_path"]
            schedule_time = entry.get("schedule_time")
            comment_text = entry.get("comment_text", "")
            comment_image_paths = self._split_comment_image_paths(entry.get("comment_image_paths", ""))
            has_comment = bool(comment_text or comment_image_paths)
            self._append_log(
                f"[Config] Bắt đầu xử lý page={page_name} title={title or 'n/a'} comment={bool(comment_text)} images={len(comment_image_paths)} post_type={entry.get('post_type', 'video')}",
                "info",
            )
            proxy_info = self._fetch_proxy()
            proxy_config = self._build_proxy_config(proxy_info)
            self.status_changed.emit(page_name, "Đang đăng", "Đang gửi yêu cầu tới Facebook")
            self._set_config_status(page_name, "Đang đăng")
            try:
                post_result = self._post_video_from_config(page_name, title, description, video_path, schedule_time, comment_text, comment_image_paths, proxy_config, post_type=entry.get("post_type", "video"))
                success_count += 1
                detail = "Đăng thành công"
                if has_comment:
                    detail = f"Đăng thành công (comment: {'thành công' if post_result.get('comment_ok') else 'bị chặn bởi quyền App'})"
                self.status_changed.emit(page_name, "Thành công", detail)
                self._set_config_status(page_name, "Thành công")
                permalink = str(post_result.get("permalink_url") or post_result.get("link") or "")
                self.config_link_changed.emit(int(entry.get("row_index", -1)), permalink)
                self._record_successful_page_video(
                    page_name,
                    video_path,
                    str(post_result.get("post_id") or ""),
                    permalink,
                )
                self._log_successful_post(page_name, f"{title}\n\n{description}".strip(), "video")
                self._append_log(
                    f"[Config] Thành công page={page_name} detail={detail} post_id={post_result.get('post_id') or 'n/a'} permalink={post_result.get('permalink_url') or 'n/a'} privacy={post_result.get('privacy') or 'n/a'} published={post_result.get('published')} is_published={post_result.get('is_published')}",
                    "info",
                )
            except Exception as exc:
                fail_count += 1
                self.logger.exception("Lỗi khi xử lý page=%s: %s", page_name, exc)
                self.status_changed.emit(page_name, "Thất bại", str(exc))
                self._set_config_status(page_name, "Thất bại")
                self._append_log(f"[Config] Thất bại page={page_name}: {exc}", "error")

        batches = _chunked_batches(rows, max_workers) if use_concurrency else [rows]
        for batch_index, batch in enumerate(batches):
            if batch_index > 0 and self.concurrency_delay > 0:
                time.sleep(self.concurrency_delay)
            if use_concurrency:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(worker, entry) for entry in batch]
                    for future in as_completed(futures):
                        future.result()
            else:
                for entry in batch:
                    worker(entry)
        self.logger.info("Hoàn tất hàng đợi đăng: success=%s fail=%s", success_count, fail_count)
        if video_files and rows:
            current_index = self._load_video_rotation_index()
            next_index = (current_index + len(rows)) % len(video_files)
            last_video_path = video_files[(current_index + len(rows) - 1) % len(video_files)]
            self._save_video_rotation_index(next_index, last_video_path)
        if comment_files and rows:
            current_index = self._load_comment_rotation_index()
            next_index = (current_index + len(rows)) % len(comment_files)
            last_comment_path = comment_files[(current_index + len(rows) - 1) % len(comment_files)]
            self._save_comment_rotation_index(next_index, last_comment_path)
        self.posting_completed.emit(success_count, fail_count)

    def _split_comment_image_paths(self, raw_paths: str) -> List[str]:
        if not raw_paths:
            return []
        parts = re.split(r"[;\r\n]+", raw_paths)
        return [part.strip().strip('"') for part in parts if part.strip()]

    def _derive_post_title(self, video_path: str, title: str, fallback_name: str) -> str:
        if title and title.strip():
            return title.strip()
        return self._derive_config_title(video_path, title, fallback_name)

    def _derive_config_title(self, video_path: str, title: str, fallback_name: str) -> str:
        if title and title.strip():
            return title.strip()
        if video_path:
            candidate = Path(video_path).stem
            if candidate:
                return candidate
        return fallback_name or "Bài đăng"

    def _post_video_from_config(
        self,
        page_name: str,
        title: str,
        description: str,
        video_path: str,
        schedule_time: Optional[str],
        comment_text: str = "",
        comment_image_paths: Optional[List[str]] = None,
        proxy_config: Optional[Dict[str, str]] = None,
        post_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        matching_page = None
        for page in self.pages:
            if page.get("name") == page_name or page.get("id") == page_name:
                matching_page = page
                break
        if not matching_page:
            raise ValueError(f"Không tìm thấy page: {page_name}")
        if schedule_time:
            try:
                parsed = datetime.fromisoformat(schedule_time)
                if parsed > datetime.now():
                    self._set_config_status(page_name, "Đang chờ lịch")
                    self.logger.info("Page %s đang chờ lịch đăng tới %s", page_name, schedule_time)
                    time.sleep(max(0, (parsed - datetime.now()).total_seconds()))
            except Exception as exc:
                self.logger.warning("Không parse được schedule_time %s cho page=%s: %s", schedule_time, page_name, exc)
        self._append_log(f"[Post] Bắt đầu đăng page={page_name} path={video_path} post_type={post_type or 'video'}", "info")
        response_data = self._post_media(matching_page, f"{title}\n\n{description}".strip(), video_path, media_type="video", title=title, proxy_config=proxy_config, post_type=post_type)
        post_id = self._extract_post_id(response_data)
        self.logger.info("Kết quả đăng page=%s post_id=%s response=%s", page_name, post_id, response_data)
        result: Dict[str, Any] = {"post_id": post_id, "comment_ok": False}
        if isinstance(response_data, dict):
            result.update({
                "permalink_url": response_data.get("permalink_url"),
                "link": response_data.get("link"),
                "upload_id": response_data.get("upload_id"),
                "public_visibility": response_data.get("public_visibility"),
                "public_visibility_reason": response_data.get("public_visibility_reason"),
                "privacy": response_data.get("privacy"),
                "published": response_data.get("published"),
                "is_published": response_data.get("is_published"),
            })
        self._append_log(
            f"[Post] Kết quả page={page_name} post_id={post_id or 'n/a'} permalink={result.get('permalink_url') or 'n/a'} privacy={result.get('privacy') or 'n/a'} published={result.get('published')} is_published={result.get('is_published')}",
            "info",
        )
        has_comment_content = bool(comment_text or (comment_image_paths or []))
        if has_comment_content:
            if not post_id:
                self.logger.warning("Không lấy được post_id từ phản hồi đăng page=%s, bỏ qua comment", page_name)
                return result
            try:
                self._post_comment_with_retries(matching_page, page_name, post_id, comment_text, comment_image_paths or [], proxy_config)
                result["comment_ok"] = True
                self._append_log(f"[Post] Comment thành công page={page_name} post_id={post_id}", "info")
            except Exception as exc:
                self.logger.warning("Comment bị chặn hoặc lỗi cho page=%s post_id=%s: %s", page_name, post_id, exc)
                result["comment_error"] = str(exc)
        return result

    def _post_comment_with_retries(
        self,
        page: Dict[str, Any],
        page_name: str,
        post_id: str,
        comment_text: str,
        comment_image_paths: Optional[List[str]] = None,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> None:
        image_paths = comment_image_paths or []
        for attempt in range(1, 4):
            try:
                self.logger.info(
                    "Gửi comment lần %s/3 cho page=%s post_id=%s image_count=%s",
                    attempt,
                    page_name,
                    post_id,
                    len(image_paths),
                )
                self._post_comment_on_post(page, post_id, comment_text, image_paths, proxy_config)
                self.logger.info("Comment thành công page=%s post_id=%s", page_name, post_id)
                return
            except Exception as exc:
                self.logger.warning("Comment lần %s/3 thất bại page=%s post_id=%s: %s", attempt, page_name, post_id, exc)
                if attempt < 3:
                    time.sleep(3)
        raise RuntimeError(f"Comment thất bại sau 3 lần thử cho post_id={post_id}")

    def _start_posting(self, use_all: bool) -> None:
        selected = self._selected_pages(use_all=use_all)
        if not selected:
            self._show_message_box("Chưa chọn page", "Vui lòng chọn ít nhất 1 page", QtWidgets.QMessageBox.Warning)
            return

        message = self.message_input.toPlainText().strip()
        if not message:
            self._show_message_box("Thiếu nội dung", "Vui lòng nhập nội dung bài viết", QtWidgets.QMessageBox.Warning)
            return

        post_type = self._normalize_post_type(self.post_type_combo.currentData() or self.post_type_combo.currentText())

        image_paths = [p.strip() for p in self.image_paths_input.text().split(";") if p.strip()]
        video_paths = [p.strip() for p in self.video_paths_input.text().split(";") if p.strip()]
        if image_paths and video_paths:
            self._show_message_box("Lỗi", "Chỉ nên chọn ảnh hoặc video, không nên cả hai", QtWidgets.QMessageBox.Warning)
            return

        self.stop_requested = False
        for page in selected:
            self.page_post_statuses[page["id"]] = "Chờ xử lý"

        scheduled_time = self._selected_schedule_base_time()

        self._save_config()

        self.post_selected_btn.setEnabled(False)
        self.post_all_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("Đang xử lý hàng đợi đăng bài...")

        for page in selected:
            self.page_post_statuses[page["id"]] = "Chờ xử lý"
        self._populate_page_table(self.pages)

        self.post_thread = threading.Thread(
            target=self._run_post_queue,
            args=(selected, message, image_paths or None, video_paths or None, scheduled_time, post_type),
            daemon=True,
        )
        self.post_thread.start()

    def _update_status_row(self, page_id: str, status: str, detail: str, comment_status: str = "") -> None:
        self.page_post_statuses[page_id] = status
        page_name = next((page.get("name", page.get("id", "")) for page in self.pages if page.get("id") == page_id), page_id)
        for row in range(self.page_table.rowCount()):
            id_item = self.page_table.item(row, 5)
            if id_item and id_item.text() == page_id:
                status_item = QtWidgets.QTableWidgetItem(status)
                if "Success" in status:
                    status_item.setForeground(QtGui.QColor("#22c55e"))
                elif "Failed" in status or "Stopped" in status:
                    status_item.setForeground(QtGui.QColor("#ef4444"))
                elif "Waiting" in status:
                    status_item.setForeground(QtGui.QColor("#f59e0b"))
                else:
                    status_item.setForeground(QtGui.QColor("#cbd5e1"))
                self.page_table.setItem(row, 3, status_item)
                header_item = self.page_table.verticalHeaderItem(row)
                if header_item is None:
                    header_item = QtWidgets.QTableWidgetItem("")
                header_label = page_name if status in {"Chờ xử lý", "Chưa đăng"} else f"{page_name} ({status})"
                header_item.setText(header_label)
                self.page_table.setVerticalHeaderItem(row, header_item)
                break

    def _run_post_queue(
        self,
        pages: List[Dict[str, Any]],
        message: str,
        image_paths: Optional[List[str]],
        video_paths: Optional[List[str]],
        scheduled_time: Optional[datetime],
        post_type: str = "video",
    ) -> None:
        success_count = 0
        fail_count = 0
        use_concurrency = self.concurrency_enabled and len(pages) > 1
        max_workers = self.concurrency_threads if use_concurrency else 1

        def worker(page: Dict[str, Any]) -> None:
            nonlocal success_count, fail_count
            page_id = page["id"]
            if self.stop_requested:
                self.status_changed.emit(page_id, "Đã dừng", "Quá trình bị dừng bởi người dùng")
                return
            if scheduled_time:
                remaining = (scheduled_time - datetime.now()).total_seconds()
                if remaining > 0:
                    self.status_changed.emit(page_id, "Đang chờ lịch", f"Chờ thêm {int(remaining)} giây")
                    while remaining > 0 and not self.stop_requested:
                        time.sleep(1)
                        remaining = (scheduled_time - datetime.now()).total_seconds()
                    if self.stop_requested:
                        return
            if self.concurrency_delay > 0:
                time.sleep(self.concurrency_delay)
            self.status_changed.emit(page_id, "Đang đăng", "Đang gửi yêu cầu tới Facebook")
            self._append_log(f"[Queue] Bắt đầu page={page['name']} type={post_type} media={'video' if video_paths else 'image' if image_paths else 'text'}", "info")
            try:
                if video_paths:
                    self._post_media(page, message, video_paths[0], media_type="video", post_type=post_type)
                elif image_paths:
                    self._post_media(page, message, image_paths[0], media_type="image", post_type=post_type)
                else:
                    self._post_text(page, message)
                success_count += 1
                self.status_changed.emit(page_id, "Thành công", "Đăng thành công")
                self._append_log(f"[Queue] Thành công page={page['name']}", "info")
                if video_paths:
                    self._log_successful_post(page["name"], message, "video")
                elif image_paths:
                    self._log_successful_post(page["name"], message, "image")
                else:
                    self._log_successful_post(page["name"], message, "text")
            except Exception as exc:
                fail_count += 1
                self.status_changed.emit(page_id, "Thất bại", str(exc))
                self._append_log(f"[Queue] Thất bại page={page['name']}: {exc}", "error")

        batches = _chunked_batches(pages, max_workers) if use_concurrency else [pages]
        for batch_index, batch in enumerate(batches):
            if batch_index > 0 and self.concurrency_delay > 0:
                time.sleep(self.concurrency_delay)
            if use_concurrency:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(worker, page) for page in batch]
                    for future in as_completed(futures):
                        future.result()
            else:
                for page in batch:
                    worker(page)
        self.posting_completed.emit(success_count, fail_count)

    @QtCore.pyqtSlot(int, int)
    def _finish_posting(self, success_count: int, fail_count: int) -> None:
        self.post_selected_btn.setEnabled(True)
        self.post_all_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.post_config_btn.setEnabled(True)
        self.post_config_btn.setText("Đăng")
        self.add_row_btn.setEnabled(True)
        self.status_label.setText(f"Hoàn tất: thành công {success_count}, thất bại {fail_count}")
        if not self.stop_requested:
            self._schedule_next_repeat_run()

    def stop_posting(self) -> None:
        self.stop_requested = True
        if hasattr(self, "config_repeat_timer"):
            self.config_repeat_timer.stop()
        self.status_label.setText("Đang dừng quá trình đăng bài...")

    def _schedule_next_repeat_run(self) -> None:
        if not self.schedule_checkbox.isChecked():
            return
        mode = str(self.schedule_mode_combo.currentData() or "once")
        if mode == "once":
            return
        next_run = self._next_repeat_run_time()
        if not next_run:
            return
        delay_ms = max(1000, int(max(0.0, (next_run - datetime.now()).total_seconds()) * 1000))
        self.status_label.setText(f"Hoàn tất. Lần chạy tiếp theo lúc {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        self.config_repeat_timer.start(delay_ms)

    @staticmethod
    def _normalize_post_type(post_type: Optional[str]) -> str:
        value = (post_type or "").strip().lower()
        if not value:
            return "video"
        if value in {"feed", "post", "bài đăng thường", "bai dang thuong", "regular", "normal", "feed post"}:
            return "feed"
        if value in {"video", "reel", "reels", "reel/video", "video/reel", "video post", "reel post"}:
            return "video"
        if "reel" in value:
            return "video"
        if "feed" in value or "post" in value:
            return "feed"
        return "video"

    def _build_public_post_payload(self, page: Dict[str, Any], message: str, *, title: Optional[str] = None, description: Optional[str] = None) -> Dict[str, str]:
        payload: Dict[str, str] = {
            "access_token": page["access_token"],
            "message": message,
            "published": "true",
            "is_published": "true",
            "privacy": '{"value":"EVERYONE"}',
        }
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description
        return payload

    def _publish_graph_object(self, page: Dict[str, Any], object_id: str, *, extra_payload: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        payload = {
            "access_token": page["access_token"],
            "published": "true",
            "is_published": "true",
            "privacy": '{"value":"EVERYONE"}',
        }
        if extra_payload:
            payload.update(extra_payload)
        try:
            response = requests.post(
                f"{self.base_url}/{object_id}",
                data=payload,
                timeout=60,
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {}
        except requests.RequestException as exc:
            self._append_log(f"[Publish] Warning page={page.get('name', page.get('id', ''))} object_id={object_id}: {exc}", "warning")
            return {"error": str(exc)}
        except Exception as exc:
            self._append_log(f"[Publish] Warning page={page.get('name', page.get('id', ''))} object_id={object_id}: {exc}", "warning")
            return {"error": str(exc)}

    def _get_graph_object(self, page: Dict[str, Any], object_id: str, fields: str) -> Dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/{object_id}",
            params={"access_token": page["access_token"], "fields": fields},
            timeout=60,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}

    def _wait_for_public_video(self, page: Dict[str, Any], object_id: str, timeout_seconds: int = 120) -> Dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_payload: Dict[str, Any] = {}
        while time.time() < deadline:
            try:
                payload = self._get_graph_object(page, object_id, "id,permalink_url,status,privacy,published,is_published")
                last_payload = payload
                status_payload = payload.get("status") if isinstance(payload.get("status"), dict) else {}
                video_status = str(status_payload.get("video_status") or status_payload.get("status") or "").lower()
                published_value = payload.get("published")
                is_published_value = payload.get("is_published")
                if video_status in {"ready", "processed", "published"} or published_value in {True, "true", "1"} or is_published_value in {True, "true", "1"}:
                    return payload
            except Exception as exc:
                self.logger.warning("Chờ video publish page=%s object_id=%s lỗi: %s", page.get("name", page.get("id", "")), object_id, exc)
            time.sleep(5)
        if last_payload:
            self.logger.warning("Video %s vẫn chưa sẵn sàng sau %s giây: %s", object_id, timeout_seconds, last_payload)
        return last_payload

    def _post_text(self, page: Dict[str, Any], message: str) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/{page['id']}/feed",
            data=self._build_public_post_payload(page, message),
            timeout=60,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}

    def _build_comment_payload(self, page: Dict[str, Any], comment_text: str) -> Dict[str, str]:
        payload = {"access_token": page["access_token"]}
        normalized_text = (comment_text or "").strip()
        if normalized_text:
            payload["message"] = normalized_text
        else:
            payload["message"] = "Thanks!"
        return payload

    def _post_comment_on_post(
        self,
        page: Dict[str, Any],
        post_id: str,
        comment_text: str,
        comment_image_paths: Optional[List[str]] = None,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> None:
        comment_image_paths = comment_image_paths or []
        if not comment_text and not comment_image_paths:
            return
        try:
            self.logger.info(
                "Gọi API comment page=%s post_id=%s image_count=%s",
                page.get("name", page.get("id", "")),
                post_id,
                len(comment_image_paths),
            )
            data = self._build_comment_payload(page, comment_text)
            files = []
            opened_files = []
            try:
                for image_path in comment_image_paths:
                    if not image_path:
                        continue
                    image_path_obj = Path(image_path)
                    if not image_path_obj.exists():
                        raise FileNotFoundError(f"File ảnh comment không tồn tại: {image_path}")
                    media_file = image_path_obj.open("rb")
                    opened_files.append(media_file)
                    files.append(("source", (image_path_obj.name, media_file, "application/octet-stream")))

                response = requests.post(
                    f"{self.base_url}/{post_id}/comments",
                    data=data,
                    files=files or None,
                    timeout=60,
                    proxies=proxy_config or None,
                )
            finally:
                for handle in opened_files:
                    handle.close()

            self.logger.info("Response comment status=%s body=%s", response.status_code, response.text[:1000])
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code >= 400:
                message = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else str(payload)
                error_code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
                missing_permissions = []
                if "pages_read_user_content" in message:
                    missing_permissions.append("pages_read_user_content")
                if "pages_manage_engagement" in message:
                    missing_permissions.append("pages_manage_engagement")
                if "permission" in message.lower() or error_code == 200:
                    self.logger.warning(
                        "Comment bị chặn bởi Facebook. Missing permissions: %s. Message: %s",
                        missing_permissions or ["unknown"],
                        message,
                    )
                raise RuntimeError(f"Comment thất bại: status={response.status_code} message={message} missing_permissions={missing_permissions}")
            if not payload.get("id"):
                raise RuntimeError(f"Comment API trả về dữ liệu không hợp lệ: {payload}")
        except Exception as exc:
            raise RuntimeError(f"Comment thất bại: {exc}") from exc

    def _extract_post_id(self, response_data: Dict[str, Any]) -> Optional[str]:
        if not isinstance(response_data, dict):
            return None
        return response_data.get("id") or response_data.get("post_id")

    def _extract_facebook_error_message(self, response: requests.Response, *, fallback: Optional[str] = None) -> str:
        if response is None:
            return fallback or "Unknown Facebook API error"

        payload: Any = {}
        text = (response.text or "").strip()
        if text:
            try:
                payload = response.json()
            except ValueError:
                try:
                    payload = json.loads(text)
                except (TypeError, ValueError):
                    payload = {}
        elif response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = {}

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if message:
                    return str(message)
            if payload.get("message"):
                return str(payload["message"])

        if text:
            return text
        if fallback:
            return fallback
        return f"status={response.status_code}"

    def _post_media(self, page: Dict[str, Any], message: str, media_path: str, media_type: str, title: Optional[str] = None, proxy_config: Optional[Dict[str, str]] = None, post_type: Optional[str] = None) -> Dict[str, Any]:
        page_name = page.get("name", page.get("id", ""))
        tasks = page.get("tasks")
        if isinstance(tasks, list) and tasks and "CREATE_CONTENT" not in tasks:
            raise RuntimeError(f"Page token không có task CREATE_CONTENT để đăng nội dung: tasks={tasks}")
        if not page.get("access_token"):
            raise RuntimeError("Thiếu Page Access Token. Hãy tải lại Page từ /me/accounts bằng user quản trị page.")
        for attempt in range(1, self.post_retry_count + 2):
            try:
                self._append_log(f"[Media] Bắt đầu page={page_name} type={media_type} attempt={attempt}/{self.post_retry_count + 1} path={media_path}", "info")
                if media_type == "image":
                    with open(media_path, "rb") as media_file:
                        response = requests.post(
                            f"{self.base_url}/{page['id']}/photos",
                            data=self._build_public_post_payload(page, message, title=title, description=message),
                            files={"source": media_file},
                            timeout=120,
                            proxies=proxy_config or None,
                        )
                    if response.status_code >= 400:
                        message_text = self._extract_facebook_error_message(response)
                        self._append_log(f"[Media] Facebook error page={page_name} type={media_type} status={response.status_code} body={message_text}", "error")
                        raise RuntimeError(f"Đăng media thất bại: status={response.status_code} message={message_text}")
                    try:
                        return response.json()
                    except ValueError:
                        return {}

                normalized_post_type = self._normalize_post_type(post_type)
                self._append_log(f"[Media] Using Reels uploader page={page_name} type={media_type} mode={normalized_post_type}", "info")
                try:
                    uploader = getattr(self, "reels_uploader", None)
                except RuntimeError:
                    uploader = None
                if uploader is None:
                    uploader = FacebookReelsUploader(self.base_url, logger=self.logger, append_log=self._append_log)
                    try:
                        self.reels_uploader = uploader
                    except RuntimeError:
                        pass
                upload_result = uploader.start_upload(
                    page,
                    media_path,
                    message,
                    title=title or page.get("name") or "Video",
                    description=message,
                    proxy_config=proxy_config,
                )
                upload_id = upload_result.get("id") or upload_result.get("video_id") or upload_result.get("upload_id")
                if not upload_id:
                    raise RuntimeError(f"Reels upload không trả về upload_id: {upload_result}")

                if upload_result.get("status") or upload_result.get("video_status"):
                    self._append_log(f"[Media] Reels upload started page={page_name} upload_id={upload_id} status={upload_result.get('status')}", "info")

                binary_result = uploader.upload_binary(page, upload_id, media_path, proxy_config=proxy_config)
                if binary_result.get("error"):
                    raise RuntimeError(f"Reels binary upload failed: {binary_result['error']}")

                publish_result = uploader.finish_publish(page, upload_id, message=message, title=title or page.get("name") or "Video", description=message, proxy_config=proxy_config)
                final_payload = uploader.verify_public_visibility(page, upload_id, timeout_seconds=300, proxy_config=proxy_config)
                final_payload = dict(final_payload or {})
                final_payload.setdefault("upload_id", upload_id)
                final_payload.setdefault("post_id", publish_result.get("post_id") if isinstance(publish_result, dict) else None)
                final_payload.setdefault("publish_result", publish_result)
                final_payload.setdefault("binary_result", binary_result)
                final_payload.setdefault("start_result", upload_result)
                final_payload.setdefault("permalink_url", uploader.get_permalink(page, upload_id, proxy_config=proxy_config))
                self._append_log(
                    f"[Media] Reels page={page_name} upload_id={upload_id} permalink={final_payload.get('permalink_url') or 'n/a'} privacy={final_payload.get('privacy') or 'n/a'} published={final_payload.get('published')} is_published={final_payload.get('is_published')} public_visibility={final_payload.get('public_visibility')}",
                    "info",
                )
                if final_payload.get("public_visibility") is False:
                    self._append_log(
                        f"[Media] Reels page={page_name} upload_id={upload_id} đã publish nhưng chưa verify được public visibility: {final_payload.get('public_visibility_reason') or 'unknown'}",
                        "warning",
                    )
                    raise RuntimeError(
                        f"Reels đã publish nhưng không public cho người ngoài xem được: {final_payload.get('public_visibility_reason') or 'unknown'}"
                    )
                return final_payload
            except requests.exceptions.RequestException as exc:
                self.logger.exception("Lỗi request đăng media page=%s type=%s: %s", page_name, media_type, exc)
                self._append_log(f"[Media] Failed page={page_name} type={media_type}: {exc}", "error")
                if self._is_facebook_block_error(str(exc)) and attempt <= self.post_retry_count:
                    delay = self._retry_delay_for_attempt(attempt, self.post_retry_delay_base)
                    self.logger.warning("Retry đăng media page=%s sau %s giây do lỗi request: %s", page_name, delay, exc)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Đăng media thất bại: {exc}") from exc
            except Exception as exc:
                self.logger.exception("Lỗi không xác định khi đăng media page=%s type=%s: %s", page_name, media_type, exc)
                self._append_log(f"[Media] Failed page={page_name} type={media_type}: {exc}", "error")
                raise RuntimeError(f"Đăng media thất bại: {exc}") from exc
        raise RuntimeError(f"Đăng media thất bại sau {self.post_retry_count + 1} lần thử cho page={page_name}")


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Nunito", 9))
    window = FacebookPageManagerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    # Ensure resources folder exists (for packaged builds)
    res_dir = Path(__file__).resolve().parent / "resources"
    if not res_dir.exists():
        try:
            res_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    main()

