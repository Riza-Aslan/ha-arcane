"""Data update coordinator for Arcane."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcaneAuthError, ArcaneClient, ArcaneError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type ArcaneConfigEntry = ConfigEntry[ArcaneCoordinator]


@dataclass
class ArcaneData:
    """Snapshot of one Arcane environment."""

    version: dict[str, Any] = field(default_factory=dict)
    container_counts: dict[str, Any] = field(default_factory=dict)
    project_counts: dict[str, Any] = field(default_factory=dict)
    image_updates: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, dict] = field(default_factory=dict)  # key: container name
    projects: dict[str, dict] = field(default_factory=dict)  # key: project name


def container_name(raw: dict) -> str:
    names = raw.get("names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(raw.get("id", ""))[:12]


class ArcaneCoordinator(DataUpdateCoordinator[ArcaneData]):
    """Polls Arcane."""

    config_entry: ArcaneConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ArcaneConfigEntry, client: ArcaneClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> ArcaneData:
        try:
            containers = await self.client.containers()
            projects = await self.client.projects()
            data = ArcaneData(
                container_counts=await self.client.container_counts(),
                project_counts=await self.client.project_counts(),
                image_updates=await self.client.image_update_summary(),
                containers={container_name(c): c for c in containers},
                projects={p["name"]: p for p in projects if p.get("name")},
            )
            try:
                data.version = await self.client.app_version()
            except ArcaneError:
                data.version = self.data.version if self.data else {}
        except ArcaneAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArcaneError as err:
            raise UpdateFailed(f"Arcane not reachable: {err}") from err
        return data
