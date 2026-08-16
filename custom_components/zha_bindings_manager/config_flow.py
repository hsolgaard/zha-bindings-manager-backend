"""Config flow for ZHA Bindings Manager Backend.

Deliberately zero required input — this integration has nothing to
configure. It's a storage backend the zha-binding-map-card frontend
detects on its own (see websocket_api.get_capabilities) and talks to
directly; the only thing a user does here is confirm they want it added.
Single-instance only: there's exactly one shared state store per Home
Assistant instance, so a second entry would be meaningless, not just
redundant.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class ZhaBindingsManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZHA Bindings Manager Backend."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Single confirmation step, no fields."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="ZHA Bindings Manager Backend", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
