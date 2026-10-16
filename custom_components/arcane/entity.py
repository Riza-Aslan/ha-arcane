"""Shared entity helpers."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator


def instance_device(entry: ArcaneConfigEntry, coordinator: ArcaneCoordinator) -> DeviceInfo:
    version = coordinator.data.version if coordinator.data else {}
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Arcane",
        manufacturer="Arcane",
        model="Docker management",
        sw_version=version.get("displayVersion") or version.get("currentVersion"),
        configuration_url=entry.data[CONF_URL],
    )


class ArcaneEntity(CoordinatorEntity[ArcaneCoordinator]):
    """Base entity attached to the Arcane instance device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = instance_device(entry, coordinator)


def add_dynamic(
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    kind: str,
    factory: Callable[[str], list[ArcaneEntity]],
) -> None:
    """Add entities for containers/projects now and whenever new ones appear.

    kind: "containers" or "projects". factory(name) returns the entities of one item.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _check() -> None:
        current = set(getattr(coordinator.data, kind))
        new = current - known
        if not new:
            return
        known.update(new)
        async_add_entities([e for name in sorted(new) for e in factory(name)])

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
