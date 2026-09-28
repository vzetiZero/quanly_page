import threading
from typing import Any, Callable, Dict, List, Optional

from di.interfaces import IMainView, IConfigView
from services.config_service import ConfigService, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from services.post_service import PostService


class ConfigPresenter:
    def __init__(
        self,
        view: IConfigView,
        main_view: IMainView,
        config_service: ConfigService,
        post_service: PostService,
    ) -> None:
        self._view = view
        self._main_view = main_view
        self._config_service = config_service
        self._post_service = post_service

    def prepare_rows_from_selected(self, selected_pages: List[Dict[str, Any]], post_type: str) -> None:
        self._view.clear_config_table()

        def worker():
            for page in selected_pages:
                page_name = str((page.get("name") or page.get("id") or "")).strip()
                self._view.add_config_row({"page_name": page_name, "post_type": post_type})

        threading.Thread(target=worker, daemon=True).start()

    def add_row(self, default_post_type: str) -> None:
        self._view.add_config_row({"page_name": "", "post_type": default_post_type})

    def populate_assignment_rows(self, entries: List[Dict[str, Any]]) -> None:
        """Đổ kết quả chia video (page ↔ video) vào bảng hàng đợi."""
        self._view.clear_config_table()
        for entry in entries:
            self._view.add_config_row(entry)

    def append_assignment_rows(self, entries: List[Dict[str, Any]]) -> tuple:
        """Thêm video vào hàng đợi, chặn trùng page.

        Nếu page đã có trong hàng đợi -> cập nhật dòng cũ (không thêm dòng mới).
        Trả về (added, updated, skipped).
        """
        added = 0
        updated = 0
        skipped = 0
        for entry in entries:
            page = str(entry.get("page_name") or entry.get("page") or "").strip()
            if not entry.get("video_path"):
                skipped += 1
                continue
            row_idx = self._view.find_config_row(page) if page else -1
            if page and row_idx >= 0:
                self._view.update_config_row(row_idx, entry)
                updated += 1
            else:
                self._view.add_config_row(entry)
                added += 1
        return added, updated, skipped

    def apply_folder_mapping(
        self,
        video_folder: str,
        comment_folder: str,
        use_comment_images: bool,
        video_rotation_index: int,
        comment_rotation_index: int,
        successful_video_paths: Optional[Dict[str, set]] = None,
    ) -> None:
        video_files = self._config_service.collect_media_files(video_folder, VIDEO_EXTENSIONS)
        comment_files = self._config_service.collect_media_files(comment_folder, IMAGE_EXTENSIONS) if use_comment_images else []

        rows = self._view.get_config_rows()
        assigned_videos_by_page: Dict[str, set] = {}

        for row_idx, row in enumerate(rows):
            if video_files:
                page_key = row.get("page_key", row.get("page", ""))
                posted_paths = successful_video_paths.get(page_key, set()) if successful_video_paths else set()
                already_assigned = assigned_videos_by_page.setdefault(page_key, set())
                video_path = ""
                for offset in range(len(video_files)):
                    candidate = video_files[(video_rotation_index + row_idx + offset) % len(video_files)]
                    if candidate in posted_paths or candidate in already_assigned:
                        continue
                    video_path = candidate
                    break
                if video_path:
                    already_assigned.add(video_path)
                    row["video_path"] = video_path
                    if not row.get("title"):
                        row["title"] = self._config_service.derive_title(video_path, "", row.get("page", ""))
                else:
                    row["video_path"] = ""
                    row["status"] = "Hết video mới"

            if use_comment_images and comment_files:
                row["comment_images"] = comment_files[(comment_rotation_index + row_idx) % len(comment_files)]

    def start_posting(
        self,
        pages: List[Dict[str, Any]],
        base_url: str,
        concurrency_enabled: bool,
        concurrency_threads: int,
        concurrency_delay: float,
        on_complete: Callable,
    ) -> None:
        self._view.set_post_config_button_enabled(False)
        self._view.set_post_config_button_text("Đang đăng...")
        self._main_view.show_status("Đang xử lý hàng đợi đăng video...")

        rows = self._view.get_config_rows()
        posting_rows = []
        for row in rows:
            page_name = row.get("page", "")
            if not page_name:
                continue
            video_path = row.get("video_path", "")
            if not video_path:
                continue
            posting_rows.append({
                "row_index": rows.index(row),
                "page_name": page_name,
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "video_path": video_path,
                "schedule_time": row.get("schedule_time", ""),
                "comment_text": row.get("comment", ""),
                "comment_image_paths": row.get("comment_images", ""),
                "post_type": row.get("post_type", "video"),
            })

        if not posting_rows:
            self._main_view.show_warning("Thiếu dữ liệu", "Không có page nào đủ điều kiện để đăng.")
            self._view.set_post_config_button_enabled(True)
            self._view.set_post_config_button_text("Đăng")
            return

        def worker():
            self._post_service.stop_requested = False
            self._post_service.run_config_post_queue(
                rows=posting_rows,
                pages=pages,
                base_url=base_url,
                concurrency_enabled=concurrency_enabled,
                concurrency_threads=concurrency_threads,
                concurrency_delay=concurrency_delay,
                on_status=lambda name, s, d: self._view.update_config_status(name, s),
                on_config_status=lambda name, s: self._view.update_config_status(name, s),
                on_complete=on_complete,
            )

        threading.Thread(target=worker, daemon=True).start()

    def apply_quick_content(self, post_content: str, comment_content: str) -> None:
        if not post_content and not comment_content:
            return
        rows = self._view.get_config_rows()
        for row in rows:
            if post_content:
                row["title"] = post_content
                row["description"] = post_content
            if comment_content:
                row["comment"] = comment_content
            else:
                row["comment"] = post_content

    def set_post_type_for_all_rows(self, post_type: str) -> None:
        rows = self._view.get_config_rows()
        for row in rows:
            row["post_type"] = post_type
