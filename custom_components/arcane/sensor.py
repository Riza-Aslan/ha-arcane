"""Per-host sensors."""

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

from .coordinator import ArcaneConfigEntry, ArcaneCoordinator, EnvData
from .entity import ArcaneEntity, add_dynamic


@dataclass(frozen=True, kw_only=True)
class ArcaneSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EnvData], Any]


def _get(section: str, key: str) -> Callable[[EnvData], Any]:
    return lambda e: getattr(e, section).get(key)


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
    add_dynamic(
        entry,
        async_add_entities,
        "environments",
        lambda env_id, _n: [ArcaneSensor(coordinator, entry, env_id, d) for d in SENSORS],
    )


class ArcaneSensor(ArcaneEntity, SensorEntity):
    entity_description: ArcaneSensorDescription

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ArcaneConfigEntry,
        env_id: str,
        description: ArcaneSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, env_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        env = self.env
        return None if env is None else self.entity_description.value_fn(env)
