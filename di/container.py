import os
from pathlib import Path
from typing import Optional

from models.database import DatabaseManager
from models.config_model import ConfigManager
from models.page_detail_model import PageDetailFetcher
from facebook_reels_uploader import FacebookReelsUploader
from license_manager import TrialLicenseManager
from services.page_service import PageService
from services.post_service import PostService
from services.config_service import ConfigService
from services.license_service import LicenseService
from services.page_detail_service import PageDetailService
from services.stats_service import StatsService
from services.schedule_service import ScheduleService


class Container:
    """Composition Root — nơi duy nhất được phép new concrete class."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        db_path = project_dir / "facebook_cache.sqlite3"
        config_path = project_dir / "facebook_config.json"

        api_version = os.getenv("FB_API_VERSION", "v25.0")
        base_url = f"https://graph.facebook.com/{api_version}"
        self.base_url = base_url

        # ── Repositories (implement interfaces) ───────────────────
        self.db = DatabaseManager(db_path)
        self.config_repo = ConfigManager(config_path)

        # ── External clients ──────────────────────────────────────
        self.reels_uploader = FacebookReelsUploader(base_url)
        self.page_detail_fetcher = PageDetailFetcher(base_url)
        self.license_manager = TrialLicenseManager(project_dir)
        self.license_manager.hide_state_file()

        # ── Proxy ─────────────────────────────────────────────────
        from services.proxy_service import ProxyController
        self.proxy_provider = ProxyController(
            api_key=os.getenv("KIOTPROXY_API_KEY", ""),
            provider_name=os.getenv("PROXY_PROVIDER", "kiotproxy"),
            region=os.getenv("KIOTPROXY_REGION", "random"),
            endpoint_template=os.getenv("PROXY_ENDPOINT_TEMPLATE", ""),
        )

        # ── Services ──────────────────────────────────────────────
        self.page_service = PageService(
            page_repo=self.db,
            token_repo=self.db,
            base_url=base_url,
        )
        self.post_service = PostService(
            post_repo=self.db,
            reels_uploader=self.reels_uploader,
            proxy_provider=self.proxy_provider,
        )
        self.config_service = ConfigService(config_repo=self.config_repo)
        self.license_service = LicenseService(license_manager=self.license_manager)
        self.page_detail_service = PageDetailService(
            detail_repo=self.db,
            fetcher=self.page_detail_fetcher,
        )
        self.stats_service = StatsService(
            stats_repo=self.db,
            base_url=base_url,
        )
        self.schedule_service = ScheduleService(
            schedule_repo=self.db,
            post_service=self.post_service,
            base_url=base_url,
        )

        # ── Presenters (sẽ được set sau khi view tạo) ────────────
        self.main_presenter: Optional[object] = None
