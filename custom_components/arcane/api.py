"""Minimal async client for the Arcane REST API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .const import PAGE_SIZE


class ArcaneError(Exception):
    """Base error for Arcane API problems."""


class ArcaneConnectionError(ArcaneError):
    """Arcane is not reachable."""


class ArcaneAuthError(ArcaneError):
    """The API key was rejected."""


class ArcaneApiError(ArcaneError):
    """Arcane answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


async def guarded[T](awaitable: Awaitable[T], permission: str | None = None) -> T:
    """Await an API call and turn Arcane errors into readable Home Assistant errors."""
    try:
        return await awaitable
    except ArcaneAuthError as err:
        hint = f" The API key needs the permission {permission}." if permission else ""
        raise HomeAssistantError(f"Arcane refused the request (HTTP 401/403).{hint}") from err
    except ArcaneError as err:
        raise HomeAssistantError(f"Arcane: {err}") from err


class ArcaneClient:
    """Talks to an Arcane manager; every call names its environment (host)."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/api"
        self._headers = {"X-Api-Key": api_key, "Accept": "application/json"}
        self.read_only = False  # refuse every write (set from the options)

    def _guard_write(self, method: str, path: str) -> None:
        # checking images for updates only reads from registries, it changes nothing
        if self.read_only and method != "GET" and not path.endswith("/image-updates/check-all"):
            raise ArcaneApiError("Control is switched off in the Arcane options (read only)")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        auth: bool = True,
        timeout: float = 30,
    ) -> Any:
        self._guard_write(method, path)
        try:
            async with self._session.request(
                method,
                self._base + path,
                params=params,
                json=json_body,
                headers=self._headers if auth else {"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise ArcaneAuthError(f"HTTP {resp.status} for {path}")
                if resp.status >= 400:
                    text = (await resp.text())[:200]
                    raise ArcaneApiError(f"HTTP {resp.status} for {path}: {text}", resp.status)
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ArcaneConnectionError(str(err)) from err

    async def _stream(self, method: str, path: str, timeout: float = 600) -> None:
        """Run a streaming (NDJSON) operation until `done` or `error`."""
        self._guard_write(method, path)
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
                    raise ArcaneApiError(f"HTTP {resp.status} for {path}: {text}", resp.status)
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

    async def _data(self, path: str) -> dict[str, Any]:
        body = await self._request("GET", path)
        return (body or {}).get("data", {}) or {}

    async def image_counts(self, env_id: str) -> dict[str, Any]:
        return await self._data(f"{self._env(env_id)}/images/counts")

    async def volume_counts(self, env_id: str) -> dict[str, Any]:
        return await self._data(f"{self._env(env_id)}/volumes/counts")

    async def network_counts(self, env_id: str) -> dict[str, Any]:
        return await self._data(f"{self._env(env_id)}/networks/counts")

    async def docker_info(self, env_id: str) -> dict[str, Any]:
        """Docker daemon info (not wrapped in the usual envelope)."""
        body = await self._request("GET", f"{self._env(env_id)}/system/docker/info")
        return (body or {}).get("data", body) or {}

    async def system_stats(self, env_id: str) -> dict[str, Any]:
        """One sample of the host's CPU/memory/disk (Arcane only offers it as a websocket)."""
        path = f"{self._env(env_id)}/ws/system/stats"
        try:
            async with asyncio.timeout(15):
                async with self._session.ws_connect(
                    self._base + path, params={"interval": 2}, headers=self._headers
                ) as ws:
                    msg = await ws.receive()
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        return json.loads(msg.data)
                    raise ArcaneApiError(f"No statistics received from {path}")
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise ArcaneAuthError(f"HTTP {err.status} for {path}") from err
            raise ArcaneApiError(f"HTTP {err.status} for {path}", err.status) from err
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ArcaneConnectionError(str(err)) from err

    async def prune_images(self, env_id: str) -> dict[str, Any]:
        """Remove every image no container uses (needs `images:prune`). Touches nothing else."""
        body = await self._request(
            "POST", f"{self._env(env_id)}/images/prune", json_body={"mode": "all"}, timeout=600
        )
        return (body or {}).get("data") or {}

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

    async def system_upgrade(self, env_id: str) -> None:
        """Self-upgrade of an Arcane agent/manager (needs `system:upgrade`).

        Containers running Arcane itself cannot be redeployed (Arcane refuses, the
        container would kill its own request); this starts a separate upgrader instead.
        """
        await self._request("POST", f"{self._env(env_id)}/system/upgrade", timeout=120)

    async def project_redeploy(self, env_id: str, project_id: str) -> None:
        """Pull the images and recreate the project (streams until done)."""
        await self._stream(
            "POST", f"{self._env(env_id)}/projects/{project_id}/redeploy", timeout=1200
        )

    async def set_container_auto_update(
        self, env_id: str, container_id: str, enabled: bool
    ) -> None:
        await self._request(
            "PUT",
            f"{self._env(env_id)}/containers/{container_id}/auto-update",
            json_body={"enabled": enabled},
        )

    async def run_updater(self, env_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Apply all pending updates of a host (honours Arcane's opt-out labels)."""
        body = await self._request(
            "POST",
            f"{self._env(env_id)}/updater/run",
            params={"automatic": "true"},
            json_body={"dryRun": dry_run},
            timeout=1800,
        )
        return (body or {}).get("data") or {}

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
