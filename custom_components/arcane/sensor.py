"""Per-host sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneConfigEntry, ArcaneCoordinator, EnvData, health
from .entity import ArcaneEntity, ArcaneItemEntity, add_dynamic


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


def _pct(used: Any, total: Any) -> float | None:
    if not isinstance(used, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
        return None
    return round(used / total * 100, 1)


def _unhealthy(env: EnvData) -> int:
    return sum(1 for c in env.containers.values() if health(c) == "unhealthy")


SENSORS = SENSORS + (
    ArcaneSensorDescription(
        key="containers_unhealthy",
        translation_key="containers_unhealthy",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_unhealthy,
    ),
    *(
        ArcaneSensorDescription(
            key=key,
            translation_key=key,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=_get(section, field),
        )
        for key, section, field in (
            ("images_total", "image_counts", "totalImages"),
            ("images_unused", "image_counts", "imagesUnused"),
            ("volumes_total", "volume_counts", "total"),
            ("volumes_unused", "volume_counts", "unused"),
            ("networks_total", "network_counts", "total"),
        )
    ),
    ArcaneSensorDescription(
        key="image_storage",
        translation_key="image_storage",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: (
            None
            if e.image_counts.get("totalImageSize") is None
            else e.image_counts["totalImageSize"] / 1e9
        ),
    ),
    ArcaneSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.stats.get("cpuUsage"),
    ),
    ArcaneSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _pct(e.stats.get("memoryUsage"), e.stats.get("memoryTotal")),
    ),
    ArcaneSensorDescription(
        key="disk_usage",
        translation_key="disk_usage",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _pct(e.stats.get("diskUsage"), e.stats.get("diskTotal")),
    ),
    ArcaneSensorDescription(
        key="docker_version",
        translation_key="docker_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.docker.get("ServerVersion"),
    ),
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


    add_dynamic(
        entry, async_add_entities, "containers",
        lambda env_id, n: [ContainerState(coordinator, entry, env_id, "container_state", n)],
    )
    add_dynamic(
        entry, async_add_entities, "projects",
        lambda env_id, n: [ProjectServices(coordinator, entry, env_id, "project_services", n)],
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


class ContainerState(ArcaneItemEntity, SensorEntity):
    """Docker's own state text of a container (running, exited, paused ...)."""

    _collection = "containers"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"Container {name} state"

    @property
    def native_value(self) -> str | None:
        raw = self.raw
        return None if raw is None else raw.get("state")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self.raw or {}
        return {"status": raw.get("status"), "image": raw.get("image"), "created": raw.get("created")}


class ProjectServices(ArcaneItemEntity, SensorEntity):
    """Running services of a Compose project; the total is an attribute."""

    _collection = "projects"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"Project {name} services running"

    @property
    def native_value(self) -> int | None:
        raw = self.raw
        return None if raw is None else raw.get("runningCount")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self.raw or {}
        return {"services": raw.get("serviceCount"), "status": raw.get("status")}
