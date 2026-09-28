from typing import Any, Dict, List

from di.interfaces import IMainView, ISettingsView
from services.page_service import PageService


class SettingsPresenter:
    def __init__(self, view: ISettingsView, main_view: IMainView, page_service: PageService) -> None:
        self._view = view
        self._main_view = main_view
        self._page_service = page_service

    def check_tokens(self, tokens: List[str]) -> None:
        import threading

        def worker():
            for token in tokens:
                validation = self._page_service.validate_token(token)
                self._page_service._token_repo.save_token_state(token, validation.get("valid", False), None, validation.get("error"))
            self._main_view.show_info("Thông báo", "Đã kiểm tra xong tất cả token.")

        threading.Thread(target=worker, daemon=True).start()

    def refresh_tokens(self, tokens: List[str], invalid_tokens: List[str], new_tokens: List[str]) -> List[str]:
        updated = list(tokens)
        for idx, invalid_token in enumerate(invalid_tokens):
            replacement = new_tokens[idx] if idx < len(new_tokens) else new_tokens[-1]
            try:
                updated[updated.index(invalid_token)] = replacement
            except ValueError:
                continue
        return updated

    def get_proxy_config(self) -> Dict[str, Any]:
        return self._view.get_proxy_config()

    def get_concurrency_config(self) -> Dict[str, Any]:
        return self._view.get_concurrency_config()
