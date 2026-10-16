"""Buttons: restart containers/projects, check for image updates."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
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
    async_add_entities([CheckUpdatesButton(coordinator, entry)])
    add_dynamic(
        entry,
        async_add_entities,
        "containers",
        lambda n: [ContainerRestartButton(coordinator, entry, n)],
    )
    add_dynamic(
        entry,
        async_add_entities,
        "projects",
        lambda n: [ProjectRestartButton(coordinator, entry, n)],
    )


class _ArcaneButton(ArcaneEntity, ButtonEntity):
    async def _run(self, coro) -> None:
        try:
            await coro
        except ArcaneError as err:
            raise HomeAssistantError(f"Arcane: {err}") from err
        await self.coordinator.async_request_refresh()


class CheckUpdatesButton(_ArcaneButton):
    _attr_translation_key = "check_image_updates"

    def __init__(self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_check_image_updates"

    async def async_press(self) -> None:
        await self._run(self.coordinator.client.check_all_image_updates())


class ContainerRestartButton(_ArcaneButton):
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._item = name
        self._attr_name = f"Container {name} restart"
        self._attr_unique_id = f"{entry.entry_id}_container_restart_{name}"

    @property
    def available(self) -> bool:
        return super().available and self._item in self.coordinator.data.containers

    async def async_press(self) -> None:
        cid = self.coordinator.data.containers[self._item]["id"]
        await self._run(self.coordinator.client.container_action(cid, "restart"))


class ProjectRestartButton(_ArcaneButton):
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._item = name
        self._attr_name = f"Project {name} restart"
        self._attr_unique_id = f"{entry.entry_id}_project_restart_{name}"

    @property
    def available(self) -> bool:
        return super().available and self._item in self.coordinator.data.projects

    async def async_press(self) -> None:
        pid = self.coordinator.data.projects[self._item]["id"]
        await self._run(self.coordinator.client.project_restart(pid))
