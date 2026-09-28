import argparse
import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from facebook_reels_uploader import FacebookReelsUploader

# =============================
# CẤU HÌNH
# =============================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("facebook_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class FacebookAPIConfig:
    """Cấu hình API Facebook."""

    def __init__(self):
        self.token = os.getenv("FACEBOOK_ACCESS_TOKEN", "").strip()
        self.api_version = os.getenv("FB_API_VERSION", "v25.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self.timeout = int(os.getenv("REQUEST_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.retry_delay = int(os.getenv("RETRY_DELAY", "2"))
        self.rate_limit_delay = float(os.getenv("RATE_LIMIT_DELAY", "0.5"))
        self.max_workers = int(os.getenv("MAX_WORKERS", "3"))
        self.cache_dir = Path(os.getenv("CACHE_DIR", ".cache"))
        self.cache_dir.mkdir(exist_ok=True)
        self.output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
        self.output_dir.mkdir(exist_ok=True)

    def has_token(self) -> bool:
        return bool(self.token)


class FacebookPageScraper:
    """Scraper cho Facebook Pages từ Business Manager và User Accounts."""

    def __init__(self, config: FacebookAPIConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.reels_uploader = FacebookReelsUploader(self.config.base_url, logger=logger)

    def _handle_rate_limit(self, retry_after: Optional[int] = None) -> None:
        if retry_after:
            wait_time = int(retry_after) + 1
            logger.warning(f"Rate limit exceeded. Waiting {wait_time} seconds...")
            time.sleep(wait_time)
        else:
            time.sleep(self.config.rate_limit_delay)

    def _make_request(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        if params is None:
            params = {}

        if not self.config.token:
            raise ValueError("FACEBOOK_ACCESS_TOKEN không được tìm thấy trong environment variables")

        params = dict(params)
        params["access_token"] = self.config.token

        cache_key = hashlib.md5(f"{url}{json.dumps(params, sort_keys=True)}".encode("utf-8")).hexdigest()
        cache_path = self.config.cache_dir / f"{cache_key}.json"

        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    logger.debug(f"Cache hit for {url}")
                    return json.load(handle)
            except Exception:
                pass

        for attempt in range(self.config.max_retries):
            try:
                response = self.session.get(url, params=params, timeout=self.config.timeout)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    self._handle_rate_limit(retry_after)
                    continue

                if response.status_code in {400, 401, 403}:
                    error_body = response.json() if response.content else {}
                    if error_body.get("error", {}).get("code") == 190:
                        logger.error("Access token đã hết hạn hoặc không hợp lệ")
                        raise ValueError("Invalid access token")

                response.raise_for_status()
                data = response.json()

                with open(cache_path, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                return data
            except requests.exceptions.RequestException as exc:
                logger.warning(f"Request failed (attempt {attempt + 1}/{self.config.max_retries}): {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    logger.error(f"Request failed after {self.config.max_retries} attempts: {exc}")
                    raise

        return None

    def _paginate(self, url: str, params: Optional[Dict] = None) -> List[Dict]:
        if params is None:
            params = {}

        results: List[Dict] = []
        current_url = url
        current_params = dict(params or {})
        if "limit" not in current_params:
            current_params["limit"] = 100

        while current_url:
            try:
                data = self._make_request(current_url, current_params)
                if not data:
                    break

                items = data.get("data", [])
                results.extend(items)
                logger.info(f"Lấy {len(items)} items (total: {len(results)})")

                paging = data.get("paging", {})
                next_url = paging.get("next") or ""
                if not next_url:
                    after = (paging.get("cursors") or {}).get("after")
                    if after and url:
                        current_url = url
                        current_params = dict(params or {})
                        current_params["limit"] = current_params.get("limit", 100)
                        current_params["after"] = after
                    else:
                        break
                else:
                    current_url = next_url
                    current_params = {}

                if len(results) % 100 == 0:
                    self._handle_rate_limit()
            except Exception as exc:
                logger.error(f"Error during pagination: {exc}")
                break

        logger.info(f"Tổng cộng {len(results)} items từ {url}")
        return results

    def get_businesses(self) -> List[Dict]:
        logger.info("Đang lấy danh sách Business Manager...")
        url = f"{self.config.base_url}/me/businesses"
        params = {"fields": "id,name"}
        businesses = self._paginate(url, params)
        logger.info(f"Tìm thấy {len(businesses)} Business Manager")
        return businesses

    def get_pages_from_business(self, business_id: str, business_name: str) -> List[Dict]:
        pages: List[Dict] = []
        endpoints = ["owned_pages", "client_pages"]

        for endpoint in endpoints:
            logger.info(f"Lấy {endpoint} từ Business {business_name} ({business_id})")
            url = f"{self.config.base_url}/{business_id}/{endpoint}"
            params = {
                "fields": "id,name,access_token,tasks,category,location,about,phone,website,instagram_business_account"
            }
            try:
                results = self._paginate(url, params)
                for page in results:
                    page["business_name"] = business_name
                    page["business_id"] = business_id
                    page["page_type"] = endpoint
                    pages.append(page)
            except Exception as exc:
                logger.error(f"Error getting {endpoint} from {business_id}: {exc}")

        logger.info(f"Tìm thấy {len(pages)} pages từ Business {business_name}")
        return pages

    def get_pages_from_account(self) -> List[Dict]:
        logger.info("Lấy Pages từ me/accounts...")
        url = f"{self.config.base_url}/me/accounts"
        params = {
            "fields": "id,name,access_token,tasks,category,location,about,phone,website,instagram_business_account"
        }
        pages = self._paginate(url, params)
        for page in pages:
            page["business_name"] = ""
            page["business_id"] = ""
            page["page_type"] = "me/accounts"
        logger.info(f"Tìm thấy {len(pages)} pages từ me/accounts")
        return pages

    def verify_access_token(self) -> Dict[str, Any]:
        if not self.config.has_token():
            raise ValueError("FACEBOOK_ACCESS_TOKEN chưa được cấu hình")

        logger.info("Đang kiểm tra token và quyền truy cập của Meta App...")
        result: Dict[str, Any] = {
            "debug_token": {},
            "profile": {},
            "permissions": {},
            "businesses": [],
            "account_pages": [],
            "errors": [],
            "summary": {},
        }

        try:
            result["debug_token"] = self._make_request(
                f"{self.config.base_url}/debug_token",
                {
                    "input_token": self.config.token,
                    "fields": "app_id,is_valid,application,expires_at,scopes,user_id",
                },
            ) or {}
        except Exception as exc:
            result["errors"].append({"operation": "debug_token", "error": str(exc)})

        try:
            result["profile"] = self._make_request(f"{self.config.base_url}/me", {"fields": "id,name"}) or {}
        except Exception as exc:
            result["errors"].append({"operation": "me", "error": str(exc)})

        try:
            result["permissions"] = self._make_request(
                f"{self.config.base_url}/me/permissions",
                {"fields": "permission,status"},
            ) or {}
        except Exception as exc:
            result["errors"].append({"operation": "me/permissions", "error": str(exc)})

        try:
            result["businesses"] = self.get_businesses()
        except Exception as exc:
            result["errors"].append({"operation": "me/businesses", "error": str(exc)})

        try:
            result["account_pages"] = self.get_pages_from_account()
        except Exception as exc:
            result["errors"].append({"operation": "me/accounts", "error": str(exc)})

        permissions_data = result.get("permissions", {})
        result["summary"] = {
            "business_count": len(result.get("businesses", [])),
            "page_count_from_accounts": len(result.get("account_pages", [])),
            "has_pages_show_list": any(
                perm.get("permission") == "pages_show_list" and perm.get("status") == "granted"
                for perm in permissions_data.get("data", [])
            ),
            "has_business_management": any(
                perm.get("permission") == "business_management" and perm.get("status") == "granted"
                for perm in permissions_data.get("data", [])
            ),
            "has_pages_manage_posts": any(
                perm.get("permission") == "pages_manage_posts" and perm.get("status") == "granted"
                for perm in permissions_data.get("data", [])
            ),
            "has_publish_video": any(
                perm.get("permission") == "publish_video" and perm.get("status") == "granted"
                for perm in permissions_data.get("data", [])
            ),
        }

        logger.info("Kiểm tra token hoàn tất")
        return result

    def scrape_all_pages(self) -> List[Dict]:
        start_time = time.time()
        all_pages: List[Dict] = []
        page_ids_seen = set()

        if not self.config.has_token():
            logger.warning("FACEBOOK_ACCESS_TOKEN chưa được cấu hình; bỏ qua cuộc gọi API thật")
            return []

        businesses = self.get_businesses()
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_business = {
                executor.submit(self.get_pages_from_business, biz.get("id", ""), biz.get("name", "")): biz
                for biz in businesses
            }
            for future in as_completed(future_to_business):
                business = future_to_business[future]
                try:
                    pages = future.result()
                    for page in pages:
                        if page.get("id") not in page_ids_seen:
                            page_ids_seen.add(page.get("id"))
                            all_pages.append(page)
                except Exception as exc:
                    logger.error(f"Error processing business {business.get('name', '')}: {exc}")

        account_pages = self.get_pages_from_account()
        for page in account_pages:
            if page.get("id") not in page_ids_seen:
                page_ids_seen.add(page.get("id"))
                all_pages.append(page)

        elapsed = time.time() - start_time
        logger.info(f"Hoàn thành scraping {len(all_pages)} pages trong {elapsed:.2f} giây")
        return all_pages


class PagePostManager:
    """Quản lý đăng bài lên Facebook Pages."""

    def __init__(self, config: FacebookAPIConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def _handle_rate_limit(self, retry_after: Optional[int] = None) -> None:
        if retry_after:
            wait_time = int(retry_after) + 1
            logger.warning(f"Rate limit exceeded. Waiting {wait_time} seconds...")
            time.sleep(wait_time)
        else:
            time.sleep(self.config.rate_limit_delay)

    def _request_with_retry(self, url: str, data: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        for attempt in range(self.config.max_retries):
            try:
                response = self.session.post(url, data=data, files=files, timeout=self.config.timeout)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    self._handle_rate_limit(retry_after)
                    continue

                if response.status_code in {400, 401, 403}:
                    error_data = response.json() if response.content else {}
                    if error_data.get("error", {}).get("code") == 190:
                        raise ValueError("Invalid OAuth token")
                    if error_data.get("error", {}).get("code") == 4:
                        raise RuntimeError("Application request limit reached")

                response.raise_for_status()
                result = response.json()
                if "error" in result:
                    raise RuntimeError(result["error"].get("message", "Unknown Facebook API error"))
                return result
            except requests.exceptions.RequestException as exc:
                logger.warning(f"Request failed (attempt {attempt + 1}/{self.config.max_retries}): {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    raise
            except Exception as exc:
                logger.warning(f"Post attempt {attempt + 1}/{self.config.max_retries} failed: {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    raise
        raise RuntimeError("Post failed after retries")

    def wait_until_scheduled_time(self, scheduled_time: datetime) -> None:
        if scheduled_time is None:
            return
        remaining = (scheduled_time - datetime.now()).total_seconds()
        if remaining > 0:
            logger.info(f"Đợi {remaining:.0f} giây trước khi đăng lịch...")
            time.sleep(remaining)

    def post_text(self, page_id: str, page_token: str, message: str, link: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.config.base_url}/{page_id}/feed"
        data = {"access_token": page_token, "message": message}
        if link:
            data["link"] = link
        result = self._request_with_retry(url, data=data)
        logger.info(f"Đã đăng bài text lên Page {page_id}: {result.get('id')}")
        return result

    def post_photo(self, page_id: str, page_token: str, image_path: str, caption: str = "", link: Optional[str] = None) -> Dict[str, Any]:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Không tìm thấy file ảnh: {image_path}")

        url = f"{self.config.base_url}/{page_id}/photos"
        with open(image_path, "rb") as image_file:
            files = {"source": image_file}
            data = {"access_token": page_token, "caption": caption}
            if link:
                data["link"] = link
            result = self._request_with_retry(url, data=data, files=files)
        logger.info(f"Đã đăng ảnh lên Page {page_id}: {result.get('id')}")
        return result

    def post_video(
        self,
        page_id: str,
        page_token: str,
        video_path: str,
        title: str = "",
        description: str = "",
        thumbnail_path: Optional[str] = None,
        scheduled_time: Optional[datetime] = None,
        public_visibility_timeout: int = 300,
    ) -> Dict[str, Any]:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Khﾃｴng tﾃｬm th蘯･y file video: {video_path}")

        if scheduled_time:
            self.wait_until_scheduled_time(scheduled_time)

        if thumbnail_path:
            logger.info(f"Thumbnail bị bỏ qua trong flow Reels mới: {thumbnail_path}")

        page = {"id": page_id, "access_token": page_token, "name": page_id}
        if not page_token:
            raise RuntimeError("Thiếu Page Access Token. Hãy lấy token từ /me/accounts của user quản trị Page.")
        upload_message = description or title or ""
        start_result = self.reels_uploader.start_upload(
            page,
            video_path,
            upload_message,
            title=title or page_id,
            description=description or title or "",
        )
        upload_id = start_result.get("id") or start_result.get("video_id") or start_result.get("upload_id")
        if not upload_id:
            raise RuntimeError(f"Reels upload không trả về upload_id: {start_result}")

        binary_result = self.reels_uploader.upload_binary(page, upload_id, video_path)
        publish_result = self.reels_uploader.finish_publish(
            page,
            upload_id,
            message=upload_message,
            title=title or page_id,
            description=description or title or "",
        )
        final_payload = self.reels_uploader.verify_public_visibility(
            page,
            upload_id,
            timeout_seconds=public_visibility_timeout,
        )
        final_payload = dict(final_payload or {})
        final_payload.setdefault("upload_id", upload_id)
        final_payload.setdefault("post_id", publish_result.get("post_id") if isinstance(publish_result, dict) else None)
        final_payload.setdefault("start_result", start_result)
        final_payload.setdefault("binary_result", binary_result)
        final_payload.setdefault("publish_result", publish_result)
        final_payload.setdefault("permalink_url", self.reels_uploader.get_permalink(page, upload_id))
        if final_payload.get("public_visibility") is False:
            raise RuntimeError(
                f"Reels đã publish nhưng chưa public được: {final_payload.get('public_visibility_reason') or 'unknown'}"
            )
        logger.info(f"Đã đăng reel lên Page {page_id}: {final_payload.get('permalink_url') or upload_id}")
        return final_payload

    def post_video_with_retry(
        self,
        page_id: str,
        page_token: str,
        video_path: str,
        title: str = "",
        description: str = "",
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        for attempt in range(max_retries):
            try:
                return self.post_video(page_id, page_token, video_path, title, description)
            except Exception as exc:
                logger.warning(f"Lần thử {attempt + 1}/{max_retries} thất bại: {exc}")
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                else:
                    raise
        raise RuntimeError("Reel post failed after retries")


class DataExporter:
    """Xuất dữ liệu ra thư mục output."""

    @staticmethod
    def save_txt(pages: List[Dict], filepath: Path) -> None:
        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write("=" * 80 + "\n")
            handle.write("FACEBOOK PAGES TOKEN COLLECTION\n")
            handle.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write("=" * 80 + "\n\n")
            handle.write("SUMMARY STATISTICS\n")
            handle.write("-" * 40 + "\n")
            handle.write(f"Total Pages: {len(pages)}\n")
            business_pages = [page for page in pages if page.get("business_name")]
            user_pages = [page for page in pages if not page.get("business_name")]
            handle.write(f"Business Pages: {len(business_pages)}\n")
            handle.write(f"User Pages: {len(user_pages)}\n")
            pages_with_token = [page for page in pages if page.get("access_token")]
            handle.write(f"Pages with Access Token: {len(pages_with_token)}\n")
            handle.write("\n" + "=" * 80 + "\n\n")

            for idx, page in enumerate(pages, 1):
                handle.write(f"[{idx}] PAGE INFORMATION\n")
                handle.write("-" * 40 + "\n")
                handle.write(f"Page Name: {page.get('name', 'N/A')}\n")
                handle.write(f"Page ID: {page.get('id', 'N/A')}\n")
                handle.write(f"Category: {page.get('category', 'N/A')}\n")
                if page.get("access_token"):
                    handle.write(f"Access Token: {page.get('access_token')}\n")
                else:
                    handle.write("Access Token: NOT AVAILABLE\n")
                if page.get("business_name"):
                    handle.write(f"Business Name: {page.get('business_name')}\n")
                    handle.write(f"Business ID: {page.get('business_id')}\n")
                    handle.write(f"Page Type: {page.get('page_type', 'N/A')}\n")
                else:
                    handle.write("Source: User Account (me/accounts)\n")
                if page.get("about"):
                    handle.write(f"About: {str(page.get('about'))[:200]}\n")
                if page.get("phone"):
                    handle.write(f"Phone: {page.get('phone')}\n")
                if page.get("website"):
                    handle.write(f"Website: {page.get('website')}\n")
                if page.get("instagram_business_account"):
                    instagram = page["instagram_business_account"]
                    handle.write(f"Instagram ID: {instagram.get('id', 'N/A')}\n")
                    handle.write(f"Instagram Username: {instagram.get('username', 'N/A')}\n")
                if page.get("location"):
                    location = page["location"]
                    handle.write(f"Location: {location.get('city', '')}, {location.get('country', '')}\n")
                handle.write("\n" + "-" * 40 + "\n\n")

            handle.write("=" * 80 + "\n")
            handle.write("HOW TO USE THESE TOKENS\n")
            handle.write("=" * 80 + "\n\n")
            handle.write("1. POST TEXT: POST https://graph.facebook.com/v25.0/{page_id}/feed\n")
            handle.write("   Params: access_token={token}&message=Your message\n\n")
            handle.write("2. POST PHOTO: POST https://graph.facebook.com/v25.0/{page_id}/photos\n")
            handle.write("   Params: access_token={token}&caption=Caption\n\n")
            handle.write("3. POST REELS: POST https://graph.facebook.com/v25.0/{page_id}/video_reels\n")
            handle.write("   Params: access_token={token}&title=Title&description=Description\n")
            handle.write("4. IMPORTANT: page access token needs publish permissions for posts and reels.\n")

    @staticmethod
    def save_json(pages: List[Dict], filepath: Path) -> None:
        with open(filepath, "w", encoding="utf-8") as handle:
            json.dump(pages, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def save_usage_guide(filepath: Path) -> None:
        guide = """# Facebook Page Posting Guide

## 1. Cấu hình environment variables
- FACEBOOK_ACCESS_TOKEN: User access token hoặc app token đủ quyền.
- FB_API_VERSION: default v25.0.
- OUTPUT_DIR: thư mục xuất file kết quả.

## 2. Thu thập Pages
```bash
python main.py collect
```

## 3. Đăng bài text
```python
from main import FacebookAPIConfig, PagePostManager

config = FacebookAPIConfig()
manager = PagePostManager(config)
result = manager.post_text(page_id="YOUR_PAGE_ID", page_token="YOUR_PAGE_TOKEN", message="Hello from automation")
print(result)
```

## 4. Đăng reel
```python
from main import FacebookAPIConfig, PagePostManager

config = FacebookAPIConfig()
manager = PagePostManager(config)
result = manager.post_video(
    page_id="YOUR_PAGE_ID",
    page_token="YOUR_PAGE_TOKEN",
    video_path="video.mp4",
    title="My reel",
    description="Posted via Reels flow",
)
print(result)
```

## 5. Lên lịch đăng reel
```python
from datetime import datetime, timedelta
from main import FacebookAPIConfig, PagePostManager

config = FacebookAPIConfig()
manager = PagePostManager(config)
future_time = datetime.now() + timedelta(minutes=10)
manager.post_video(
    page_id="YOUR_PAGE_ID",
    page_token="YOUR_PAGE_TOKEN",
    video_path="video.mp4",
    title="Scheduled reel",
    description="Scheduled posting",
    scheduled_time=future_time,
)
```
"""
        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write(guide)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Facebook Page token collector and poster")
    subparsers = parser.add_subparsers(dest="command")

    collect_parser = subparsers.add_parser("collect", help="Collect page access tokens from BM and me/accounts")
    collect_parser.add_argument("--dry-run", action="store_true", help="Generate sample outputs without calling Facebook")
    collect_parser.add_argument("--output-dir", default=None, help="Thư mục lưu output")

    post_text_parser = subparsers.add_parser("post-text", help="Post a text status to a page")
    post_text_parser.add_argument("--page-id", required=True)
    post_text_parser.add_argument("--page-token")
    post_text_parser.add_argument("--message", required=True)
    post_text_parser.add_argument("--link")

    post_photo_parser = subparsers.add_parser("post-photo", help="Post a photo to a page")
    post_photo_parser.add_argument("--page-id", required=True)
    post_photo_parser.add_argument("--page-token")
    post_photo_parser.add_argument("--image-path", required=True)
    post_photo_parser.add_argument("--caption")
    post_photo_parser.add_argument("--link")

    post_video_parser = subparsers.add_parser("post-video", help="Post a reel to a page")
    post_video_parser.add_argument("--page-id", required=True)
    post_video_parser.add_argument("--page-token")
    post_video_parser.add_argument("--video-path", required=True)
    post_video_parser.add_argument("--title")
    post_video_parser.add_argument("--description")
    post_video_parser.add_argument("--thumbnail-path")
    post_video_parser.add_argument("--schedule-minutes", type=int, default=0, help="Đăng video sau X phút")

    verify_parser = subparsers.add_parser("verify-token", help="Verify the Facebook token and app permissions")
    verify_parser.add_argument("--output-dir", default=None, help="Thư mục lưu kết quả xác minh")

    return parser


def parse_scheduled_time(scheduled_time: Optional[datetime], schedule_minutes: int = 0) -> Optional[datetime]:
    if scheduled_time:
        return scheduled_time
    if schedule_minutes and schedule_minutes > 0:
        return datetime.now() + timedelta(minutes=schedule_minutes)
    return None


def run_collect(config: FacebookAPIConfig, output_dir: Path, dry_run: bool) -> List[Dict]:
    exporter = DataExporter()
    if dry_run:
        pages = [
            {
                "id": "demo_page_1",
                "name": "Demo Page",
                "category": "Business",
                "access_token": "YOUR_PAGE_ACCESS_TOKEN",
                "business_name": "Demo Business Manager",
                "business_id": "demo_bm",
                "page_type": "owned_pages",
                "about": "Demo output for local validation",
            }
        ]
        logger.info("Chạy dry-run và tạo sample output")
    else:
        scraper = FacebookPageScraper(config)
        pages = scraper.scrape_all_pages()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = output_dir / f"facebook_pages_{timestamp}.txt"
    json_path = output_dir / f"facebook_pages_{timestamp}.json"
    guide_path = output_dir / "posting_guide.md"

    exporter.save_txt(pages, txt_path)
    exporter.save_json(pages, json_path)
    exporter.save_usage_guide(guide_path)
    logger.info(f"Đã lưu TXT: {txt_path}")
    logger.info(f"Đã lưu JSON: {json_path}")
    logger.info(f"Đã lưu hướng dẫn: {guide_path}")
    return pages


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = FacebookAPIConfig()
    output_dir = Path(getattr(args, "output_dir", None) or os.getenv("OUTPUT_DIR", "output"))
    output_dir.mkdir(exist_ok=True)

    if args.command == "collect":
        run_collect(config, output_dir, dry_run=args.dry_run)
        return

    if args.command == "post-text":
        manager = PagePostManager(config)
        page_token = args.page_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_ACCESS_TOKEN") or config.token
        manager.post_text(args.page_id, page_token, args.message, args.link)
        return

    if args.command == "post-photo":
        manager = PagePostManager(config)
        page_token = args.page_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_ACCESS_TOKEN") or config.token
        manager.post_photo(args.page_id, page_token, args.image_path, args.caption or "", args.link)
        return

    if args.command == "post-video":
        manager = PagePostManager(config)
        page_token = args.page_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or os.getenv("FACEBOOK_ACCESS_TOKEN") or config.token
        scheduled_time = parse_scheduled_time(None, args.schedule_minutes)
        manager.post_video(
            args.page_id,
            page_token,
            args.video_path,
            title=args.title or "",
            description=args.description or "",
            thumbnail_path=args.thumbnail_path,
            scheduled_time=scheduled_time,
        )
        return

    if args.command == "verify-token":
        scraper = FacebookPageScraper(config)
        output_dir = Path(getattr(args, "output_dir", None) or os.getenv("OUTPUT_DIR", "output"))
        output_dir.mkdir(exist_ok=True)
        verification = scraper.verify_access_token()
        report_path = output_dir / "token_verification.json"
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(verification, handle, ensure_ascii=False, indent=2)
        logger.info(f"Đã lưu kết quả kiểm tra token tại: {report_path}")
        print(json.dumps(verification.get("summary", {}), indent=2, ensure_ascii=False))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
