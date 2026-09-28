import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("facebook_ui")

PAGE_DETAIL_FIELDS = ",".join([
    "id", "name", "category", "about", "description", "phone", "website",
    "location", "emails", "fan_count", "followers_count", "talking_about_count",
    "were_here_count", "overall_star_rating", "rating_count",
    "cover", "verification_status", "is_published", "link", "created_time",
    "instagram_business_account", "engagement",
])

PAGE_INSIGHT_FIELDS = "page_views_total,page_impressions,page_engaged_users,page_post_impressions_total"


class PageDetailFetcher:
    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_page_info(self, page_id: str, access_token: str) -> Dict[str, Any]:
        BASIC_FIELDS = "id,name,category,about,phone,website,link,verification_status,is_published"

        try:
            response = requests.get(
                f"{self.base_url}/{page_id}",
                params={"access_token": access_token, "fields": BASIC_FIELDS},
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                logger.warning("Page info basic request failed for %s: %s", page_id, response.status_code)
                return {"page_id": page_id, "page_name": "", "fetched_at": datetime.now().isoformat()}
            data = response.json()
            result = self._parse_page_info(data)
        except Exception as exc:
            logger.warning("Không thể lấy page info cơ bản cho %s: %s", page_id, exc)
            return {"page_id": page_id, "page_name": "", "fetched_at": datetime.now().isoformat()}

        EXTRA_FIELDS = "followers_count,fan_count,talking_about_count,were_here_count,overall_star_rating,rating_count,location,emails,cover,instagram_business_account,engagement"
        try:
            response = requests.get(
                f"{self.base_url}/{page_id}",
                params={"access_token": access_token, "fields": EXTRA_FIELDS},
                timeout=self.timeout,
            )
            if response.status_code < 400:
                extra_data = response.json()
                result.update(self._parse_page_info(extra_data))
        except Exception:
            pass

        return result

    def fetch_page_insights(self, page_id: str, access_token: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.base_url}/{page_id}/insights",
                params={
                    "access_token": access_token,
                    "metric": PAGE_INSIGHT_FIELDS,
                    "period": "day",
                    "limit": 7,
                },
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                logger.warning("Insights request failed for %s: %s (có thể do thiếu quyền)", page_id, response.status_code)
                return {}
            data = response.json()
            return self._parse_insights(data)
        except Exception as exc:
            logger.warning("Không thể lấy insights cho %s: %s", page_id, exc)
            return {}

    def fetch_all(self, page_id: str, page_name: str, access_token: str) -> Dict[str, Any]:
        info = self.fetch_page_info(page_id, access_token)
        insights = self.fetch_page_insights(page_id, access_token)

        result: Dict[str, Any] = {
            "page_id": page_id,
            "page_name": page_name or info.get("name", ""),
            "fetched_at": datetime.now().isoformat(),
        }
        result.update(info)
        result.update(insights)
        return result

    def _parse_page_info(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        for field in ("id", "name", "category", "about", "description", "phone", "website", "verification_status", "link", "created_time"):
            value = data.get(field)
            if value is not None:
                result[field] = str(value) if not isinstance(value, (int, float, bool)) else value

        result["page_name"] = data.get("name", "")
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

        for count_field in ("fan_count", "followers_count", "talking_about_count", "were_here_count", "rating_count"):
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
            result["likes_count"] = engagement.get("count")

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
