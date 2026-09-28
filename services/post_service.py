import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from di.interfaces import IPostRepository, IProxyProvider, IReelsUploader

logger = logging.getLogger("facebook_ui")


def _is_facebook_block_error(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in [
        "spam", "rate limit", "temporarily blocked", "temporarily limited",
        "too many requests", "anti-spam", "repeated requests",
    ])


def _retry_delay_for_attempt(attempt: int, base_delay: int = 10) -> int:
    return base_delay * max(1, attempt)


def chunked_batches(items: list, size: int) -> list:
    if size <= 1:
        return [list(items)]
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


class PostService:
    def __init__(
        self,
        post_repo: IPostRepository,
        reels_uploader: IReelsUploader,
        proxy_provider: IProxyProvider,
    ) -> None:
        self._post_repo = post_repo
        self._reels_uploader = reels_uploader
        self._proxy_provider = proxy_provider
        self.post_retry_count = 2
        self.post_retry_delay_base = 10
        self.stop_requested = False

    def _build_public_post_payload(self, page: Dict[str, Any], message: str, *, title: Optional[str] = None, description: Optional[str] = None) -> Dict[str, str]:
        payload: Dict[str, str] = {
            "access_token": page["access_token"],
            "message": message,
            "published": "true",
            "is_published": "true",
            "privacy": '{"value":"EVERYONE"}',
        }
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description
        return payload

    def _extract_post_id(self, response_data: Dict[str, Any]) -> Optional[str]:
        if not isinstance(response_data, dict):
            return None
        return response_data.get("id") or response_data.get("post_id")

    # ── Chia video cho page (dùng tên video làm khoá) ────────────

    def load_posted_video_names(self, page_key: str) -> set:
        """Tên các video đã đăng thành công cho 1 page."""
        if not page_key:
            return set()
        try:
            return set(self._post_repo.load_posted_video_names_for_page(str(page_key)) or set())
        except Exception:
            return set()

    def summarize_posted_for_pages(
        self,
        videos: List[str],
        pages: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Với mỗi video, đếm xem đã được đăng ở bao nhiêu page trong danh sách chọn."""
        page_keys = [str(p.get("name") or p.get("id") or "") for p in pages]
        posted_by_page = {key: self.load_posted_video_names(key) for key in page_keys if key}
        counts: Dict[str, int] = {}
        for video_path in videos or []:
            video_name = Path(video_path).name
            counts[video_path] = sum(1 for key in page_keys if video_name in posted_by_page.get(key, set()))
        return counts

    def plan_video_assignment(
        self,
        pages: List[Dict[str, Any]],
        video_paths: List[str],
    ) -> List[Dict[str, Any]]:
        """Chia video cho từng page sao cho mỗi page đều có video mới để đăng.

        Thuật toán:
        - Duyệt page theo thứ tự, mỗi page bắt đầu tại vị trí ``index`` trong
          danh sách video (round-robin) để phân bổ đều.
        - Bỏ qua các video mà page đó đã đăng (so khớp theo TÊN VIDEO).
        - Nếu còn video chưa đăng -> gán video đầu tiên tìm được.
        - Nếu page đã đăng hết toàn bộ video đang chọn -> đánh dấu
          "Hết video mới" và không đăng.
        """
        entries: List[Dict[str, Any]] = []
        candidates = [str(p) for p in (video_paths or []) if p]
        total = len(candidates)

        if not pages:
            return entries

        if total == 0:
            for page in pages:
                entries.append(self._assignment_entry(page, "", "Không có video"))
            return entries

        for index, page in enumerate(pages):
            page_key = str(page.get("name") or page.get("id") or "")
            posted_names = self.load_posted_video_names(page_key)
            page_id = str(page.get("id") or "")
            if page_id and page_id != page_key:
                posted_names = posted_names | self.load_posted_video_names(page_id)

            start = index % total
            chosen_path = ""
            for step in range(total):
                candidate = candidates[(start + step) % total]
                if Path(candidate).name in posted_names:
                    continue
                chosen_path = candidate
                break

            if not chosen_path:
                entries.append(self._assignment_entry(page, "", "Hết video mới"))
            else:
                entries.append(self._assignment_entry(page, chosen_path, "Chờ đăng"))

        return entries

    @staticmethod
    def _assignment_entry(page: Dict[str, Any], video_path: str, status: str) -> Dict[str, Any]:
        return {
            "page_id": page.get("id", ""),
            "page_name": page.get("name") or page.get("id", ""),
            "video_path": video_path,
            "title": Path(video_path).stem if video_path else "",
            "description": "",
            "comment": "",
            "comment_images": "",
            "post_type": "video",
            "schedule_time": "",
            "status": status,
        }

    def _extract_facebook_error_message(self, response: Any, *, fallback: Optional[str] = None) -> str:
        if response is None:
            return fallback or "Unknown Facebook API error"
        text = (getattr(response, "text", "") or "").strip()
        payload: Any = {}
        if text:
            try:
                payload = response.json()
            except (ValueError, TypeError):
                payload = {}
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if message:
                    return str(message)
            if payload.get("message"):
                return str(payload["message"])
        if text:
            return text
        if fallback:
            return fallback
        return f"status={getattr(response, 'status_code', 'n/a')}"

    def post_text(self, page: Dict[str, Any], message: str, base_url: str) -> Dict[str, Any]:
        import requests
        response = requests.post(
            f"{base_url}/{page['id']}/feed",
            data=self._build_public_post_payload(page, message),
            timeout=60,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}

    def post_image(self, page: Dict[str, Any], message: str, image_path: str, base_url: str, proxy_config: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        import requests
        with open(image_path, "rb") as media_file:
            response = requests.post(
                f"{base_url}/{page['id']}/photos",
                data=self._build_public_post_payload(page, message),
                files={"source": media_file},
                timeout=120,
                proxies=proxy_config or None,
            )
        if response.status_code >= 400:
            msg = self._extract_facebook_error_message(response)
            raise RuntimeError(f"Đăng ảnh thất bại: status={response.status_code} message={msg}")
        try:
            return response.json()
        except ValueError:
            return {}

    def post_media(self, page: Dict[str, Any], message: str, media_path: str, base_url: str, media_type: str = "video", title: Optional[str] = None, proxy_config: Optional[Dict[str, str]] = None, post_type: Optional[str] = None) -> Dict[str, Any]:
        import requests
        page_name = page.get("name", page.get("id", ""))
        tasks = page.get("tasks")
        if isinstance(tasks, list) and tasks and "CREATE_CONTENT" not in tasks:
            raise RuntimeError(f"Page token không có task CREATE_CONTENT: tasks={tasks}")
        if not page.get("access_token"):
            raise RuntimeError("Thiếu Page Access Token.")

        for attempt in range(1, self.post_retry_count + 2):
            try:
                if media_type == "image":
                    return self.post_image(page, message, media_path, base_url, proxy_config)

                uploader = self._reels_uploader
                upload_result = uploader.start_upload(
                    page, media_path, message,
                    title=title or page.get("name") or "Video",
                    description=message, proxy_config=proxy_config,
                )
                upload_id = upload_result.get("id") or upload_result.get("video_id") or upload_result.get("upload_id")
                if not upload_id:
                    raise RuntimeError(f"Reels upload không trả về upload_id: {upload_result}")

                binary_result = uploader.upload_binary(page, upload_id, media_path, proxy_config=proxy_config)
                if binary_result.get("error"):
                    raise RuntimeError(f"Reels binary upload failed: {binary_result['error']}")

                publish_result = uploader.finish_publish(
                    page, upload_id, message=message,
                    title=title or page.get("name") or "Video",
                    description=message, proxy_config=proxy_config,
                )
                final_payload = uploader.verify_public_visibility(page, upload_id, timeout_seconds=300, proxy_config=proxy_config)
                final_payload = dict(final_payload or {})
                final_payload.setdefault("upload_id", upload_id)
                final_payload.setdefault("post_id", publish_result.get("post_id") if isinstance(publish_result, dict) else None)
                final_payload.setdefault("publish_result", publish_result)
                final_payload.setdefault("binary_result", binary_result)
                final_payload.setdefault("start_result", upload_result)
                final_payload.setdefault("permalink_url", uploader.get_permalink(page, upload_id, proxy_config=proxy_config))

                if final_payload.get("public_visibility") is False:
                    raise RuntimeError(f"Reels đã publish nhưng không public: {final_payload.get('public_visibility_reason') or 'unknown'}")
                return final_payload

            except requests.exceptions.RequestException as exc:
                if _is_facebook_block_error(str(exc)) and attempt <= self.post_retry_count:
                    time.sleep(_retry_delay_for_attempt(attempt, self.post_retry_delay_base))
                    continue
                raise RuntimeError(f"Đăng media thất bại: {exc}") from exc
            except Exception as exc:
                if attempt <= self.post_retry_count:
                    time.sleep(_retry_delay_for_attempt(attempt, self.post_retry_delay_base))
                    continue
                raise RuntimeError(f"Đăng media thất bại: {exc}") from exc

        raise RuntimeError(f"Đăng media thất bại sau {self.post_retry_count + 1} lần thử")

    def post_comment_with_retries(self, page: Dict[str, Any], post_id: str, comment_text: str, comment_image_paths: Optional[List[str]] = None, proxy_config: Optional[Dict[str, str]] = None, base_url: Optional[str] = None) -> None:
        import requests
        graph_base = (base_url or getattr(self._reels_uploader, "base_url", "") or "https://graph.facebook.com/v25.0").rstrip("/")
        for attempt in range(1, 4):
            try:
                normalized_text = (comment_text or "").strip() or "Thanks!"
                data: Dict[str, str] = {"access_token": page["access_token"], "message": normalized_text}
                files = []
                opened_files = []
                try:
                    for image_path in (comment_image_paths or []):
                        if not image_path:
                            continue
                        image_path_obj = Path(image_path)
                        if not image_path_obj.exists():
                            raise FileNotFoundError(f"File ảnh comment không tồn tại: {image_path}")
                        media_file = image_path_obj.open("rb")
                        opened_files.append(media_file)
                        files.append(("source", (image_path_obj.name, media_file, "application/octet-stream")))
                    response = requests.post(
                        f"{graph_base}/{post_id}/comments",
                        data=data, files=files or None, timeout=60, proxies=proxy_config or None,
                    )
                finally:
                    for handle in opened_files:
                        handle.close()
                if response.status_code >= 400:
                    raise RuntimeError(f"Comment thất bại: status={response.status_code}")
                return
            except Exception as exc:
                if attempt < 3:
                    time.sleep(3)
                else:
                    raise RuntimeError(f"Comment thất bại sau 3 lần thử cho post_id={post_id}") from exc

    def run_post_queue(
        self,
        pages: List[Dict[str, Any]],
        message: str,
        image_paths: Optional[List[str]],
        video_paths: Optional[List[str]],
        scheduled_time: Optional[datetime],
        post_type: str,
        base_url: str,
        concurrency_enabled: bool,
        concurrency_threads: int,
        concurrency_delay: float,
        on_status: Callable,
        on_complete: Callable,
    ) -> None:
        success_count = 0
        fail_count = 0
        use_concurrency = concurrency_enabled and len(pages) > 1
        max_workers = concurrency_threads if use_concurrency else 1

        def worker(page: Dict[str, Any]) -> None:
            nonlocal success_count, fail_count
            page_id = page["id"]
            if self.stop_requested:
                on_status(page_id, "Đã dừng", "Quá trình bị dừng bởi người dùng")
                return
            if scheduled_time:
                remaining = (scheduled_time - datetime.now()).total_seconds()
                if remaining > 0:
                    on_status(page_id, "Đang chờ lịch", f"Chờ thêm {int(remaining)} giây")
                    while remaining > 0 and not self.stop_requested:
                        time.sleep(1)
                        remaining = (scheduled_time - datetime.now()).total_seconds()
                    if self.stop_requested:
                        return
            if concurrency_delay > 0:
                time.sleep(concurrency_delay)
            on_status(page_id, "Đang đăng", "Đang gửi yêu cầu tới Facebook")
            try:
                if video_paths:
                    self.post_media(page, message, video_paths[0], base_url, media_type="video", post_type=post_type)
                elif image_paths:
                    self.post_media(page, message, image_paths[0], base_url, media_type="image", post_type=post_type)
                else:
                    self.post_text(page, message, base_url)
                success_count += 1
                on_status(page_id, "Thành công", "Đăng thành công")
                media_type = "video" if video_paths else "image" if image_paths else "text"
                self._post_repo.log_successful_post(page.get("name", page_id), message, media_type)
            except Exception as exc:
                fail_count += 1
                on_status(page_id, "Thất bại", str(exc))

        if use_concurrency:
            batches = chunked_batches(pages, max_workers)
        else:
            batches = [pages]

        for batch_index, batch in enumerate(batches):
            if batch_index > 0 and concurrency_delay > 0:
                time.sleep(concurrency_delay)
            if use_concurrency:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(worker, page) for page in batch]
                    for future in as_completed(futures):
                        future.result()
            else:
                for page in batch:
                    worker(page)

        on_complete(success_count, fail_count)

    def run_config_post_queue(
        self,
        rows: List[Dict[str, Any]],
        pages: List[Dict[str, Any]],
        base_url: str,
        concurrency_enabled: bool,
        concurrency_threads: int,
        concurrency_delay: float,
        on_status: Callable,
        on_config_status: Callable,
        on_link: Optional[Callable] = None,
        on_complete: Callable = None,
    ) -> None:
        success_count = 0
        fail_count = 0
        use_concurrency = concurrency_enabled and len(rows) > 1
        max_workers = concurrency_threads if use_concurrency else 1

        def worker(entry: Dict[str, Any]) -> None:
            nonlocal success_count, fail_count
            if self.stop_requested:
                return
            page_name = entry["page_name"]
            title = entry["title"]
            description = entry.get("description", "")
            video_path = entry["video_path"]

            if video_path and Path(video_path).name in self.load_posted_video_names(page_name):
                on_config_status(page_name, "Đã đăng trước đó")
                on_status(page_name, "Bỏ qua", "Video đã đăng cho page này, không đăng lại")
                entry["result"] = {"skipped": True, "reason": "already_posted"}
                return

            on_config_status(page_name, "Đang đăng")
            on_status(page_name, "Đang đăng", "Đang gửi yêu cầu tới Facebook")

            try:
                matching_page = None
                for page in pages:
                    if page.get("name") == page_name or page.get("id") == page_name:
                        matching_page = page
                        break
                if not matching_page:
                    raise ValueError(f"Không tìm thấy page: {page_name}")

                schedule_time = entry.get("schedule_time")
                if schedule_time:
                    try:
                        parsed = datetime.fromisoformat(schedule_time)
                        if parsed > datetime.now():
                            on_config_status(page_name, "Đang chờ lịch")
                            time.sleep(max(0, (parsed - datetime.now()).total_seconds()))
                    except Exception:
                        pass

                proxy_info = self._proxy_provider.fetch_proxy()
                proxy_config = self._proxy_provider.build_proxy_config(proxy_info)

                post_result = self.post_media(
                    matching_page, f"{title}\n\n{description}".strip(), video_path,
                    base_url, media_type="video", title=title, proxy_config=proxy_config,
                    post_type=entry.get("post_type", "video"),
                )

                success_count += 1
                post_id = self._extract_post_id(post_result)
                permalink = str(post_result.get("permalink_url") or post_result.get("link") or "")

                on_config_status(page_name, "Thành công")
                on_status(page_name, "Thành công", "Đăng thành công")
                if on_link and permalink:
                    on_link(page_name, permalink)

                self._post_repo.record_page_video(page_name, page_name, video_path, str(post_id or ""), permalink)
                self._post_repo.log_successful_post(page_name, f"{title}\n\n{description}".strip(), "video")

                comment_text = entry.get("comment_text", "")
                comment_image_paths = self.split_comment_image_paths(entry.get("comment_image_paths", ""))
                has_comment = bool(comment_text or comment_image_paths)
                if has_comment and post_id:
                    try:
                        self.post_comment_with_retries(matching_page, post_id, comment_text, comment_image_paths, proxy_config, base_url)
                    except Exception as exc:
                        logger.warning("Comment bị chặn cho page=%s: %s", page_name, exc)

                entry["result"] = {"post_id": post_id, "permalink_url": permalink, "comment_ok": has_comment}
            except Exception as exc:
                fail_count += 1
                on_config_status(page_name, "Thất bại")
                on_status(page_name, "Thất bại", str(exc))

        if use_concurrency:
            batches = chunked_batches(rows, max_workers)
        else:
            batches = [rows]

        for batch_index, batch in enumerate(batches):
            if batch_index > 0 and concurrency_delay > 0:
                time.sleep(concurrency_delay)
            if use_concurrency:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(worker, entry) for entry in batch]
                    for future in as_completed(futures):
                        future.result()
            else:
                for entry in batch:
                    worker(entry)

        if on_complete:
            on_complete(success_count, fail_count)

    @staticmethod
    def normalize_post_type(post_type: Optional[str]) -> str:
        value = (post_type or "").strip().lower()
        if not value:
            return "video"
        if value in {"feed", "post", "bài đăng thường", "bai dang thuong", "regular", "normal", "feed post"}:
            return "feed"
        if value in {"video", "reel", "reels", "reel/video", "video/reel", "video post", "reel post"}:
            return "video"
        if "reel" in value:
            return "video"
        if "feed" in value or "post" in value:
            return "feed"
        return "video"

    @staticmethod
    def format_post_type_label(post_type: str) -> str:
        return "Feed" if post_type == "feed" else "Reel / video"

    @staticmethod
    def split_comment_image_paths(raw_paths: str) -> List[str]:
        if not raw_paths:
            return []
        parts = re.split(r"[;\r\n]+", raw_paths)
        return [part.strip().strip('"') for part in parts if part.strip()]
