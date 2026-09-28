from pathlib import Path
from typing import Any, Dict, List, Optional

from di.interfaces import IConfigRepository

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".image"}


class ConfigService:
    def __init__(self, config_repo: IConfigRepository) -> None:
        self._repo = config_repo

    def load(self) -> Dict[str, Any]:
        return self._repo.load()

    def save(self, payload: Dict[str, Any]) -> None:
        self._repo.save(payload)

    def get_tokens(self) -> List[str]:
        data = self._repo.load()
        return [str(t).strip() for t in data.get("tokens", []) if str(t).strip()]

    def get_message(self) -> str:
        return str(self._repo.load().get("message", ""))

    def get_post_type(self) -> str:
        return str(self._repo.load().get("post_type", "video"))

    def get_images(self) -> List[str]:
        return [p.strip() for p in self._repo.load().get("images", []) if p.strip()]

    def get_videos(self) -> List[str]:
        return [p.strip() for p in self._repo.load().get("videos", []) if p.strip()]

    def get_video_folder(self) -> str:
        return str(self._repo.load().get("video_folder", ""))

    def get_comment_image_folder(self) -> str:
        return str(self._repo.load().get("comment_image_folder", ""))

    def get_use_comment_images(self) -> bool:
        return bool(self._repo.load().get("use_comment_images", False))

    def get_skip_missing_videos(self) -> bool:
        return bool(self._repo.load().get("skip_missing_videos", True))

    def get_schedule(self) -> Dict[str, Any]:
        data = self._repo.load()
        return data.get("quick_schedule") or data.get("schedule") or {}

    def get_concurrency(self) -> Dict[str, Any]:
        return self._repo.load().get("concurrency", {})

    def get_quick_config(self) -> Dict[str, str]:
        return self._repo.load().get("quick_config", {})

    def get_proxy(self) -> Dict[str, Any]:
        return self._repo.load().get("proxy", {})

    def get_config_rows(self) -> List[Dict[str, str]]:
        return self._repo.load().get("config_rows", [])

    def collect_media_files(self, folder: Optional[str], extensions: Optional[set] = None) -> List[str]:
        if not folder:
            return []
        path = Path(folder).expanduser()
        if not path.exists() or not path.is_dir():
            return []
        files = []
        for candidate in path.iterdir():
            if not candidate.is_file():
                continue
            if extensions and candidate.suffix.lower() not in extensions:
                continue
            files.append(str(candidate.resolve()))
        files.sort(key=lambda value: Path(value).name.lower())
        return files

    def derive_title(self, video_path: str, current_title: str, fallback_name: str) -> str:
        if current_title and current_title.strip():
            return current_title.strip()
        if video_path:
            candidate = Path(video_path).stem
            if candidate:
                return candidate
        return fallback_name or "Bài đăng"
