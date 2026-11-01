"""Services that act on a whole host (device) of Arcane."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    ATTR_DRY_RUN,
    DOMAIN,
    SEP,
    SERVICE_CHECK_UPDATES,
    SERVICE_UPDATE_ALL,
)
from .coordinator import ArcaneCoordinator

_DEVICES = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}
SCHEMA_CHECK = vol.Schema(_DEVICES)
SCHEMA_UPDATE_ALL = vol.Schema({**_DEVICES, vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean})


def _hosts(hass: HomeAssistant, device_ids: list[str]) -> list[tuple[ArcaneCoordinator, str, str]]:
    """Resolve host devices to (coordinator, environment id, name)."""
    registry = dr.async_get(hass)
    found: list[tuple[ArcaneCoordinator, str, str]] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unknown device {device_id}")
        for domain, ident in device.identifiers:
            if domain != DOMAIN:
                continue
            parts = ident.split(SEP)
            if len(parts) != 2:
                raise ServiceValidationError(
                    f"{device.name} is the Arcane manager, select one of its hosts instead"
                )
            entry = hass.config_entries.async_get_entry(parts[0])
            if entry is None or entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(f"The Arcane entry of {device.name} is not loaded")
            found.append((entry.runtime_data, parts[1], device.name_by_user or device.name or ""))
            break
        else:
            raise ServiceValidationError(f"{device.name} is not an Arcane host")
    return found


def async_register_services(hass: HomeAssistant) -> None:
    async def _check(call: ServiceCall) -> None:
        for coordinator, env_id, _name in _hosts(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_check_updates(env_id)

    async def _update_all(call: ServiceCall) -> dict[str, Any]:
        hosts: dict[str, Any] = {}
        for coordinator, env_id, name in _hosts(hass, call.data[ATTR_DEVICE_ID]):
            hosts[name] = await coordinator.async_update_all(
                env_id, dry_run=call.data[ATTR_DRY_RUN]
            )
        return {"hosts": hosts}

    hass.services.async_register(DOMAIN, SERVICE_CHECK_UPDATES, _check, schema=SCHEMA_CHECK)
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ALL,
        _update_all,
        schema=SCHEMA_UPDATE_ALL,
        supports_response=SupportsResponse.OPTIONAL,
    )
