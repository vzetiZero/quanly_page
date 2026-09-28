import logging
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("facebook_ui")

# Metric lượt xem video ở cấp Page (cần quyền read_insights).
PAGE_VIDEO_VIEW_METRICS = ["page_video_views", "page_video_views_organic", "page_post_engagements"]
# Metric ở cấp từng video (dự phòng).
VIDEO_INSIGHT_METRICS = ["total_video_views", "post_video_views", "video_views"]


class StatsService:
    """Thống kê số video đã đăng và lượt xem theo từng page."""

    def __init__(self, stats_repo: Any, base_url: str = "", timeout: int = 20) -> None:
        self._repo = stats_repo
        self._base_url = (base_url or "https://graph.facebook.com/v25.0").rstrip("/")
        self._timeout = timeout

    # ── Local (luôn chính xác, không cần mạng) ────────────────────
    def local_video_counts(self) -> Dict[str, int]:
        return self._repo.count_posted_videos_by_page()

    def load_saved_stats(self) -> List[Dict[str, Any]]:
        return self._repo.load_all_page_stats()

    def local_rows(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        counts = self.local_video_counts()
        saved = {str(r.get("page_key")): r for r in self.load_saved_stats()}
        rows = []
        for page in pages or []:
            key = str(page.get("name") or page.get("id") or "")
            stat = saved.get(key, {})
            rows.append({
                "page": page.get("name") or key,
                "page_key": key,
                "video_count": counts.get(key, 0),
                "video_views": stat.get("video_views"),
                "followers": stat.get("followers_count"),
                "fan_count": stat.get("fan_count"),
                "updated_at": str(stat.get("updated_at") or "")[:19].replace("T", " "),
            })
        return rows

    # ── Facebook (best-effort) ────────────────────────────────────
    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        try:
            response = requests.get(f"{self._base_url}{path}", params=params, timeout=self._timeout)
            payload = response.json()
        except Exception as exc:
            return None, str(exc)
        if response.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            return None, error.get("message") or f"status={response.status_code}"
        return payload, None

    @staticmethod
    def _sum_insight_values(payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        total = 0
        found = False
        for metric in payload.get("data", []) or []:
            for value in metric.get("values", []) or []:
                data_value = value.get("value") if isinstance(value, dict) else value
                if isinstance(data_value, (int, float)):
                    total += int(data_value)
                    found = True
        return total if found else None

    def _page_video_views(self, page_id: str, token: str) -> Optional[int]:
        payload, error = self._get(
            f"/{page_id}/insights",
            {"access_token": token, "metric": ",".join(PAGE_VIDEO_VIEW_METRICS), "period": "day"},
        )
        if error is None:
            total = self._sum_insight_values(payload)
            if total is not None:
                return total
        for metric in PAGE_VIDEO_VIEW_METRICS:
            payload, error = self._get(
                f"/{page_id}/insights",
                {"access_token": token, "metric": metric, "period": "day"},
            )
            if error is None:
                total = self._sum_insight_values(payload)
                if total is not None:
                    return total
        return None

    def _sum_video_views(self, token: str, posted_ids: List[str]) -> Optional[int]:
        total = 0
        found = False
        for video_id in posted_ids:
            for metric in VIDEO_INSIGHT_METRICS:
                payload, error = self._get(
                    f"/{video_id}/video_insights",
                    {"access_token": token, "metric": metric},
                )
                if error is None:
                    value = self._sum_insight_values(payload)
                    if value is not None:
                        total += value
                        found = True
                        break
        return total if found else None

    def _page_counts(self, page_id: str, token: str) -> Dict[str, Optional[int]]:
        payload, error = self._get(
            f"/{page_id}",
            {"access_token": token, "fields": "followers_count,fan_count"},
        )
        if error is not None or not isinstance(payload, dict):
            return {"followers_count": None, "fan_count": None}
        return {
            "followers_count": payload.get("followers_count"),
            "fan_count": payload.get("fan_count"),
        }

    def refresh(self, pages: List[Dict[str, Any]], on_page: Optional[Callable[[Dict[str, Any]], None]] = None) -> List[Dict[str, Any]]:
        counts = self.local_video_counts()
        rows: List[Dict[str, Any]] = []
        for page in pages or []:
            page_id = str(page.get("id") or "")
            page_name = page.get("name") or page_id
            page_key = str(page_name)
            token = page.get("access_token") or ""

            video_count = counts.get(page_key, 0)
            followers_count = None
            fan_count = None
            video_views = None

            if token and page_id:
                page_counts = self._page_counts(page_id, token)
                followers_count = page_counts.get("followers_count")
                fan_count = page_counts.get("fan_count")
                video_views = self._page_video_views(page_id, token)
                if video_views is None:
                    posted_ids = [item.get("post_id") for item in self._repo.load_posted_video_ids(page_key)]
                    posted_ids = [pid for pid in posted_ids if pid]
                    if posted_ids:
                        video_views = self._sum_video_views(token, posted_ids)

            self._repo.save_page_stats(page_key, page_name, video_count, video_views, followers_count, fan_count)
            row = {
                "page": page_name,
                "page_key": page_key,
                "video_count": video_count,
                "video_views": video_views,
                "followers": followers_count,
                "fan_count": fan_count,
                "updated_at": "",
            }
            rows.append(row)
            if on_page:
                on_page(row)
        return rows
