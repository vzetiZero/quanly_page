import logging
import threading
from typing import Any, Callable, Dict, Optional

from di.interfaces import IPageDetailRepository, IPageDetailFetcher

logger = logging.getLogger("facebook_ui")


class PageDetailService:
    def __init__(self, detail_repo: IPageDetailRepository, fetcher: IPageDetailFetcher) -> None:
        self._repo = detail_repo
        self._fetcher = fetcher

    def load_latest(self, page_id: str) -> Optional[Dict[str, Any]]:
        return self._repo.load_latest_page_detail(page_id)

    def refresh(self, page_id: str, page_name: str, access_token: str) -> Dict[str, Any]:
        details = self._fetcher.fetch_all(page_id, page_name, access_token)
        if details:
            self._repo.save_page_details(page_id, details)
        return details

    def refresh_async(
        self,
        page_id: str,
        page_name: str,
        access_token: str,
        on_complete: Callable[[Dict[str, Any]], None],
        on_error: Callable[[str], None],
    ) -> None:
        def worker():
            try:
                result = self.refresh(page_id, page_name, access_token)
                on_complete(result)
            except Exception as exc:
                logger.warning("Lỗi refresh page detail %s: %s", page_id, exc)
                on_error(str(exc))

        threading.Thread(target=worker, daemon=True).start()
