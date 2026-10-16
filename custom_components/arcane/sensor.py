"""Instance-wide sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneConfigEntry, ArcaneCoordinator, ArcaneData
from .entity import ArcaneEntity


@dataclass(frozen=True, kw_only=True)
class ArcaneSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ArcaneData], Any]


def _get(section: str, key: str) -> Callable[[ArcaneData], Any]:
    return lambda d: getattr(d, section).get(key)


SENSORS: tuple[ArcaneSensorDescription, ...] = tuple(
    ArcaneSensorDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_get(section, field),
    )
    for key, section, field in (
        ("containers_running", "container_counts", "runningContainers"),
        ("containers_stopped", "container_counts", "stoppedContainers"),
        ("containers_total", "container_counts", "totalContainers"),
        ("projects_running", "project_counts", "runningProjects"),
        ("projects_stopped", "project_counts", "stoppedProjects"),
        ("projects_total", "project_counts", "totalProjects"),
        ("images_with_updates", "image_updates", "imagesWithUpdates"),
        ("image_update_errors", "image_updates", "errorsCount"),
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ArcaneSensor(coordinator, entry, d) for d in SENSORS)


class ArcaneSensor(ArcaneEntity, SensorEntity):
    entity_description: ArcaneSensorDescription

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ArcaneConfigEntry,
        description: ArcaneSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)
