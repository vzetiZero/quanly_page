from typing import Any, Dict, List, Optional

from di.interfaces import IMainView, IPageListView
from services.page_service import PageService


class PageListPresenter:
    def __init__(self, view: IPageListView, main_view: IMainView, page_service: PageService) -> None:
        self._view = view
        self._main_view = main_view
        self._page_service = page_service
        self.pages: List[Dict[str, Any]] = []
        self.page_selection_states: Dict[str, bool] = {}
        self.page_post_statuses: Dict[str, str] = {}
        self.token_statuses: Dict[str, Dict[str, Any]] = {}
        self.current_page = 1
        self.page_size = 20

    # ── Khoá duy nhất cho mỗi dòng (tránh 2 tài khoản cùng quản lý 1 page) ──
    @staticmethod
    def page_key(page: Dict[str, Any]) -> str:
        account = page.get("account_token") or page.get("account_label") or ""
        return f"{account}|{page.get('id', '')}"

    def get_page_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        for page in self.pages:
            if self.page_key(page) == key:
                return page
        return None

    def set_page_status(self, key: str, status: str) -> None:
        """Lưu trạng thái token vừa kiểm tra vào dữ liệu page (giữ khi vẽ lại bảng)."""
        page = self.get_page_by_key(key)
        if page is not None:
            page["status"] = status

    # ── Tải dữ liệu ───────────────────────────────────────────────
    def load_all_from_cache(self) -> None:
        self.pages = self._page_service.load_all_from_cache()
        self._refresh_display()

    def load_pages_from_tokens(self, tokens: List[str]) -> None:
        import threading

        def worker():
            for token in tokens:
                _pages, status_info = self._page_service.load_pages_for_token(token)
                self.token_statuses[token] = status_info
            self.pages = self._page_service.load_all_from_cache()
            self._refresh_display()

        threading.Thread(target=worker, daemon=True).start()

    # ── Chọn / bỏ chọn ────────────────────────────────────────────
    def select_all(self) -> None:
        for page in self.pages:
            if page.get("id"):
                self.page_selection_states[self.page_key(page)] = True
        self._refresh_display()

    def clear_selection(self) -> None:
        for page in self.pages:
            if page.get("id"):
                self.page_selection_states[self.page_key(page)] = False
        self._refresh_display()

    def remove_pages(self, keys: List[str]) -> None:
        import math

        key_set = {str(k) for k in (keys or []) if k}
        if not key_set:
            return
        self.pages = [p for p in self.pages if self.page_key(p) not in key_set]
        for key in key_set:
            self.page_selection_states.pop(key, None)
            self.page_post_statuses.pop(key, None)
        total_pages = max(1, math.ceil(len(self.pages) / self.page_size))
        self.current_page = max(1, min(self.current_page, total_pages))
        self._refresh_display()

    def filter_pages(self, query: str) -> None:
        q = (query or "").strip().lower()
        if not q:
            self._refresh_display()
            return
        filtered = [p for p in self.pages if q in str(p.get("name", "")).lower() or q in str(p.get("id", "")).lower() or q in str(p.get("account_label", "")).lower()]
        self._view.populate_page_table(filtered)

    def get_token_input(self) -> str:
        return self._main_view.get_token_input()

    def get_selected_pages(self, use_all: bool = False) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for page in self.pages:
            key = self.page_key(page)
            if use_all or self.page_selection_states.get(key, False):
                selected.append({
                    "key": key,
                    "account": page.get("account_label", ""),
                    "id": page.get("id", ""),
                    "name": page.get("name", ""),
                    "access_token": page.get("access_token", ""),
                })
        return selected

    def resolve_page(self, page_name: str) -> Optional[Dict[str, Any]]:
        return self._page_service.resolve_page(self.pages, page_name)

    def history_page_key(self, page_value: str) -> str:
        return self._page_service.history_page_key(self.pages, page_value)

    def refresh_page_infos(self) -> None:
        import threading
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        paged = self.pages[start:end]

        def callback(page_id: str, info: dict) -> None:
            self._view.update_page_info(page_id, info)

        threading.Thread(target=self._page_service.fetch_page_infos, args=(paged, callback), daemon=True).start()

    def go_prev(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self._refresh_display()

    def go_next(self) -> None:
        import math
        total_pages = max(1, math.ceil(len(self.pages) / self.page_size))
        if self.current_page < total_pages:
            self.current_page += 1
            self._refresh_display()

    def set_page_size(self, size: int) -> None:
        self.page_size = size
        self.current_page = 1
        self._refresh_display()

    def _refresh_display(self) -> None:
        import math
        total_pages = max(1, math.ceil(len(self.pages) / self.page_size))
        self._view.populate_page_table(self.pages)
        self._view.update_pagination(self.current_page, total_pages)
        total = len(self.pages)
        selected = sum(1 for p in self.pages if self.page_selection_states.get(self.page_key(p), False))
        self._view.update_selection_summary(selected, total)
