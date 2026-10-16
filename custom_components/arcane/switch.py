"""Start/stop switches for containers and projects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        lambda n: [ContainerSwitch(coordinator, entry, n)],
    )
    add_dynamic(
        entry,
        async_add_entities,
        "projects",
        lambda n: [ProjectSwitch(coordinator, entry, n)],
    )


class _ArcaneSwitch(ArcaneEntity, SwitchEntity):
    _kind = ""

    def __init__(
        self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._item = name
        self._attr_name = f"{self._kind} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{self._kind.lower()}_{name}"

    async def _run(self, coro) -> None:
        try:
            await coro
        except ArcaneError as err:
            raise HomeAssistantError(f"Arcane: {err}") from err
        await self.coordinator.async_request_refresh()


class ContainerSwitch(_ArcaneSwitch):
    _kind = "Container"

    @property
    def _raw(self) -> dict | None:
        return self.coordinator.data.containers.get(self._item)

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    @property
    def is_on(self) -> bool | None:
        raw = self._raw
        return None if raw is None else raw.get("state") == "running"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self._raw or {}
        return {"image": raw.get("image"), "status": raw.get("status"), "state": raw.get("state")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.container_action(self._raw["id"], "start"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.container_action(self._raw["id"], "stop"))


class ProjectSwitch(_ArcaneSwitch):
    _kind = "Project"

    @property
    def _raw(self) -> dict | None:
        return self.coordinator.data.projects.get(self._item)

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    @property
    def is_on(self) -> bool | None:
        raw = self._raw
        return None if raw is None else raw.get("status") in ("running", "partially running")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = self._raw or {}
        return {"status": raw.get("status")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.project_up(self._raw["id"]))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.coordinator.client.project_down(self._raw["id"]))
