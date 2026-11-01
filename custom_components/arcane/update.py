"""Update entities: one per container, install = Arcane redeploy."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ArcaneApiError, ArcaneAuthError, ArcaneError
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator
from .entity import ArcaneItemEntity, add_dynamic

_LOGGER = logging.getLogger(__name__)


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
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

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
        # HA's update panel shows the *device* (host) as headline and `title` + version
        # below it, so the container name has to be part of the title.
        raw = self._raw or {}
        image = raw.get("image")
        return f"{self._item} ({image})" if image else self._item

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info
        return {
            "update_type": info.get("updateType"),
            "last_checked": info.get("checkTime"),
            "check_error": info.get("error") or None,
        }

    @property
    def in_progress(self) -> bool:
        return self.coordinator.is_upgrading(self._env_id, self._item)

    @property
    def available(self) -> bool:
        # stay visible while the host restarts as part of the update
        if self.in_progress and self.raw is not None:
            return True
        return super().available

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        raw = self._raw
        if raw is None:
            raise HomeAssistantError("Container no longer exists")
        client, coord = self.coordinator.client, self.coordinator
        # Arcane's own agent/manager: redeploy is refused, use the self-upgrade, which
        # returns at once and replaces the container (and restarts the host's agent).
        self_upgrade = bool(raw.get("redeployDisabled"))
        coord.mark_upgrading(self._env_id, self._item, raw.get("id"), blocking=not self_upgrade)
        try:
            if self_upgrade:
                try:
                    await client.system_upgrade(self._env_id)
                except ArcaneAuthError as err:
                    raise HomeAssistantError(
                        "Arcane refused the upgrade (HTTP 401/403). The API key needs the "
                        "permission system:upgrade."
                    ) from err
                except ArcaneApiError as err:
                    if err.status != 409:
                        raise
                    _LOGGER.info("Upgrade of %s is already running", self._item)
            else:
                await client.container_redeploy(self._env_id, raw["id"])
        except HomeAssistantError:
            coord.unmark_upgrading(self._env_id, self._item)
            raise
        except ArcaneError as err:
            coord.unmark_upgrading(self._env_id, self._item)
            raise HomeAssistantError(f"Arcane: {err}") from err
        if not self_upgrade:
            coord.unmark_upgrading(self._env_id, self._item)
        await coord.async_request_refresh()
