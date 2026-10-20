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
from .entity import ArcaneItemEntity, add_dynamic


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
        lambda env_id, n: [ContainerUpdate(coordinator, entry, env_id, "container_update", n)],
    )


def _short(value: str | None) -> str | None:
    if value and value.startswith("sha256:"):
        return value[7:19]
    return value or None


class ContainerUpdate(ArcaneItemEntity, UpdateEntity):
    _collection = "containers"
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: ArcaneCoordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"Container {name}"

    @property
    def _raw(self) -> dict | None:
        return self.raw

    @property
    def _info(self) -> dict:
        return (self._raw or {}).get("updateInfo") or {}

    @property
    def installed_version(self) -> str | None:
        info = self._info
        if info.get("updateType") == "digest":
            return _short(info.get("currentDigest")) or info.get("currentVersion")
        return info.get("currentVersion") or _short(info.get("currentDigest"))

    @property
    def latest_version(self) -> str | None:
        info = self._info
        if not info or info.get("error") or not info.get("hasUpdate"):
            return self.installed_version
        if info.get("updateType") == "digest":
            latest = _short(info.get("latestDigest"))
        else:
            latest = info.get("latestVersion") or _short(info.get("latestDigest"))
        # never equal to the installed value while an update is pending
        return latest if latest and latest != self.installed_version else "update available"

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
            await self.coordinator.client.container_redeploy(self._env_id, raw["id"])
        except ArcaneError as err:
            raise HomeAssistantError(f"Arcane: {err}") from err
        await self.coordinator.async_request_refresh()
