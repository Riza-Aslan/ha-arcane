"""Config flow for Arcane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcaneAuthError, ArcaneClient, ArcaneError
from .const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT_ID,
    DEFAULT_ENVIRONMENT_ID,
    DOMAIN,
)


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    client = ArcaneClient(
        async_get_clientsession(hass),
        data[CONF_URL],
        data[CONF_API_KEY],
        data.get(CONF_ENVIRONMENT_ID, DEFAULT_ENVIRONMENT_ID),
    )
    await client.health()
    await client.container_counts()  # needs a valid key + valid environment


class ArcaneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arcane."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_URL: user_input[CONF_URL].strip().rstrip("/"),
            }
            await self.async_set_unique_id(
                f"{user_input[CONF_URL]}|{user_input[CONF_ENVIRONMENT_ID]}"
            )
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
                    vol.Required(
                        CONF_URL,
                        default=(user_input or {}).get(CONF_URL, "http://"),
                    ): str,
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(
                        CONF_ENVIRONMENT_ID,
                        default=(user_input or {}).get(
                            CONF_ENVIRONMENT_ID, DEFAULT_ENVIRONMENT_ID
                        ),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
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
