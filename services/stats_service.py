import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("facebook_ui")

# Metric lượt xem video ở cấp Page (cần quyền read_insights).
PAGE_VIDEO_VIEW_METRICS = ["page_video_views", "page_video_views_organic"]
# Metric ở cấp từng video (dự phòng, cho tổng view trọn đời).
VIDEO_INSIGHT_METRICS = [
    "total_video_views",
    "post_video_views",
    "blue_reels_play_count",
    "video_views",
    "post_video_impressions",
]
# Số video tối đa lấy về để đếm + cộng view (tránh chạy quá lâu).
MAX_VIDEOS = 200


class StatsService:
    """Thống kê số video và lượt xem theo từng page.

    - Video trên page + View video: lấy từ Facebook (số thật trên page).
    - Video đã đăng (app): đếm từ lịch sử đăng trong app.
    """

    def __init__(self, stats_repo: Any, base_url: str = "", timeout: int = 20) -> None:
        self._repo = stats_repo
        self._base_url = (base_url or "https://graph.facebook.com/v25.0").rstrip("/")
        self._timeout = timeout

    # ── Local (không cần mạng) ────────────────────────────────────
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
                "page_video_count": stat.get("video_count"),
                "app_video_count": counts.get(key, 0),
                "video_views": stat.get("video_views"),
                "followers": stat.get("followers_count"),
                "fan_count": stat.get("fan_count"),
                "updated_at": str(stat.get("updated_at") or "")[:19].replace("T", " "),
            })
        return rows

    # ── HTTP ──────────────────────────────────────────────────────
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

    # ── Facebook ──────────────────────────────────────────────────
    def _page_counts(self, page_id: str, token: str) -> Dict[str, Optional[int]]:
        payload, error = self._get(f"/{page_id}", {"access_token": token, "fields": "followers_count,fan_count"})
        if error is not None or not isinstance(payload, dict):
            return {"followers_count": None, "fan_count": None}
        return {"followers_count": payload.get("followers_count"), "fan_count": payload.get("fan_count")}

    def _page_videos(self, page_id: str, token: str, max_videos: int = MAX_VIDEOS) -> List[str]:
        """Danh sách id video thật trên page (khử trùng, gộp cả reels)."""
        ids: List[str] = []
        seen = set()
        for edge in ("videos", "video_reels"):
            after = None
            while len(ids) < max_videos:
                params: Dict[str, Any] = {"access_token": token, "fields": "id", "limit": 100}
                if after:
                    params["after"] = after
                payload, error = self._get(f"/{page_id}/{edge}", params)
                if error is not None or not isinstance(payload, dict):
                    logger.info("Không lấy được '%s' của page %s: %s", edge, page_id, error)
                    break
                data = payload.get("data", []) or []
                for item in data:
                    video_id = str(item.get("id")) if isinstance(item, dict) and item.get("id") else ""
                    if video_id and video_id not in seen:
                        seen.add(video_id)
                        ids.append(video_id)
                after = (payload.get("paging", {}) or {}).get("cursors", {}).get("after")
                if not data or not after:
                    break
        logger.info("Page %s: tìm thấy %d video trên page", page_id, len(ids))
        return ids[:max_videos]

    def _video_views(self, token: str, video_id: str) -> Optional[int]:
        """Lấy view của 1 video; thử nhiều metric và chọn giá trị lớn nhất (khác 0)."""
        best: Optional[int] = None
        for metric in VIDEO_INSIGHT_METRICS:
            payload, error = self._get(f"/{video_id}/video_insights", {"access_token": token, "metric": metric})
            if error is not None:
                continue
            value = self._sum_insight_values(payload)
            if value is None:
                continue
            if best is None or value > best:
                best = value
            logger.debug("video %s metric %s = %s", video_id, metric, value)
        if best is not None:
            logger.info("video %s: view = %s", video_id, best)
        else:
            logger.info("video %s: không lấy được metric view", video_id)
        return best

    def _sum_video_views(self, token: str, video_ids: List[str]) -> Optional[int]:
        total = 0
        found = False
        for video_id in video_ids:
            value = self._video_views(token, video_id)
            if value is not None:
                total += value
                found = True
        return total if found else None

    def _page_video_views(self, page_id: str, token: str) -> Optional[int]:
        """Dự phòng: tổng view video ở cấp Page, thử nhiều khoảng thời gian."""
        attempts = [
            {"period": "day", "date_preset": "last_month"},
            {"period": "day", "date_preset": "last_90d"},
            {"period": "day", "date_preset": "maximum"},
            {"period": "day"},
        ]
        for extra in attempts:
            params = {"access_token": token, "metric": ",".join(PAGE_VIDEO_VIEW_METRICS)}
            params.update(extra)
            payload, error = self._get(f"/{page_id}/insights", params)
            if error is None:
                total = self._sum_insight_values(payload)
                if total is not None:
                    return total
        for metric in PAGE_VIDEO_VIEW_METRICS:
            payload, error = self._get(
                f"/{page_id}/insights",
                {"access_token": token, "metric": metric, "period": "day", "date_preset": "last_month"},
            )
            if error is None:
                total = self._sum_insight_values(payload)
                if total is not None:
                    return total
        return None

    def refresh(self, pages: List[Dict[str, Any]], on_page: Optional[Callable[[Dict[str, Any]], None]] = None) -> List[Dict[str, Any]]:
        app_counts = self.local_video_counts()
        rows: List[Dict[str, Any]] = []
        for page in pages or []:
            page_id = str(page.get("id") or "")
            page_name = page.get("name") or page_id
            page_key = str(page_name)
            token = page.get("access_token") or ""

            app_video_count = app_counts.get(page_key, 0)
            page_video_count: Optional[int] = None
            video_views: Optional[int] = None
            followers: Optional[int] = None
            fan_count: Optional[int] = None

            if token and page_id:
                counts = self._page_counts(page_id, token)
                followers = counts.get("followers_count")
                fan_count = counts.get("fan_count")

                video_ids = self._page_videos(page_id, token)
                if video_ids:
                    page_video_count = len(video_ids)
                    video_views = self._sum_video_views(token, video_ids)
                if video_views is None:
                    video_views = self._page_video_views(page_id, token)

            self._repo.save_page_stats(
                page_key, page_name,
                page_video_count or 0, video_views, followers, fan_count,
            )

            status = "ok"
            status_text = "Thành công"
            if not token:
                status, status_text = "error", "Thiếu token"
            elif page_video_count is None and video_views is None and followers is None and fan_count is None:
                status, status_text = "error", "Không lấy được dữ liệu"

            row = {
                "page": page_name,
                "page_key": page_key,
                "page_video_count": page_video_count,
                "app_video_count": app_video_count,
                "video_views": video_views,
                "followers": followers,
                "fan_count": fan_count,
                "status": status,
                "status_text": status_text,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "ok" else "",
            }
            rows.append(row)
            if on_page:
                on_page(row)
        return rows
