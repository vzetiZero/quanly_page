import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


LICENSE_SIGNING_KEY = b"TMV_FB_PAGE_MANAGER_LICENSE_STATE_V2"
PERMANENT_CODE = "vietboss1998"


class TrialLicenseManager:
    def __init__(self, project_dir: Optional[Path] = None) -> None:
        self.project_dir = Path(project_dir).resolve() if project_dir else Path(__file__).resolve().parent
        self.legacy_state_path = self.project_dir / "license_state.json"
        self.license_state_path = self._resolve_state_path()
        self.code: Optional[str] = None
        self.issued_at: Optional[datetime] = None
        self.expires_at: Optional[datetime] = None
        self.permanent: bool = False
        self.device_id: Optional[str] = None
        self._load_device_id()
        self.load_state()

    def _user_state_dir(self) -> Path:
        appdata = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "FBPageManager_TMV"
        if os.name == "nt":
            return Path.home() / "AppData" / "Local" / "FBPageManager_TMV"
        return Path.home() / ".fbpage_manager_tmv"

    def _resolve_state_path(self) -> Path:
        override = os.getenv("FB_LICENSE_PATH")
        if override:
            path = Path(override).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        path = self._user_state_dir() / "t1m2v3l4c.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _state_candidates(self) -> list[Path]:
        return [
            self._resolve_state_path(),
            self.legacy_state_path,
            self.project_dir / ".fb_license_state.dat",
        ]

    def _load_device_id(self) -> None:
        self.device_id = os.getenv("FB_DEVICE_ID") or self._default_device_id()

    def _default_device_id(self) -> str:
        try:
            import socket
            return socket.gethostname().strip() or "default-device"
        except Exception:
            return "default-device"

    def _reset_state(self) -> None:
        self.code = None
        self.issued_at = None
        self.expires_at = None
        self.permanent = False

    def _signature_payload(self, code: str, issued_at: datetime, expires_at: datetime) -> str:
        return "|".join(
            [
                str(code).strip().lower(),
                issued_at.isoformat(timespec="microseconds"),
                expires_at.isoformat(timespec="microseconds"),
                str(self.device_id or "").strip(),
            ]
        )

    def _make_signature(self, code: str, issued_at: datetime, expires_at: datetime) -> str:
        payload = self._signature_payload(code, issued_at, expires_at).encode("utf-8")
        return hmac.new(LICENSE_SIGNING_KEY, payload, hashlib.sha256).hexdigest()

    def _is_signature_valid(self, data: dict, code: str, issued_at: datetime, expires_at: datetime) -> bool:
        signature = str(data.get("signature", ""))
        expected = self._make_signature(code, issued_at, expires_at)
        return hmac.compare_digest(signature, expected)

    def _duration_days_from_code(self, code: str) -> Optional[int]:
        match = re.fullmatch(r"\s*(\d+)\s*([dD])\s*", str(code))
        if not match:
            return None
        return int(match.group(1))

    def _is_duration_valid(self, code: str, issued_at: datetime, expires_at: datetime) -> bool:
        if str(code).strip().lower() == PERMANENT_CODE:
            return True
        duration_days = self._duration_days_from_code(code)
        if duration_days is None:
            return False
        max_expires_at = issued_at + timedelta(days=duration_days, seconds=5)
        return issued_at <= expires_at <= max_expires_at

    def load_state(self) -> None:
        for candidate in self._state_candidates():
            if not candidate.exists():
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    continue
                code = str(data.get("code", "")).strip()
                issued_at_value = data.get("issued_at")
                expires_at_value = data.get("expires_at")
                if not code or not issued_at_value or not expires_at_value:
                    continue
                issued_at = datetime.fromisoformat(str(issued_at_value))
                expires_at = datetime.fromisoformat(str(expires_at_value))
                if not self._is_signature_valid(data, code, issued_at, expires_at):
                    continue
                stored_device_id = str(data.get("device_id", "")).strip() or None
                if stored_device_id and self.device_id and stored_device_id != self.device_id:
                    continue
                if not self._is_duration_valid(code, issued_at, expires_at):
                    continue
                self.license_state_path = self._resolve_state_path()
                self.code = code
                self.issued_at = issued_at
                self.expires_at = expires_at
                self.permanent = bool(code.strip().lower() == PERMANENT_CODE)
                return
            except Exception:
                continue
        self._reset_state()

    def save_state(self, code: str, expires_at: datetime) -> None:
        issued_at = datetime.now()
        self.code = code
        self.issued_at = issued_at
        self.expires_at = expires_at
        self.permanent = bool(str(code).strip().lower() == PERMANENT_CODE)
        payload = {
            "code": code,
            "issued_at": issued_at.isoformat(timespec="microseconds"),
            "expires_at": expires_at.isoformat(timespec="microseconds"),
            "device_id": str(self.device_id or "").strip() or self._default_device_id(),
        }
        payload["signature"] = self._make_signature(code, issued_at, expires_at)

        candidates = [self._resolve_state_path(), self._user_state_dir() / "license_state.json"]
        last_error: Optional[Exception] = None
        for candidate in candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                with open(candidate, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                self.license_state_path = candidate
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise RuntimeError(f"Không thể ghi trạng thái license: {last_error}") from last_error

    def validate_code(self, code: str) -> Optional[datetime]:
        normalized = str(code).strip().lower()
        if normalized == PERMANENT_CODE:
            self.permanent = True
            return datetime(2099, 12, 31, 23, 59, 59)
        if self.code and self.issued_at is not None and self.expires_at is not None:
            return None
        match = re.fullmatch(r"\s*(\d+)\s*([dD])\s*", code)
        if not match:
            return None
        amount = int(match.group(1))
        self.permanent = False
        return datetime.now() + timedelta(days=amount)

    def is_permanent(self) -> bool:
        return self.permanent or bool(self.code and str(self.code).strip().lower() == PERMANENT_CODE)

    def is_active(self) -> bool:
        if self.is_permanent():
            return True
        if self.code and self.issued_at and self.expires_at and not self._is_duration_valid(self.code, self.issued_at, self.expires_at):
            self._reset_state()
            return False
        return bool(self.expires_at and self.expires_at > datetime.now())

    def remaining_seconds(self) -> float:
        if self.expires_at is None:
            return 0.0
        return max(0.0, (self.expires_at - datetime.now()).total_seconds())

    def remaining_label(self) -> str:
        if self.is_permanent():
            return "Trial: vĩnh viễn"
        seconds = int(self.remaining_seconds())
        if seconds <= 0:
            return "Trial: đã hết hạn"
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"Trial: còn {hours}h {minutes}m {secs}s"

    def activate(self, code: str, expires_at: datetime) -> None:
        self.load_state()
        normalized = str(code).strip().lower()
        if normalized == PERMANENT_CODE:
            self.save_state(code, expires_at)
            return
        if self.is_permanent():
            return
        if self.code and self.issued_at is not None and self.expires_at is not None:
            return
        self.save_state(code, expires_at)

    def clear(self) -> None:
        self._reset_state()
        try:
            for candidate in self._state_candidates():
                try:
                    if candidate.exists():
                        candidate.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def hide_state_file(self) -> None:
        try:
            for candidate in self._state_candidates():
                if candidate.exists():
                    os.system(f"attrib +h \"{candidate}\" >nul 2>&1")
        except Exception:
            pass
