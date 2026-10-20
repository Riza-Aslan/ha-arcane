"""Minimal async client for the Arcane REST API."""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from .const import PAGE_SIZE


class ArcaneError(Exception):
    """Base error for Arcane API problems."""


class ArcaneConnectionError(ArcaneError):
    """Arcane is not reachable."""


class ArcaneAuthError(ArcaneError):
    """The API key was rejected."""


class ArcaneApiError(ArcaneError):
    """Arcane answered with an error."""


class ArcaneClient:
    """Talks to an Arcane manager; every call names its environment (host)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/api"
        self._headers = {"X-Api-Key": api_key, "Accept": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: float = 30,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                self._base + path,
                params=params,
                headers=self._headers if auth else {"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise ArcaneAuthError(f"HTTP {resp.status} for {path}")
                if resp.status >= 400:
                    text = (await resp.text())[:200]
                    raise ArcaneApiError(f"HTTP {resp.status} for {path}: {text}")
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ArcaneConnectionError(str(err)) from err

    async def _stream(self, method: str, path: str, timeout: float = 600) -> None:
        """Run a streaming (NDJSON) operation until `done` or `error`."""
        try:
            async with self._session.request(
                method,
                self._base + path,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout, sock_read=timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise ArcaneAuthError(f"HTTP {resp.status} for {path}")
                if resp.status >= 400:
                    text = (await resp.text())[:200]
                    raise ArcaneApiError(f"HTTP {resp.status} for {path}: {text}")
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        if msg.get("error"):
                            raise ArcaneApiError(str(msg["error"]))
                        if msg.get("done"):
                            return
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ArcaneConnectionError(str(err)) from err

    async def _paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        items: list[dict] = []
        start = 0
        while True:
            query = {**(params or {}), "start": start, "limit": PAGE_SIZE}
            body = await self._request("GET", path, params=query)
            data = (body or {}).get("data") or []
            items.extend(data)
            total = ((body or {}).get("pagination") or {}).get("totalItems", len(items))
            start += PAGE_SIZE
            if not data or len(items) >= total:
                return items

    @staticmethod
    def _env(env_id: str) -> str:
        return f"/environments/{env_id}"

    # --- read ---------------------------------------------------------------

    async def health(self) -> bool:
        """Unauthenticated liveness check."""
        await self._request("GET", "/health", auth=False, timeout=10)
        return True

    async def app_version(self) -> dict[str, Any]:
        body = await self._request("GET", "/app-version", auth=False)
        return (body or {}).get("data", body) or {}

    async def environments(self) -> list[dict]:
        return await self._paged("/environments")

    async def container_counts(self, env_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"{self._env(env_id)}/containers/counts")
        return (body or {}).get("data", {})

    async def project_counts(self, env_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"{self._env(env_id)}/projects/counts")
        return (body or {}).get("data", {})

    async def image_update_summary(self, env_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"{self._env(env_id)}/image-updates/summary")
        return (body or {}).get("data", {})

    async def containers(self, env_id: str) -> list[dict]:
        return await self._paged(f"{self._env(env_id)}/containers")

    async def projects(self, env_id: str) -> list[dict]:
        return await self._paged(f"{self._env(env_id)}/projects")

    # --- actions ------------------------------------------------------------

    async def container_action(self, env_id: str, container_id: str, action: str) -> None:
        """action: start | stop | restart."""
        await self._request(
            "POST", f"{self._env(env_id)}/containers/{container_id}/{action}", timeout=120
        )

    async def container_redeploy(self, env_id: str, container_id: str) -> None:
        await self._request(
            "POST", f"{self._env(env_id)}/containers/{container_id}/redeploy", timeout=600
        )

    async def project_up(self, env_id: str, project_id: str) -> None:
        await self._stream("POST", f"{self._env(env_id)}/projects/{project_id}/up")

    async def project_down(self, env_id: str, project_id: str) -> None:
        await self._request(
            "POST", f"{self._env(env_id)}/projects/{project_id}/down", timeout=300
        )

    async def project_restart(self, env_id: str, project_id: str) -> None:
        await self._request(
            "POST", f"{self._env(env_id)}/projects/{project_id}/restart", timeout=300
        )

    async def check_all_image_updates(self, env_id: str) -> None:
        await self._request(
            "POST", f"{self._env(env_id)}/image-updates/check-all", timeout=600
        )
