"""Authenticate private web proxies without trusting forwarded client headers."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyIdentity:
    peers: frozenset[str]
    secret: str


@dataclass(frozen=True)
class CloudAccess:
    service_role: str
    proxies: dict[str, ProxyIdentity]
    origins: frozenset[str]

    @classmethod
    def from_environment(cls) -> CloudAccess | None:
        mode = os.getenv("DEPLOYMENT_MODE", "local").strip()
        if mode == "local":
            return None
        if mode != "cloud":
            raise ValueError("DEPLOYMENT_MODE must be local or cloud")
        role = os.getenv("SERVICE_ROLE", "")
        if role not in {"main-api", "fault-api"}:
            raise ValueError("Cloud API SERVICE_ROLE is invalid")
        raw = json.loads(os.environ.get("CLOUD_PROXY_IDENTITIES", "{}"))
        required = {"plan-web", "game-web"} if role == "main-api" else {"plan-web"}
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError("Cloud proxy identities must match the API role")
        proxies = {}
        for name, config in raw.items():
            if not isinstance(config, dict) or set(config) != {"peers", "secret"}:
                raise ValueError("Invalid cloud proxy contract")
            if not isinstance(config["secret"], str) or len(config["secret"]) < 32:
                raise ValueError("Cloud proxy proof must contain at least 32 characters")
            if not isinstance(config["peers"], list) or not config["peers"]:
                raise ValueError("Cloud proxy peers must be explicit IP addresses")
            peers = frozenset(str(ipaddress.ip_address(peer)) for peer in config["peers"])
            if any(
                not ipaddress.ip_address(peer).is_private
                or ipaddress.ip_address(peer).is_loopback
                or ipaddress.ip_address(peer).is_unspecified
                for peer in peers
            ):
                raise ValueError("Cloud proxies must connect through private addresses")
            proxies[name] = ProxyIdentity(peers, config["secret"])
        if len({proxy.secret for proxy in proxies.values()}) != len(proxies):
            raise ValueError("Cloud proxies require distinct proofs")
        origins = frozenset(
            origin.strip()
            for origin in os.getenv("APP_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        if not origins or any(not origin.startswith("https://") for origin in origins):
            raise ValueError("Cloud access requires explicit HTTPS origins")
        return cls(role, proxies, origins)

    def permits(self, *, host: str, identity: str, secret: str, path: str) -> bool:
        proxy = self.proxies.get(identity)
        try:
            peer = str(ipaddress.ip_address(host))
        except ValueError:
            return False
        if (
            proxy is None
            or peer not in proxy.peers
            or not hmac.compare_digest(secret.encode("utf-8"), proxy.secret.encode("utf-8"))
        ):
            return False
        test_path = path == "/api/test" or path.startswith("/api/test/")
        if self.service_role == "fault-api":
            return identity == "plan-web" and test_path
        if test_path:
            return False
        if identity == "game-web":
            return path == "/api/game" or path.startswith("/api/game/")
        return identity == "plan-web"
