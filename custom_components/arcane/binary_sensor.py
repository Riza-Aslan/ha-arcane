"""Per-host connectivity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

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
        "environments",
        lambda env_id, _n: [HostOnline(coordinator, entry, env_id)],
    )


class HostOnline(ArcaneEntity, BinarySensorEntity):
    """On while Arcane can read the host. Stays available when the host is down."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "host_online"
    _requires_online = False

    def __init__(self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, env_id: str) -> None:
        super().__init__(coordinator, entry, env_id, "host_online")

    @property
    def is_on(self) -> bool | None:
        env = self.env
        return None if env is None else env.ok

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        env = self.env
        return {} if env is None else {"status": env.status, "enabled": env.enabled}
