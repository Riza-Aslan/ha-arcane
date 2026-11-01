"""The Arcane integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcaneClient
from .const import CONF_API_KEY, CONF_ENVIRONMENT_ID, CONF_ENVIRONMENTS, DOMAIN, SEP
from .coordinator import ArcaneConfigEntry, ArcaneCoordinator
from .entity import instance_device_info
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

# v1 unique_ids were "<entry_id>_<rest>"; longer prefixes first.
_V1_KINDS = (
    ("container_restart_", "container_restart"),
    ("container_update_", "container_update"),
    ("project_restart_", "project_restart"),
    ("container_", "container"),
    ("project_", "project"),
)


def migrate_unique_id(entry_id: str, env_id: str, unique_id: str) -> str | None:
    """Map a v1 unique_id to the v2 scheme (None = not a v1 id)."""
    prefix = f"{entry_id}_"
    if not unique_id.startswith(prefix):
        return None
    rest = unique_id[len(prefix):]
    for old, kind in _V1_KINDS:
        if rest.startswith(old):
            return SEP.join([entry_id, env_id, kind, rest[len(old):]])
    return SEP.join([entry_id, env_id, rest])  # per-host sensor/button key


async def async_migrate_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> bool:
    """v1 (one environment per entry) -> v2 (all environments, one device each).

    Entities keep their entity_id (and so their history); only the unique_id changes.
    """
    if entry.version == 1:
        env_id = str(entry.data.get(CONF_ENVIRONMENT_ID, "0"))

        def _update(ent: er.RegistryEntry) -> dict[str, str] | None:
            new = migrate_unique_id(entry.entry_id, env_id, ent.unique_id or "")
            return {"new_unique_id": new} if new else None

        await er.async_migrate_entries(hass, entry.entry_id, _update)
        data = {k: v for k, v in entry.data.items() if k != CONF_ENVIRONMENT_ID}
        options = dict(entry.options)
        if env_id != "0":  # the old entry only ever looked at this one host
            options.setdefault(CONF_ENVIRONMENTS, [env_id])
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, unique_id=data[CONF_URL], version=2
        )
        _LOGGER.info("Migrated Arcane entry %s to version 2", entry.entry_id)
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> bool:
    client = ArcaneClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_KEY]
    )
    coordinator = ArcaneCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # The manager is the parent of every host device: create it first so hosts can point to its id.
    manager = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **instance_device_info(entry, coordinator.data)
    )
    coordinator.manager_device_id = manager.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.async_clear_issues()
    return unloaded
