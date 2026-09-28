from typing import Any, Dict, Optional

from services.page_detail_service import PageDetailService


class PageDetailPresenter:
    """Điều khiển dialog chi tiết page (IPageDetailView)."""

    def __init__(
        self,
        view: Any,
        page_detail_service: PageDetailService,
        stats_service: Any = None,
    ) -> None:
        self._view = view
        self._service = page_detail_service
        self._stats_service = stats_service
        self._page_id = ""
        self._page_name = ""
        self._access_token = ""

    def load_data(
        self,
        page_id: str,
        page_name: str = "",
        access_token: str = "",
        initial_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._page_id = page_id
        self._page_name = page_name
        self._access_token = access_token

        latest = self._service.load_latest(page_id)
        if latest:
            self._view.populate_info(latest)
        if initial_stats:
            self._view.populate_overview(initial_stats)

        self.load_videos()
        if not latest and access_token:
            self.refresh(page_id, page_name, access_token)

    def refresh(self, page_id: str, page_name: str, access_token: str) -> None:
        self._view.show_overlay("Đang lấy dữ liệu từ Facebook...")
        self._view.set_refresh_enabled(False)
        self._view.set_refresh_text("Đang cập nhật...")

        def on_complete(data: dict) -> None:
            # Phát tín hiệu để UI cập nhật ở luồng chính (an toàn luồng).
            self._view.details_ready.emit(data or {})

        def on_error(message: str) -> None:
            self._view.details_error.emit(message)

        self._service.refresh_async(page_id, page_name, access_token, on_complete, on_error)

    # ── Danh sách video của page ───────────────────────────────────
    def load_videos(self) -> None:
        if not self._stats_service or not self._page_id or not self._access_token:
            return
        self._view.set_videos_loading(True)

        def on_complete(videos: list) -> None:
            self._view.set_videos_loading(False)
            self._view.videos_ready.emit(videos or [])

        def on_error(message: str) -> None:
            self._view.set_videos_loading(False)
            self._view.videos_ready.emit([])

        self._stats_service.fetch_page_videos_async(
            self._page_id, self._access_token, on_complete, on_error
        )

    # ── Số liệu tổng quan (cùng nguồn với tab Thống kê) ───────────
    def refresh_stats(self) -> None:
        if not self._stats_service or not self._page_id:
            return
        self._view.set_stats_loading(True)

        def on_complete(row: dict) -> None:
            self._view.set_stats_loading(False)
            self._view.page_stats_ready.emit(row or {})

        def on_error(message: str) -> None:
            self._view.set_stats_loading(False)
            self._view.page_stats_ready.emit({})

        self._stats_service.refresh_page_async(
            {"id": self._page_id, "name": self._page_name, "access_token": self._access_token},
            on_complete,
            on_error,
        )
