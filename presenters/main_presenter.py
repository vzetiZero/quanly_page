import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5 import QtWidgets
from di.container import Container
from di.interfaces import IMainView
from presenters.page_list_presenter import PageListPresenter
from presenters.config_presenter import ConfigPresenter
from presenters.schedule_presenter import SchedulePresenter
from presenters.settings_presenter import SettingsPresenter


class MainPresenter:
    def __init__(self, container: Container, view: IMainView) -> None:
        self._container = container
        self._view = view
        self.license_locked = False
        self.stop_requested = False
        self._bypass_schedule_once = False

        self.page_list = PageListPresenter(
            view=view,
            main_view=view,
            page_service=container.page_service,
        )
        self.config = ConfigPresenter(
            view=view,
            main_view=view,
            config_service=container.config_service,
            post_service=container.post_service,
        )
        self.settings = SettingsPresenter(
            view=view,
            main_view=view,
            page_service=container.page_service,
        )
        self.schedule = SchedulePresenter(
            view=view,
            main_view=view,
            schedule_service=container.schedule_service,
            settings_repo=container.db,
            page_source=lambda: self.page_list.pages,
            concurrency_source=view.get_concurrency_config,
        )

    def on_app_start(self) -> None:
        self._view.set_controls_enabled(False)
        self._load_saved_config()
        self.page_list.load_all_from_cache()
        self.schedule.on_app_start()

    def on_trial_check(self) -> None:
        try:
            self._container.license_service.load_state()
            if self._container.license_service.is_active():
                self._activate_license(
                    self._container.license_service.code or "1D",
                    self._container.license_service.expires_at or datetime(2099, 12, 31, 23, 59, 59),
                )
                return
            self._lock_trial_expired()
        except Exception as exc:
            self._lock_trial_expired()

    def on_trial_activate(self, code: str) -> None:
        expires_at = self._container.license_service.validate_code(code)
        if expires_at:
            if self._container.license_service.is_active() and not self._container.license_service.is_permanent() and code.strip().lower() != "vietboss1998":
                self._view.show_info("Thông báo", "Bản dùng thử đã được kích hoạt. Chỉ mã `vietboss1998` mới có thể ghi đè.")
                return
            self._activate_license(code, expires_at)
            if code.strip().lower() != "vietboss1998":
                self._view.show_info("Thành công", f"Đã kích hoạt bản dùng thử cho {code}")
        else:
            self._view.show_warning("Mã không hợp lệ", "LH zl 0363657998")
            self._lock_trial_expired()

    def on_load_pages(self) -> None:
        tokens = [t.strip() for t in self._view.get_token_input().splitlines() if t.strip()]
        if not tokens:
            self._view.show_warning("Thiếu token", "Vui lòng nhập ít nhất 1 access token")
            return
        self._save_config()
        self._view.show_status("Đang kiểm tra cache và token...")
        self.page_list.load_pages_from_tokens(tokens)

    def on_post(self) -> None:
        selected = self.page_list.get_selected_pages(use_all=False)
        if not selected:
            selected = self.page_list.get_selected_pages(use_all=True)
        if not selected:
            self._view.show_warning("Chưa chọn page", "Vui lòng chọn ít nhất 1 page")
            return
        self._prepare_config_from_selected(selected)

    def on_stop(self) -> None:
        self.stop_requested = True
        self._container.post_service.stop_requested = True
        # Bấm "Dừng" thì dừng luôn lịch chạy nền, và không tự bật lại lần sau.
        self.schedule.stop(remember=False)

    def on_tokens_file_loaded(self, file_path: str) -> None:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            tokens = [line.strip() for line in content.splitlines() if line.strip()]
            current = self._view.get_token_input().strip()
            if current:
                current += "\n"
            self._view.set_token_input(current + "\n".join(tokens))
            self._save_config()
            self._view.show_status(f"Đã nhập {len(tokens)} token từ file")
        except Exception as exc:
            self._view.show_error("Lỗi", str(exc))

    def on_refresh_tokens(self) -> None:
        tokens = [t.strip() for t in self._view.get_token_input().splitlines() if t.strip()]
        invalid = [t for t in tokens if not self.page_list.token_statuses.get(t, {}).get("valid", True)]
        if not invalid:
            self._view.show_info("Thông báo", "Không có token nào cần refresh")
            return
        from PyQt5 import QtWidgets
        new_text, ok = QtWidgets.QInputDialog.getText(None, "Refresh token", "Nhập token mới (mỗi token 1 dòng):")
        if not ok or not new_text.strip():
            return
        new_tokens = [line.strip() for line in new_text.splitlines() if line.strip()]
        updated = self.settings.refresh_tokens(tokens, invalid, new_tokens)
        self._view.set_token_input("\n".join(updated))
        self._save_config()
        self._view.show_status("Đang tải lại page với token mới...")
        self.page_list.load_pages_from_tokens(updated)

    def on_clear_cache(self) -> None:
        if self._view.confirm("Xóa cache", "Bạn có muốn xóa toàn bộ dữ liệu page và trạng thái token?"):
            self._container.db.clear_all_cache()
            self.page_list.pages = []
            self.page_list.page_selection_states.clear()
            self.page_list.page_post_statuses.clear()
            self.page_list.token_statuses.clear()
            self.page_list._refresh_display()
            self._view.show_status("Đã xóa cache")

    def _activate_license(self, code: str, expires_at: datetime) -> None:
        try:
            self._container.license_service.activate(code, expires_at)
            self.license_locked = False
            self._view.set_controls_enabled(True)
            self._view.show_status("Bản dùng thử đã được kích hoạt")
            # Chỉ bật lịch chạy nền khi app đã qua kiểm tra bản quyền.
            self.schedule.start_auto_if_enabled()
            self._update_trial_status()
        except Exception:
            self._lock_trial_expired()

    def _lock_trial_expired(self) -> None:
        self.license_locked = True
        self._view.set_controls_enabled(False)
        # Hết hạn giữa chừng thì dừng lịch, nhưng giữ nguyên lựa chọn của người dùng.
        self.schedule.stop()
        self._view.show_status("Bản dùng thử chưa được kích hoạt hoặc đã hết hạn.")
        self._view.update_trial_status("Trial: đã hết hạn", "background: #1f0707; border: 1px solid #ef4444; border-radius: 8px; padding: 6px 10px; color: #fecaca; font-weight: 800;")

    def _update_trial_status(self) -> None:
        if self._container.license_service.is_active():
            label = self._container.license_service.remaining_label()
            self._view.update_trial_status(label, "background: #052e2b; border: 1px solid #14b8a6; border-radius: 8px; padding: 6px 10px; color: #ccfbf1; font-weight: 800;")
        elif self._container.license_service.expires_at is None and self._container.license_service.code is None:
            self._view.update_trial_status("Trial: chưa kích hoạt", "background: #1c1404; border: 1px solid #f59e0b; border-radius: 8px; padding: 6px 10px; color: #fde68a; font-weight: 800;")
        else:
            self._view.update_trial_status("Trial: đã hết hạn", "background: #1f0707; border: 1px solid #ef4444; border-radius: 8px; padding: 6px 10px; color: #fecaca; font-weight: 800;")
            if not self.license_locked:
                self._lock_trial_expired()

    def _prepare_config_from_selected(self, selected_pages: List[Dict[str, Any]]) -> None:
        post_type = self._container.post_service.normalize_post_type("video")
        self.config.prepare_rows_from_selected(selected_pages, post_type)

        video_folder = self._view.video_folder_input.text().strip()
        comment_folder = self._view.comment_image_folder_input.text().strip()
        if not video_folder and not comment_folder:
            return

        from services.config_service import VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
        video_files = self._container.config_service.collect_media_files(video_folder, VIDEO_EXTENSIONS) if video_folder else []
        comment_files = self._container.config_service.collect_media_files(comment_folder, IMAGE_EXTENSIONS) if comment_folder else []

        for i in range(self._view.config_table.rowCount()):
            if video_files and i < len(video_files):
                item = self._view.config_table.item(i, 3)
                if item is None:
                    item = QtWidgets.QTableWidgetItem("")
                    self._view.config_table.setItem(i, 3, item)
                item.setText(video_files[i])
                title_item = self._view.config_table.item(i, 1)
                if title_item is None:
                    title_item = QtWidgets.QTableWidgetItem("")
                    self._view.config_table.setItem(i, 1, title_item)
                if not title_item.text().strip():
                    from pathlib import Path
                    title_item.setText(Path(video_files[i]).stem)
            if comment_files and i < len(comment_files):
                item = self._view.config_table.item(i, 5)
                if item is None:
                    item = QtWidgets.QTableWidgetItem("")
                    self._view.config_table.setItem(i, 5, item)
                item.setText(comment_files[i])

    def get_token_input(self) -> str:
        return self._view.get_token_input()

    def _load_saved_config(self) -> None:
        data = self._container.config_service.load()
        if not data:
            return

        tokens = self._container.config_service.get_tokens()
        if tokens:
            self._view.set_token_input("\n".join(tokens))
            if self._container.license_service.is_active():
                threading.Timer(0.3, self.on_load_pages).start()

        video_folder = str(data.get("video_folder", "") or "")
        if video_folder:
            self._view.video_folder_input.setText(video_folder)
        comment_folder = str(data.get("comment_image_folder", "") or "")
        if comment_folder:
            self._view.comment_image_folder_input.setText(comment_folder)

        self._view.use_comment_images_checkbox.setChecked(bool(data.get("use_comment_images", False)))
        self._view.skip_missing_checkbox.setChecked(bool(data.get("skip_missing_videos", True)))

        quick = data.get("quick_config") or {}
        self._view.quick_post_content_input.setPlainText(str(quick.get("post_content", "") or ""))
        self._view.quick_comment_content_input.setPlainText(str(quick.get("comment_content", "") or ""))

        self._view.load_proxy_config(data.get("proxy") or {})
        self._view.load_concurrency_config(data.get("concurrency") or {})

    def _save_config(self) -> None:
        tokens = [t.strip() for t in self._view.get_token_input().splitlines() if t.strip()]
        payload: Dict[str, Any] = {
            "tokens": tokens,
            "message": "",
            "post_type": "video",
            "images": [],
            "videos": [],
            "video_folder": self._view.video_folder_input.text().strip(),
            "comment_image_folder": self._view.comment_image_folder_input.text().strip(),
            "use_comment_images": self._view.use_comment_images_checkbox.isChecked(),
            "skip_missing_videos": self._view.skip_missing_checkbox.isChecked(),
            "quick_config": {
                "post_content": self._view.quick_post_content_input.toPlainText().strip(),
                "comment_content": self._view.quick_comment_content_input.toPlainText().strip(),
            },
            "proxy": self.settings.get_proxy_config(),
            "concurrency": self.settings.get_concurrency_config(),
            "config_rows": [],
        }
        self._container.config_service.save(payload)
