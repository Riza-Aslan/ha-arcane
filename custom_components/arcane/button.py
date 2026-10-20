"""Buttons: restart containers/projects, check updates per host, clean up."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ArcaneError
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator
from .entity import ArcaneEntity, ArcaneInstanceEntity, ArcaneItemEntity, add_dynamic


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([CleanupButton(coordinator, entry)])
    add_dynamic(
        entry, async_add_entities, "environments",
        lambda env_id, _n: [CheckUpdatesButton(coordinator, entry, env_id)],
    )
    add_dynamic(
        entry, async_add_entities, "containers",
        lambda env_id, n: [ContainerRestartButton(coordinator, entry, env_id, "container_restart", n)],
    )
    add_dynamic(
        entry, async_add_entities, "projects",
        lambda env_id, n: [ProjectRestartButton(coordinator, entry, env_id, "project_restart", n)],
    )


async def _run(coordinator: ArcaneCoordinator, coro) -> None:
    try:
        await coro
    except ArcaneError as err:
        raise HomeAssistantError(f"Arcane: {err}") from err
    await coordinator.async_request_refresh()


class CleanupButton(ArcaneInstanceEntity, ButtonEntity):
    """Remove entities of containers/projects/hosts that are gone, without waiting."""

    _attr_translation_key = "cleanup_missing"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry) -> None:
        super().__init__(coordinator, entry, "cleanup_missing")

    async def async_press(self) -> None:
        await self.coordinator.async_cleanup_now()
        await self.coordinator.async_request_refresh()


class CheckUpdatesButton(ArcaneEntity, ButtonEntity):
    _attr_translation_key = "check_image_updates"

    def __init__(self, coordinator: ArcaneCoordinator, entry, env_id: str) -> None:
        super().__init__(coordinator, entry, env_id, "check_image_updates")

    async def async_press(self) -> None:
        await _run(self.coordinator, self.coordinator.client.check_all_image_updates(self._env_id))


class ContainerRestartButton(ArcaneItemEntity, ButtonEntity):
    _collection = "containers"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"Container {name} restart"

    async def async_press(self) -> None:
        await _run(
            self.coordinator,
            self.coordinator.client.container_action(self._env_id, self.raw["id"], "restart"),
        )


class ProjectRestartButton(ArcaneItemEntity, ButtonEntity):
    _collection = "projects"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator, entry, env_id, kind, name) -> None:
        super().__init__(coordinator, entry, env_id, kind, name)
        self._attr_name = f"Project {name} restart"

    async def async_press(self) -> None:
        await _run(
            self.coordinator,
            self.coordinator.client.project_restart(self._env_id, self.raw["id"]),
        )
