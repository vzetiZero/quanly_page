import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConfigManager:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def get_tokens(self) -> List[str]:
        data = self.load()
        return [str(t).strip() for t in data.get("tokens", []) if str(t).strip()]

    def get_message(self) -> str:
        return str(self.load().get("message", ""))

    def get_post_type(self) -> str:
        return str(self.load().get("post_type", "video"))

    def get_images(self) -> List[str]:
        return [p.strip() for p in self.load().get("images", []) if p.strip()]

    def get_videos(self) -> List[str]:
        return [p.strip() for p in self.load().get("videos", []) if p.strip()]

    def get_video_folder(self) -> str:
        return str(self.load().get("video_folder", ""))

    def get_comment_image_folder(self) -> str:
        return str(self.load().get("comment_image_folder", ""))

    def get_use_comment_images(self) -> bool:
        return bool(self.load().get("use_comment_images", False))

    def get_skip_missing_videos(self) -> bool:
        return bool(self.load().get("skip_missing_videos", True))

    def get_schedule(self) -> Dict[str, Any]:
        data = self.load()
        return data.get("quick_schedule") or data.get("schedule") or {}

    def get_concurrency(self) -> Dict[str, Any]:
        return self.load().get("concurrency", {})

    def get_quick_config(self) -> Dict[str, str]:
        return self.load().get("quick_config", {})

    def get_proxy(self) -> Dict[str, Any]:
        return self.load().get("proxy", {})

    def get_config_rows(self) -> List[Dict[str, str]]:
        return self.load().get("config_rows", [])
