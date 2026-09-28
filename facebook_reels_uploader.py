import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests


class FacebookReelsUploader:
    def __init__(
        self,
        base_url: str,
        *,
        logger: Optional[logging.Logger] = None,
        append_log=None,
        requests_module=None,
        timeout: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger or logging.getLogger("facebook_reels_uploader")
        self.append_log = append_log or (lambda *args, **kwargs: None)
        self.requests_module = requests_module or requests
        self.timeout = timeout
        self._upload_urls: Dict[str, str] = {}
        self._post_ids: Dict[str, str] = {}

    def _log(self, message: str, level: str = "info") -> None:
        getattr(self.logger, level, self.logger.info)(message)
        self.append_log(message, level)

    @staticmethod
    def _parse_json_or_empty(response: Any) -> Dict[str, Any]:
        try:
            return response.json() if hasattr(response, "json") else {}
        except ValueError:
            return {}

    @staticmethod
    def _redact_sensitive(value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if str(key).lower() in {"authorization", "access_token"}:
                    redacted[key] = "<redacted>"
                else:
                    redacted[key] = FacebookReelsUploader._redact_sensitive(item)
            return redacted
        if not isinstance(value, str):
            return value
        value = re.sub(r"(access_token=)[^&\s]+", r"\1<redacted>", value)
        value = re.sub(r"(OAuth\s+)[A-Za-z0-9_\-]+", r"\1<redacted>", value)
        value = re.sub(r'("access_token"\s*:\s*")[^"]+', r'\1<redacted>', value)
        return value

    def _raise_for_graph_error(self, response: Any, *, action: str) -> None:
        if response is None:
            raise RuntimeError(f"Reels upload failed during {action}: no response")
        if getattr(response, "status_code", 0) < 400:
            return
        # Try to extract structured error information, fall back to raw text
        payload = self._parse_json_or_empty(response)
        error_payload = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = None
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
        if not message and isinstance(payload, dict):
            message = payload.get("message")

        raw_text = (getattr(response, "text", "") or "").strip()
        status = getattr(response, "status_code", "n/a")
        req = getattr(response, "request", None)
        request_url = self._redact_sensitive(getattr(req, "url", None) or "")

        if not message:
            message = raw_text or f"status={status}"

        # Collect request details for debugging (method, headers, body)
        try:
            req_method = getattr(req, "method", "") or ""
            req_headers = getattr(req, "headers", {}) or {}
            req_body = getattr(req, "body", None)
        except Exception:
            req_method = ""
            req_headers = {}
            req_body = None

        # Represent body safely: show decoded text up to 1024 chars, or byte length
        req_body_display = ""
        if req_body is None:
            req_body_display = ""
        elif isinstance(req_body, (bytes, bytearray)):
            try:
                if len(req_body) <= 1024:
                    req_body_display = self._redact_sensitive(req_body.decode(errors="replace"))
                else:
                    req_body_display = f"<binary {len(req_body)} bytes>"
            except Exception:
                req_body_display = f"<binary {len(req_body)} bytes>"
        else:
            # likely a string
            try:
                s = self._redact_sensitive(str(req_body))
                req_body_display = s if len(s) <= 1024 else s[:1024] + "..."
            except Exception:
                req_body_display = "<unserializable>"

        # Append status, URL, and request info for easier debugging
        detailed = f"{message} (status={status}"
        if request_url:
            detailed += f", url={request_url}"
        if req_method:
            detailed += f", request_method={req_method}"
        if req_headers:
            detailed += f", request_headers={self._redact_sensitive(dict(req_headers))}"
        if req_body_display:
            detailed += f", request_body={req_body_display}"
        detailed += ")"

        raise RuntimeError(f"Reels upload failed during {action}: {detailed}")

    @staticmethod
    def _build_metadata_payload(
        page: Dict[str, Any],
        *,
        message: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"access_token": page.get("access_token", "")}
        if message:
            payload["message"] = message
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description
        return payload

    @staticmethod
    def _extract_upload_id(payload: Dict[str, Any]) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        return payload.get("id") or payload.get("video_id") or payload.get("upload_id")

    @staticmethod
    def _build_reel_permalink(upload_id: str) -> str:
        return f"https://www.facebook.com/reel/{upload_id}"

    @staticmethod
    def _status_blob(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            return status_payload
        if payload.get("video_status") is not None:
            return {"video_status": payload.get("video_status")}
        if isinstance(status_payload, str):
            return {"status": status_payload}
        return {}

    @staticmethod
    def _is_public_payload(payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False

        status_payload = FacebookReelsUploader._status_blob(payload)
        video_status = str(
            status_payload.get("video_status")
            or status_payload.get("status")
            or payload.get("video_status")
            or payload.get("status")
            or ""
        ).lower()
        status_values = {str(value).lower() for value in status_payload.values() if value is not None}
        if isinstance(payload.get("status"), str):
            status_values.add(payload.get("status").lower())
        if payload.get("video_status") is not None:
            status_values.add(str(payload.get("video_status")).lower())
        permalink = payload.get("permalink_url") or payload.get("link")
        return bool(
            permalink
            and (
                video_status in {"complete", "ready", "processed", "published", "success"}
                or bool(status_values.intersection({"complete", "ready", "processed", "published", "success"}))
            )
        )

    def _request_public_page(self, permalink_url: str, *, timeout: int = 30, proxy_config: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        try:
            response = self.requests_module.get(
                permalink_url,
                timeout=timeout,
                proxies=proxy_config or None,
                allow_redirects=True,
            )
        except Exception as exc:
            return False, str(exc)

        if response is None:
            return False, "no response"
        if getattr(response, "status_code", 0) >= 400:
            return False, f"status={getattr(response, 'status_code', 'n/a')}"
        body = (getattr(response, "text", "") or "").lower()
        if "login" in body and "sign up" in body:
            return False, "login wall"
        if "content unavailable" in body or "this content isn't available" in body:
            return False, "content unavailable"
        return True, f"status={getattr(response, 'status_code', 'n/a')}"


    def start_upload(
        self,
        page: Dict[str, Any],
        media_path: str,
        message: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"File media not found: {media_path}")

        payload = {
            "access_token": page.get("access_token", ""),
            "upload_phase": "start",
        }
        response = self.requests_module.post(
            f"{self.base_url}/{page['id']}/video_reels",
            data=payload,
            timeout=60,
            proxies=proxy_config or None,
        )
        self._raise_for_graph_error(response, action="start_upload")
        response_payload = self._parse_json_or_empty(response)
        upload_id = self._extract_upload_id(response_payload)
        upload_url = response_payload.get("upload_url")
        if upload_id and upload_url:
            self._upload_urls[str(upload_id)] = str(upload_url)
        self._log(f"[Reels] start_upload page={page.get('name', page.get('id', ''))} response={response_payload}", "info")
        return response_payload

    def upload_binary(
        self,
        page: Dict[str, Any],
        upload_id: str,
        media_path: str,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"File media not found: {media_path}")

        upload_url = self._upload_urls.get(str(upload_id))
        if not upload_url:
            raise RuntimeError(f"Reels upload failed during upload_binary: missing upload_url for id={upload_id}")

        file_size = os.path.getsize(media_path)
        headers = {
            "Authorization": f"OAuth {page.get('access_token', '')}",
            # Follow Meta docs: these are headers, not different names
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream",
        }

        # Read binary fully and send as the request body (non-streaming) to match
        # Meta's example using --data-binary for debugging purposes.
        response = None
        with open(media_path, "rb") as f:
            binary = f.read()
        try:
            response = self.requests_module.post(
                upload_url,
                data=binary,
                headers=headers,
                timeout=self.timeout,
                proxies=proxy_config or None,
            )
        except Exception:
            response = None
        self._raise_for_graph_error(response, action="upload_binary")
        response_payload = self._parse_json_or_empty(response)
        self._log(f"[Reels] upload_binary id={upload_id} response={response_payload}", "info")
        return response_payload

    def check_upload_status(
        self,
        page: Dict[str, Any],
        upload_id: str,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        response = self.requests_module.get(
            f"{self.base_url}/{upload_id}",
            params={
                "access_token": page.get("access_token", ""),
                "fields": "id,status",
            },
            timeout=60,
            proxies=proxy_config or None,
        )
        self._raise_for_graph_error(response, action="check_upload_status")
        response_payload = self._parse_json_or_empty(response)
        response_payload.setdefault("permalink_url", self._build_reel_permalink(upload_id))
        self._log(f"[Reels] check_upload_status id={upload_id} response={response_payload}", "info")
        return response_payload

    def finish_publish(
        self,
        page: Dict[str, Any],
        upload_id: str,
        *,
        message: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "access_token": page.get("access_token", ""),
            "upload_phase": "finish",
            "video_id": upload_id,
            "video_state": "PUBLISHED",
        }
        publish_description = description or message or title
        if publish_description:
            payload["description"] = publish_description

        response = self.requests_module.post(
            f"{self.base_url}/{page['id']}/video_reels",
            data=payload,
            timeout=60,
            proxies=proxy_config or None,
        )
        self._raise_for_graph_error(response, action="finish_publish")
        response_payload = self._parse_json_or_empty(response)
        response_payload.setdefault("permalink_url", self._build_reel_permalink(upload_id))
        post_id = response_payload.get("post_id") or response_payload.get("id")
        if post_id:
            self._post_ids[str(upload_id)] = str(post_id)
        self._log(f"[Reels] finish_publish id={upload_id} response={response_payload}", "info")
        return response_payload

    def wait_until_public(
        self,
        page: Dict[str, Any],
        upload_id: str,
        timeout_seconds: int = 120,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_payload: Dict[str, Any] = {}
        while time.time() < deadline:
            try:
                payload = self.check_upload_status(page, upload_id, proxy_config=proxy_config)
                last_payload = payload
                if self._is_public_payload(payload):
                    return payload
            except Exception as exc:
                self.logger.warning("Reels wait failed id=%s error=%s", upload_id, exc)
            time.sleep(5)
        return last_payload

    def get_permalink(
        self,
        page: Dict[str, Any],
        upload_id: str,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        payload = self.check_upload_status(page, upload_id, proxy_config=proxy_config)
        return payload.get("permalink_url") or payload.get("link") or self._build_reel_permalink(upload_id)

    def comment(
        self,
        page: Dict[str, Any],
        upload_id: str,
        comment_text: str,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        normalized_text = (comment_text or "").strip() or "Thanks!"
        response = self.requests_module.post(
            f"{self.base_url}/{upload_id}/comments",
            data={"access_token": page.get("access_token", ""), "message": normalized_text},
            timeout=60,
            proxies=proxy_config or None,
        )
        self._raise_for_graph_error(response, action="comment")
        return self._parse_json_or_empty(response)

    def verify_public_visibility(
        self,
        page: Dict[str, Any],
        upload_id: str,
        timeout_seconds: int = 120,
        *,
        proxy_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        payload = self.wait_until_public(page, upload_id, timeout_seconds=timeout_seconds, proxy_config=proxy_config)
        payload = dict(payload or {})
        permalink_url = payload.get("permalink_url") or payload.get("link")
        post_id = payload.get("post_id") or self._post_ids.get(str(upload_id))
        if post_id:
            payload["post_id"] = post_id

        if not permalink_url:
            permalink_url = self._build_reel_permalink(upload_id)
            payload["permalink_url"] = permalink_url

        if permalink_url:
            visible = False
            reason = "not checked"
            for attempt in range(3):
                visible, reason = self._request_public_page(permalink_url, proxy_config=proxy_config)
                if visible:
                    break
                time.sleep(3)
            payload["public_visibility_checked"] = True
            payload["public_visibility"] = visible
            payload["public_visibility_reason"] = reason
            if visible:
                self._log(f"[Reels] public visibility verified id={upload_id} url={permalink_url}", "info")
            else:
                self._log(
                    f"[Reels] public visibility not verified id={upload_id} url={permalink_url} reason={reason}",
                    "warning",
                )
        else:
            payload["public_visibility_checked"] = False
            payload["public_visibility"] = False
            payload["public_visibility_reason"] = "missing permalink_url"
            self._log(f"[Reels] public visibility not verified id={upload_id} reason=missing permalink_url", "warning")

        return payload
