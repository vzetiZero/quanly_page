import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests

logger = logging.getLogger("facebook_ui")

# Field còn hợp lệ trên Page (Graph API hiện hành).
PAGE_BASIC_FIELDS = [
    "id", "name", "category", "about", "description", "phone", "website",
    "link", "verification_status", "is_published",
    "cover", "emails", "location", "instagram_business_account",
]
# Field đếm cần Page Access Token + pages_read_engagement.
PAGE_COUNT_FIELDS = ["fan_count", "followers_count", "talking_about_count", "new_like_count"]

# Các field Meta đã BỎ (nếu đưa vào 1 request sẽ làm hỏng cả request) -> không request.
PAGE_REMOVED_FIELDS = ["overall_star_rating", "rating_count", "were_here_count", "engagement"]

# Metric insights còn dùng được; sẽ thử từng metric để 1 metric hỏng không kéo theo tất cả.
PAGE_INSIGHT_METRICS = [
    "page_impressions",
    "page_impressions_unique",
    "page_engaged_users",
    "page_post_engagements",
    "page_fans",
]


class PageDetailFetcher:
    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Helpers ───────────────────────────────────────────────────
    @staticmethod
    def _extract_error(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            error = payload.get("error") or {}
            if isinstance(error, dict):
                return {
                    "message": error.get("message", ""),
                    "code": error.get("code"),
                    "subcode": error.get("error_subcode"),
                    "type": error.get("type"),
                }
        return {"message": str(payload)[:200]}

    def _get(self, path: str, params: Dict[str, Any]) -> Tuple[Any, Any]:
        try:
            response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        except Exception as exc:
            return None, {"message": str(exc)}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            return None, self._extract_error(payload)
        return payload, None

    def _fetch_fields(self, page_id: str, access_token: str, fields: List[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Lấy cả nhóm field; nếu lỗi (do field đã bị bỏ) thì lấy từng field để giữ lại cái hợp lệ."""
        fields = [f for f in fields if f]
        merged: Dict[str, Any] = {}
        errors: List[Dict[str, Any]] = []
        if not fields:
            return merged, errors

        payload, error = self._get(f"/{page_id}", {"access_token": access_token, "fields": ",".join(fields)})
        if error is None:
            return payload or {}, errors

        logger.info("Nhóm field lỗi cho page %s (%s) -> thử từng field", page_id, error.get("message", ""))
        for field in fields:
            payload, error = self._get(f"/{page_id}", {"access_token": access_token, "fields": field})
            if error is None:
                merged.update(payload or {})
            else:
                errors.append({"field": field, "message": error.get("message", ""), "code": error.get("code")})
        return merged, errors

    # ── Public ────────────────────────────────────────────────────
    def fetch_page_info(self, page_id: str, access_token: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "page_id": page_id,
            "page_name": "",
            "fetched_at": datetime.now().isoformat(),
        }
        errors: List[Dict[str, Any]] = []

        basic_raw, basic_errors = self._fetch_fields(page_id, access_token, PAGE_BASIC_FIELDS)
        result.update(self._parse_page_info(basic_raw))
        errors.extend(basic_errors)

        count_raw, count_errors = self._fetch_fields(page_id, access_token, PAGE_COUNT_FIELDS)
        result.update(self._parse_page_info(count_raw))
        errors.extend(count_errors)

        if errors:
            result["_errors"] = errors
            for item in errors:
                logger.debug("Page field '%s' error: %s", item.get("field"), item.get("message"))
        return result

    def fetch_page_insights(self, page_id: str, access_token: str) -> Dict[str, Any]:
        # Thử gộp trước cho nhanh.
        payload, error = self._get(
            f"/{page_id}/insights",
            {"access_token": access_token, "metric": ",".join(PAGE_INSIGHT_METRICS), "period": "day", "limit": 1},
        )
        if error is None:
            return self._parse_insights(payload or {})

        # Fallback: thử từng metric (bỏ qua metric bị deprecate/thiếu quyền).
        merged: Dict[str, Any] = {}
        for metric in PAGE_INSIGHT_METRICS:
            payload, error = self._get(
                f"/{page_id}/insights",
                {"access_token": access_token, "metric": metric, "period": "day", "limit": 1},
            )
            if error is None:
                merged.update(self._parse_insights(payload or {}))
        if not merged:
            logger.info("Insights không lấy được cho page %s: %s", page_id, error.get("message") if error else "")
        return merged

    def fetch_all(self, page_id: str, page_name: str, access_token: str) -> Dict[str, Any]:
        info = self.fetch_page_info(page_id, access_token)
        insights = self.fetch_page_insights(page_id, access_token)

        result: Dict[str, Any] = {
            "page_id": page_id,
            "fetched_at": datetime.now().isoformat(),
        }
        result.update(info)
        result.update(insights)
        # Tên page: ưu tiên tên đang hiển thị của app, nếu trống mới lấy từ API.
        result["page_id"] = page_id
        result["page_name"] = page_name or info.get("name") or info.get("page_name") or ""
        result.setdefault("_errors", info.get("_errors", []))
        if not insights:
            result["_errors"].append({
                "field": "insights (lượt xem)",
                "message": "Thiếu quyền read_insights hoặc metric đã bị Meta bỏ",
            })
        return result

    # ── Parsers ───────────────────────────────────────────────────
    def _parse_page_info(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        for field in ("id", "name", "category", "about", "description", "phone", "website", "verification_status", "link", "created_time"):
            value = data.get(field)
            if value is not None:
                result[field] = str(value) if not isinstance(value, (int, float, bool)) else value

        if data.get("name") is not None:
            result["page_name"] = data.get("name")
        result["is_published"] = data.get("is_published")

        location = data.get("location")
        if isinstance(location, dict):
            parts = [location.get("city", ""), location.get("country", ""), location.get("street", "")]
            result["location"] = ", ".join(p for p in parts if p)
        elif isinstance(location, str):
            result["location"] = location

        emails = data.get("emails")
        if isinstance(emails, list):
            result["emails"] = ", ".join(str(e) for e in emails)
        elif isinstance(emails, str):
            result["emails"] = emails

        for count_field in ("fan_count", "followers_count", "talking_about_count", "new_like_count", "rating_count"):
            value = data.get(count_field)
            if value is not None:
                try:
                    result[count_field] = int(value)
                except (ValueError, TypeError):
                    pass

        star_rating = data.get("overall_star_rating")
        if star_rating is not None:
            try:
                result["overall_star_rating"] = float(star_rating)
            except (ValueError, TypeError):
                pass

        cover = data.get("cover")
        if isinstance(cover, dict):
            result["cover_url"] = cover.get("source", "")
        elif isinstance(cover, str):
            result["cover_url"] = cover

        picture = data.get("picture")
        if isinstance(picture, dict):
            data_obj = picture.get("data", {})
            if isinstance(data_obj, dict):
                result["logo_url"] = data_obj.get("url", "")

        ig = data.get("instagram_business_account")
        if isinstance(ig, dict):
            result["instagram_business_account_id"] = ig.get("id", "")
            result["instagram_business_account_username"] = ig.get("username", "")

        engagement = data.get("engagement")
        if isinstance(engagement, dict):
            count = engagement.get("count")
            if count is not None:
                result["likes_count"] = count

        return result

    def _parse_insights(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        metrics = data.get("data") or []
        if not isinstance(metrics, list):
            return result

        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            name = metric.get("name", "")
            values = metric.get("values") or []
            if not values or not isinstance(values, list):
                continue
            latest = values[-1]
            if isinstance(latest, dict):
                value = latest.get("value")
            else:
                value = latest
            if value is not None:
                result[f"insight_{name}"] = value

        return result
