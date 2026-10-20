"""Start/stop switches for containers and projects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        entry, async_add_entities, "containers",
        lambda env_id, n: [ContainerSwitch(coordinator, entry, env_id, "container", n)],
    )
    add_dynamic(
        entry, async_add_entities, "projects",
        lambda env_id, n: [ProjectSwitch(coordinator, entry, env_id, "project", n)],
    )


class _ArcaneSwitch(ArcaneItemEntity, SwitchEntity):
    _label = ""

    def __init__(self, coordinator: ArcaneCoordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"{self._label} {name}"

    async def _run(self, coro) -> None:
        try:
            await coro
        except ArcaneError as err:
            raise HomeAssistantError(f"Arcane: {err}") from err
        await self.coordinator.async_request_refresh()


class ContainerSwitch(_ArcaneSwitch):
    _collection = "containers"
    _label = "Container"

    @property
    def is_on(self) -> bool | None:
        raw = self.raw
        return None if raw is None else raw.get("state") == "running"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self.raw or {}
        return {"image": raw.get("image"), "status": raw.get("status"), "state": raw.get("state")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(
            self.coordinator.client.container_action(self._env_id, self.raw["id"], "start")
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(
            self.coordinator.client.container_action(self._env_id, self.raw["id"], "stop")
        )


class ProjectSwitch(_ArcaneSwitch):
    _collection = "projects"
    _label = "Project"

    @property
    def is_on(self) -> bool | None:
        raw = self.raw
        return None if raw is None else raw.get("status") in ("running", "partially running")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"status": (self.raw or {}).get("status")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.project_up(self._env_id, self.raw["id"]))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.project_down(self._env_id, self.raw["id"]))
