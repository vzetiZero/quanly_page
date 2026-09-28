from datetime import datetime
from typing import Optional

from di.interfaces import ILicenseManager


class LicenseService:
    def __init__(self, license_manager: ILicenseManager) -> None:
        self._mgr = license_manager

    def validate_code(self, code: str) -> Optional[datetime]:
        return self._mgr.validate_code(code)

    def activate(self, code: str, expires_at: datetime) -> None:
        self._mgr.activate(code, expires_at)
        self._mgr.hide_state_file()

    def is_active(self) -> bool:
        return self._mgr.is_active()

    def is_permanent(self) -> bool:
        return self._mgr.is_permanent()

    def remaining_label(self) -> str:
        return self._mgr.remaining_label()

    def clear(self) -> None:
        self._mgr.clear()
        self._mgr.hide_state_file()

    def load_state(self) -> None:
        self._mgr.load_state()

    @property
    def code(self) -> Optional[str]:
        return self._mgr.code

    @property
    def expires_at(self) -> Optional[datetime]:
        return self._mgr.expires_at
