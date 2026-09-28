import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from di.interfaces import IPageRepository, ITokenRepository

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


class PageService:
    def __init__(
        self,
        page_repo: IPageRepository,
        token_repo: ITokenRepository,
        base_url: str = "",
    ) -> None:
        self._page_repo = page_repo
        self._token_repo = token_repo
        self._base_url = base_url or "https://graph.facebook.com/v25.0"

    def validate_token(self, token: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self._base_url}/me",
                params={"access_token": token, "fields": "id,name"},
                timeout=25,
            )
            payload = response.json()
            if response.status_code in {400, 401, 403}:
                error_msg = payload.get("error", {}).get("message", "Token invalid")
                return {"valid": False, "error": error_msg, "expires_at": None, "account_id": None, "account_name": None}
            if payload.get("id"):
                return {"valid": True, "error": None, "expires_at": None, "account_id": payload.get("id"), "account_name": payload.get("name")}
            return {"valid": False, "error": "Không thể xác thực token", "expires_at": None, "account_id": None, "account_name": None}
        except Exception as exc:
            return {"valid": False, "error": str(exc), "expires_at": None, "account_id": None, "account_name": None}

    def fetch_pages(self, token: str) -> List[Dict[str, Any]]:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        pages: List[Dict[str, Any]] = []
        fields = "id,name,access_token,tasks,category,location,about,phone,website,instagram_business_account"

        try:
            account_pages = _fetch_paginated_graph_data(session, f"{self._base_url}/me/accounts", {"access_token": token, "fields": fields})
            for page in account_pages:
                pages.append({
                    "id": page.get("id"), "name": page.get("name"), "access_token": page.get("access_token", ""),
                    "tasks": page.get("tasks", []), "page_type": "me/accounts", "status": "Valid",
                })
        except Exception as exc:
            logger.warning("Không thể lấy pages từ me/accounts: %s", exc)

        try:
            businesses = _fetch_paginated_graph_data(session, f"{self._base_url}/me/businesses", {"access_token": token, "fields": "id,name"})
            for business in businesses:
                business_id = business.get("id")
                business_name = business.get("name") or "Business Manager"
                if not business_id:
                    continue
                for endpoint in ("owned_pages", "client_pages"):
                    try:
                        business_pages = _fetch_paginated_graph_data(session, f"{self._base_url}/{business_id}/{endpoint}", {"access_token": token, "fields": fields})
                        for page in business_pages:
                            pages.append({
                                "id": page.get("id"), "name": page.get("name"), "access_token": page.get("access_token", ""),
                                "tasks": page.get("tasks", []), "page_type": endpoint,
                                "business_name": business_name, "business_id": business_id, "status": "Valid",
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

    def load_all_from_cache(self) -> List[Dict[str, Any]]:
        return self._dedupe_pages(self._page_repo.load_all_cached_pages())

    @staticmethod
    def _dedupe_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Gộp các page trùng page_id (cùng page qua nhiều token) thành 1 dòng.

        Giữ bản có access_token và last_updated mới nhất (token mới nhất thắng),
        nhờ vậy khi nạp token mới sẽ không nhân bản danh sách page.
        """
        best: Dict[str, tuple] = {}
        order: List[str] = []
        for page in pages:
            page_id = str(page.get("id") or "")
            if not page_id:
                continue
            score = (1 if page.get("access_token") else 0, str(page.get("last_updated") or ""))
            if page_id not in best:
                best[page_id] = (score, page)
                order.append(page_id)
            elif score > best[page_id][0]:
                best[page_id] = (score, page)
        return [best[page_id][1] for page_id in order]

    def load_pages_for_token(self, token: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        cached_pages = self._page_repo.load_cached_pages(token)
        if cached_pages:
            self._token_repo.save_token_state(token, True, None, None)
            validation = self.validate_token(token)
            if validation.get("valid"):
                pages = self.fetch_pages(token)
                self._page_repo.save_pages(token, pages)
                self._token_repo.save_token_state(token, True, validation.get("expires_at"), None)
                return self._page_repo.load_cached_pages(token), {
                    "valid": True, "status": "Cache", "error": "",
                    "expires_at": validation.get("expires_at"),
                    "account_id": validation.get("account_id"),
                    "account_name": validation.get("account_name"),
                }
            self._token_repo.save_token_state(token, False, validation.get("expires_at"), validation.get("error"))
            return cached_pages, {
                "valid": False, "status": "Lỗi", "error": validation.get("error", "Token invalid"),
                "expires_at": validation.get("expires_at"),
                "account_id": validation.get("account_id"),
                "account_name": validation.get("account_name"),
            }

        validation = self.validate_token(token)
        if not validation["valid"]:
            self._token_repo.save_token_state(token, False, validation.get("expires_at"), validation.get("error"))
            return [], {
                "valid": False, "status": "Lỗi", "error": validation.get("error", "Token invalid"),
                "expires_at": validation.get("expires_at"),
                "account_id": validation.get("account_id"),
                "account_name": validation.get("account_name"),
            }

        pages = self.fetch_pages(token)
        self._page_repo.save_pages(token, pages)
        self._token_repo.save_token_state(token, True, validation.get("expires_at"), None)
        return self._page_repo.load_cached_pages(token), {
            "valid": True, "status": "Valid", "error": "",
            "expires_at": validation.get("expires_at"),
            "account_id": validation.get("account_id"),
            "account_name": validation.get("account_name"),
        }

    def fetch_page_infos(self, pages: List[Dict[str, Any]], callback: Callable[[str, Dict[str, Any]], None]) -> None:
        def worker(page: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
            page_id = str(page.get("id", "") or "")
            access_token = page.get("access_token") or ""
            result: Dict[str, Any] = {"followers": "N/A", "views": "N/A"}
            if not page_id or not access_token:
                return page_id, result
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0"})
            try:
                resp = session.get(f"{self._base_url}/{page_id}", params={"access_token": access_token, "fields": "fan_count,followers_count"}, timeout=15)
                payload = resp.json()
                followers = payload.get("fan_count") or payload.get("followers_count")
                if isinstance(followers, (int, float)):
                    result["followers"] = str(int(followers))
            except Exception:
                pass
            try:
                resp = session.get(f"{self._base_url}/{page_id}/insights", params={"access_token": access_token, "metric": "page_impressions", "period": "day", "limit": 1}, timeout=15)
                payload = resp.json()
                data = payload.get("data") or []
                for metric in data:
                    values = metric.get("values") or []
                    if values:
                        latest = values[-1]
                        v = latest.get("value") if isinstance(latest, dict) else latest
                        if v is not None:
                            result["views"] = str(int(v)) if isinstance(v, (int, float)) else str(v)
                            break
            except Exception:
                pass
            return page_id, result

        max_workers = min(8, max(1, len(pages)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(worker, page): page for page in pages}
            for future in as_completed(futures):
                try:
                    page_id, info = future.result()
                    callback(page_id, info)
                except Exception:
                    pass

    def resolve_page(self, pages: List[Dict[str, Any]], page_name: str) -> Optional[Dict[str, Any]]:
        for page in pages:
            if page.get("name") == page_name or page.get("id") == page_name:
                return page
        return None

    def resolve_page_id(self, pages: List[Dict[str, Any]], page_value: str) -> str:
        value = (page_value or "").strip()
        if not value:
            return ""
        for page in pages:
            if str(page.get("id", "")).strip() == value:
                return str(page.get("id", "")).strip()
            if str(page.get("name", "")).strip() == value:
                return str(page.get("id", "")).strip()
        return ""

    def history_page_key(self, pages: List[Dict[str, Any]], page_value: str) -> str:
        page_id = self.resolve_page_id(pages, page_value)
        return page_id or str(page_value or "").strip()
