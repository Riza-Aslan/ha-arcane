"""Config flow for Arcane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import ArcaneAuthError, ArcaneClient, ArcaneError
from .const import (
    CONF_API_KEY,
    CONF_ENVIRONMENTS,
    CONF_STALE_HOURS,
    DEFAULT_STALE_HOURS,
    DOMAIN,
)


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> list[dict]:
    client = ArcaneClient(async_get_clientsession(hass), data[CONF_URL], data[CONF_API_KEY])
    await client.health()
    return await client.environments()  # needs a valid key with environments:list


class ArcaneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arcane."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ArcaneOptionsFlow:
        return ArcaneOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_URL: user_input[CONF_URL].strip().rstrip("/")}
            await self.async_set_unique_id(user_input[CONF_URL])
            self._abort_if_unique_id_configured()
            try:
                await _validate(self.hass, user_input)
            except ArcaneAuthError:
                errors["base"] = "invalid_auth"
            except ArcaneError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Arcane ({user_input[CONF_URL].split('//')[-1]})",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
            try:
                await _validate(self.hass, data)
            except ArcaneAuthError:
                errors["base"] = "invalid_auth"
            except ArcaneError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )


class ArcaneOptionsFlow(OptionsFlowWithReload):
    """Pick the hosts to show and how long to wait before removing vanished items."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        coordinator = getattr(self.config_entry, "runtime_data", None)
        hosts = {}
        if coordinator is not None and coordinator.data:
            hosts = {e.id: e.name for e in coordinator.data.environments.values()}
        for env_id in self.config_entry.options.get(CONF_ENVIRONMENTS) or []:
            hosts.setdefault(str(env_id), str(env_id))
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENVIRONMENTS, default=list(opts.get(CONF_ENVIRONMENTS) or [])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[SelectOptionDict(value=k, label=v) for k, v in hosts.items()],
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_STALE_HOURS, default=opts.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=720, step=1, mode=NumberSelectorMode.BOX,
                            unit_of_measurement="h",
                        )
                    ),
                }
            ),
        )
