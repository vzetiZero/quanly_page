import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("facebook_ui")


class ProxyProvider(ABC):
    def __init__(self, api_key: str, *, region: str = "random") -> None:
        self.api_key = api_key
        self.region = region

    @abstractmethod
    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        raise NotImplementedError


class KiotProxyProvider(ProxyProvider):
    def _request(self, endpoint: str, *, method: str = "GET", params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        try:
            url = f"https://api.kiotproxy.com/api/v1/proxies/{endpoint}"
            if method.upper() == "POST":
                response = requests.post(url, params=params or {}, timeout=30)
            else:
                response = requests.get(url, params=params or {}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, dict):
                    return data
                if isinstance(payload.get("proxy"), dict):
                    return payload["proxy"]
                if isinstance(payload.get("result"), dict):
                    return payload["result"]
            return None
        except Exception as exc:
            logger.warning("Lỗi KiotProxy endpoint %s: %s", endpoint, exc)
            return None

    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        params = {"key": self.api_key, "region": self.region}
        current_proxy = self._request("current", params=params)
        if isinstance(current_proxy, dict) and current_proxy.get("http"):
            return current_proxy
        new_proxy = self._request("new", params=params)
        if isinstance(new_proxy, dict) and new_proxy.get("http"):
            return new_proxy
        return None

    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        if not proxy_data:
            return
        params = {"key": self.api_key}
        proxy_id = proxy_data.get("id") or proxy_data.get("proxy_id") or proxy_data.get("uuid")
        if proxy_id:
            params["id"] = proxy_id
        self._request("out", method="POST", params=params)


class GenericProxyProvider(ProxyProvider):
    def __init__(self, api_key: str, *, region: str = "random", endpoint_template: str = "") -> None:
        super().__init__(api_key, region=region)
        self.endpoint_template = endpoint_template.strip()

    def _build_url(self) -> str:
        if self.endpoint_template:
            return self.endpoint_template.format(key=self.api_key, region=self.region)
        return ""

    def _parse_proxy_payload(self, payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("proxy"), dict):
                return payload["proxy"]
            if isinstance(payload.get("data"), dict):
                return payload["data"]
            if isinstance(payload.get("result"), dict):
                return payload["result"]
            if payload.get("http") or payload.get("https"):
                return payload
        if isinstance(payload, str):
            return {"http": payload, "https": payload}
        return None

    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        url = self._build_url()
        if not url:
            return None
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
            parsed = self._parse_proxy_payload(payload)
            if isinstance(parsed, dict) and parsed.get("http"):
                return parsed
            if isinstance(parsed, dict) and parsed.get("proxy"):
                return {"http": parsed["proxy"], "https": parsed["proxy"]}
        except Exception as exc:
            logger.warning("Lỗi GenericProxyProvider: %s", exc)
        return None

    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        return None


class ProxyController:
    def __init__(
        self,
        api_key: str = "",
        provider_name: str = "kiotproxy",
        region: str = "random",
        endpoint_template: str = "",
    ) -> None:
        self.api_key = api_key
        self.provider_name = provider_name
        self.region = region
        self.endpoint_template = endpoint_template
        self.enabled = bool(api_key)
        self._provider: Optional[ProxyProvider] = None

    def _build_provider(self) -> Optional[ProxyProvider]:
        if not self.enabled or not self.api_key:
            return None
        if self.provider_name == "generic":
            return GenericProxyProvider(self.api_key, region=self.region, endpoint_template=self.endpoint_template)
        return KiotProxyProvider(self.api_key, region=self.region)

    def fetch_proxy(self) -> Optional[Dict[str, Any]]:
        provider = self._provider or self._build_provider()
        self._provider = provider
        if not provider:
            return None
        proxy_info = provider.fetch_proxy()
        if isinstance(proxy_info, dict) and proxy_info.get("http"):
            return proxy_info
        return None

    def release_proxy(self, proxy_data: Optional[Dict[str, Any]]) -> None:
        if not self._provider:
            return
        self._provider.release_proxy(proxy_data)

    def build_proxy_config(self, proxy_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        if not proxy_data:
            return None
        http_proxy = proxy_data.get("http")
        if not http_proxy:
            return None
        return {
            "http": f"http://{http_proxy}",
            "https": f"http://{http_proxy}",
            "socks5": f"socks5://{proxy_data.get('socks5', http_proxy)}",
        }
