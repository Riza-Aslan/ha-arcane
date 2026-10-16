"""Update entities: one per container, install = Arcane redeploy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ArcaneError
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator
from .entity import ArcaneEntity, add_dynamic


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    add_dynamic(
        entry,
        async_add_entities,
        "containers",
        lambda n: [ContainerUpdate(coordinator, entry, n)],
    )


def _short(value: str | None) -> str | None:
    if value and value.startswith("sha256:"):
        return value[7:19]
    return value or None


class ContainerUpdate(ArcaneEntity, UpdateEntity):
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(
        self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._item = name
        self._attr_name = f"Container {name}"
        self._attr_unique_id = f"{entry.entry_id}_container_update_{name}"

    @property
    def _raw(self) -> dict | None:
        return self.coordinator.data.containers.get(self._item)

    @property
    def _info(self) -> dict:
        return (self._raw or {}).get("updateInfo") or {}

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    @property
    def installed_version(self) -> str | None:
        info = self._info
        return info.get("currentVersion") or _short(info.get("currentDigest"))

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if not info or info.get("error"):
            return self.installed_version
        if not info.get("hasUpdate"):
            return self.installed_version
        return info.get("latestVersion") or _short(info.get("latestDigest")) or "update"

    @property
    def title(self) -> str | None:
        return (self._raw or {}).get("image")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info
        return {
            "update_type": info.get("updateType"),
            "last_checked": info.get("checkTime"),
            "check_error": info.get("error") or None,
        }

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        raw = self._raw
        if raw is None:
            raise HomeAssistantError("Container no longer exists")
        try:
            await self.coordinator.client.container_redeploy(raw["id"])
        except ArcaneError as err:
            raise HomeAssistantError(f"Arcane: {err}") from err
        await self.coordinator.async_request_refresh()
