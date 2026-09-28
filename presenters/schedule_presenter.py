"""Điều phối tính năng lịch đăng chạy nền.

Presenter chỉ nói chuyện với giao diện qua `IScheduleView` (hỏi kế hoạch,
vẽ lại giờ trong bảng hàng đợi, đổi trạng thái nút) còn việc lưu lịch và chạy
nền do `ScheduleService` đảm nhiệm.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SETTING_AUTO_START = "schedule_auto_start"
STATUS_PENDING_LABEL = "Chờ đến giờ"


class SchedulePresenter:
    def __init__(
        self,
        view: Any,
        main_view: Any,
        schedule_service: Any,
        settings_repo: Any,
        page_source: Callable[[], List[Dict[str, Any]]],
        concurrency_source: Callable[[], Dict[str, Any]],
    ) -> None:
        self._view = view
        self._main_view = main_view
        self._service = schedule_service
        self._settings_repo = settings_repo
        self._page_source = page_source
        self._concurrency_source = concurrency_source

    # ── Vòng đời ───────────────────────────────────────────────────

    def on_app_start(self) -> None:
        """Nạp lại lịch còn chờ vào hàng đợi.

        Không bật scheduler ở đây vì lúc này app còn đang khoá licence; việc
        bật được gọi từ ``start_auto_if_enabled`` sau khi kiểm tra bản quyền.
        """
        pending = self._service.load_pending()
        if pending:
            if not self._view.get_config_rows():
                # Hàng đợi trống (app vừa mở) -> đổ lịch còn chờ vào để thấy được.
                self._view.clear_config_table()
                for item in pending:
                    self._view.add_config_row({
                        "page_id": item.get("page_id", ""),
                        "page_name": item.get("page_name", ""),
                        "title": item.get("title", ""),
                        "description": item.get("description", ""),
                        "video_path": item.get("video_path", ""),
                        "comment": item.get("comment_text", ""),
                        "comment_images": item.get("comment_image_paths", ""),
                        "post_type": item.get("post_type", "video"),
                        "schedule_time": item.get("schedule_time", ""),
                        "status": STATUS_PENDING_LABEL,
                    })
            self._view.apply_schedule_times(pending)
        self._refresh_info()

    def start_auto_if_enabled(self) -> None:
        """Chỉ gọi sau khi app đã qua kiểm tra bản quyền."""
        if self._is_auto_enabled():
            self._start_background()

    def _is_auto_enabled(self) -> bool:
        try:
            return str(self._settings_repo.get_setting(SETTING_AUTO_START, "0")) == "1"
        except Exception:
            return False

    def _set_auto_enabled(self, enabled: bool) -> None:
        try:
            self._settings_repo.set_setting(SETTING_AUTO_START, "1" if enabled else "0")
        except Exception:
            logger.exception("Không lưu được trạng thái bật lịch tự động")

    # ── Hành động từ giao diện ─────────────────────────────────────

    def plan_batch(self) -> None:
        rows = self._view.get_config_rows()
        ready = [
            row for row in rows
            if str(row.get("page_name") or row.get("page") or "").strip()
            and str(row.get("video_path") or "").strip()
        ]
        if not ready:
            self._main_view.show_warning(
                "Chưa có video",
                "Hàng đợi đang trống hoặc các dòng chưa có video.\n"
                "Hãy bấm 'Chọn video' để gán video cho page trước khi lên lịch.",
            )
            return

        plan = self._view.ask_schedule_plan(len(ready))
        if not plan:
            return

        try:
            saved = self._service.plan_rows(ready, plan)
        except Exception as exc:
            logger.exception("Lên lịch thất bại")
            self._main_view.show_error("Lên lịch thất bại", str(exc))
            return

        if not saved:
            self._main_view.show_warning(
                "Không tạo được lịch",
                "Không sinh được mốc thời gian nào. Kiểm tra lại khung giờ hoặc số phút giãn cách.",
            )
            return

        self._view.apply_schedule_times(saved)
        self._refresh_info()
        first = saved[0].get("schedule_time", "")
        last = saved[-1].get("schedule_time", "")
        self._main_view.show_info(
            "Đã lên lịch",
            f"{len(saved)} video đã được gán giờ.\n"
            f"Từ {first[:16].replace('T', ' ')} đến {last[:16].replace('T', ' ')}.\n\n"
            "Bấm 'Bật lịch tự động' để app tự đăng đúng giờ, kể cả khi bạn mở lại app sau này.",
        )

    def toggle_auto(self) -> None:
        if self._service.is_running():
            self.stop()
        else:
            self._start_background()

    def _start_background(self) -> None:
        started = self._service.start(
            get_pages=self._get_pages,
            concurrency=self._concurrency_source(),
            on_event=self._on_event,
        )
        if not started:
            self._refresh_info()
            return
        self._set_auto_enabled(True)
        logger.info("Đã bật lịch đăng tự động")
        self._refresh_info()
        self._main_view.show_status("Đã bật lịch đăng tự động")

    def stop(self, remember: bool = True) -> None:
        """Dừng scheduler. ``remember=False`` = bấm "Dừng" -> không tự bật lại
        khi mở app lần sau (chỉ ghi nhớ nếu scheduler đang chạy)."""
        was_running = self._service.is_running()
        self._service.stop()
        if not remember and was_running:
            self._set_auto_enabled(False)
        self._refresh_info()
        if was_running:
            self._main_view.show_status("Đã tắt lịch đăng tự động")

    def clear_pending(self) -> None:
        count = self._service.pending_count()
        if not count:
            self._main_view.show_info("Không có lịch chờ", "Hiện không có lịch nào đang chờ đăng.")
            return
        if not self._main_view.confirm(
            "Xoá lịch chờ",
            f"Xoá {count} lịch đang chờ? Các lịch đã đăng rồi vẫn được giữ.",
        ):
            return
        self._service.clear_pending()
        self._refresh_info()
        self._main_view.show_status(f"Đã xoá {count} lịch chờ")

    def pending_count(self) -> int:
        return self._service.pending_count()

    def is_running(self) -> bool:
        return self._service.is_running()

    # ── Cầu nối luồng nền -> giao diện ─────────────────────────────

    def _get_pages(self) -> List[Dict[str, Any]]:
        return self._page_source() or []

    def _on_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        if kind == "status":
            self._view.update_config_status(
                str(event.get("page_name") or ""),
                str(event.get("status") or ""),
                str(event.get("page_id") or ""),
            )
        elif kind == "link":
            self._view.update_config_link(str(event.get("page_name") or ""), str(event.get("link") or ""))
        elif kind == "dispatched":
            self._main_view.log(f"Lịch đăng: bắt đầu {event.get('count', 0)} video đã tới giờ")
        elif kind == "done":
            self._main_view.log(
                "Lịch đăng: xong — thành công {success}, thất bại {failed}, bỏ qua {skipped}".format(
                    success=event.get("success", 0),
                    failed=event.get("failed", 0),
                    skipped=event.get("skipped", 0),
                )
            )
        elif kind == "warning":
            self._main_view.show_warning("Lịch đăng", str(event.get("text") or ""))
        if kind == "info":
            self._main_view.show_status(str(event.get("text") or ""))
        if kind in {"dispatched", "done", "warning", "stopped"}:
            self._refresh_info()

    def _refresh_info(self) -> None:
        try:
            self._view.update_schedule_info(self.pending_count(), self.is_running(), self._service.is_dispatching())
        except Exception:
            logger.exception("Không cập nhật được thanh trạng lịch đăng")
