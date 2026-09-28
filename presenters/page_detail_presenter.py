from typing import Any

from services.page_detail_service import PageDetailService


class PageDetailPresenter:
    """Điều khiển dialog chi tiết page (IPageDetailView)."""

    def __init__(self, view: Any, page_detail_service: PageDetailService) -> None:
        self._view = view
        self._service = page_detail_service

    def load_data(self, page_id: str, page_name: str = "", access_token: str = "") -> None:
        latest = self._service.load_latest(page_id)
        history = self._service.load_history(page_id)
        if latest:
            self._view.populate_info(latest)
        self._view.populate_history(history)

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

    def load_history(self, page_id: str) -> None:
        self._view.populate_history(self._service.load_history(page_id))
