"""Shared entity helpers: one device per Arcane environment (= Docker host)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INSTANCE, SEP
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator, ArcaneData, EnvData


def instance_device_info(entry: ArcaneConfigEntry, data: ArcaneData | None) -> DeviceInfo:
    version = data.version if data else {}
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Arcane",
        manufacturer="Arcane",
        model="Docker management",
        sw_version=version.get("displayVersion") or version.get("currentVersion"),
        configuration_url=entry.data[CONF_URL],
    )


def env_device_info(entry: ArcaneConfigEntry, env: EnvData) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}{SEP}{env.id}")},
        name=env.name,
        manufacturer="Arcane",
        model="Docker host (agent)" if env.is_edge else "Docker host (local)",
        via_device=(DOMAIN, entry.entry_id),
        configuration_url=entry.data[CONF_URL],
    )


class ArcaneInstanceEntity(CoordinatorEntity[ArcaneCoordinator]):
    """Entity of the Arcane manager itself."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ArcaneCoordinator, entry: ArcaneConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = SEP.join([entry.entry_id, INSTANCE, key])
        self._attr_device_info = instance_device_info(entry, coordinator.data)


class ArcaneEntity(CoordinatorEntity[ArcaneCoordinator]):
    """Entity that belongs to one host."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ArcaneConfigEntry,
        env_id: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._env_id = env_id
        self._attr_unique_id = SEP.join([entry.entry_id, env_id, suffix])
        self._attr_device_info = env_device_info(entry, coordinator.data.environments[env_id])

    @property
    def env(self) -> EnvData | None:
        return self.coordinator.data.environments.get(self._env_id)

    @property
    def available(self) -> bool:
        env = self.env
        return super().available and env is not None and env.ok


class ArcaneItemEntity(ArcaneEntity):
    """A container or project of one host. `kind` is part of the unique_id."""

    _collection = ""  # "containers" or "projects"

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        entry: ArcaneConfigEntry,
        env_id: str,
        kind: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry, env_id, f"{kind}{SEP}{name}")
        self._item = name

    @property
    def raw(self) -> dict | None:
        env = self.env
        return None if env is None else getattr(env, self._collection).get(self._item)

    @property
    def available(self) -> bool:
        return super().available and self.raw is not None


def _keys(data: ArcaneData, collection: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for env in data.environments.values():
        if collection == "environments":
            out.add((env.id, ""))
        else:
            out.update((env.id, n) for n in getattr(env, collection))
    return out


def add_dynamic(
    entry: ArcaneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    collection: str,
    factory: Callable[[str, str], list],
) -> None:
    """Add entities now and whenever new hosts/containers/projects appear.

    collection: "environments", "containers" or "projects".
    factory(env_id, name) returns the entities for one item.
    """
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _check() -> None:
        new = _keys(coordinator.data, collection) - known
        if not new:
            return
        known.update(new)
        async_add_entities([e for env_id, name in sorted(new) for e in factory(env_id, name)])

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
