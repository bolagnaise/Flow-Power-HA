"""Config flow for Flow Power Sync integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api_clients import AmberClient
from .const import (
    CONF_AMBER_API_KEY,
    CONF_AMBER_SITE_ID,
    CONF_BASE_RATE,
    CONF_INCLUDE_GST,
    CONF_INCLUDE_NETWORK_TARIFF,
    CONF_NEM_REGION,
    CONF_NETWORK_FLAT_RATE,
    CONF_OTHER_FEES,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DEFAULT_INCLUDE_GST,
    DEFAULT_NETWORK_FLAT_RATE,
    DEFAULT_OTHER_FEES,
    DOMAIN,
    NEM_REGIONS,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_AMBER,
)

_LOGGER = logging.getLogger(__name__)


class FlowPowerSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Flow Power Sync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._amber_sites: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select price source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_PRICE_SOURCE] = user_input[CONF_PRICE_SOURCE]

            if user_input[CONF_PRICE_SOURCE] == PRICE_SOURCE_AMBER:
                return await self.async_step_amber()
            else:
                return await self.async_step_region()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_PRICE_SOURCE, default=PRICE_SOURCE_AEMO): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=PRICE_SOURCE_AEMO, label="AEMO (Direct wholesale)"),
                            selector.SelectOptionDict(value=PRICE_SOURCE_AMBER, label="Amber Electric"),
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_amber(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Amber API configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate Amber API key
            api_key = user_input[CONF_AMBER_API_KEY]

            try:
                async with aiohttp.ClientSession() as session:
                    client = AmberClient(session, api_key)
                    sites = await client.get_sites()

                    if not sites:
                        errors["base"] = "no_sites"
                    else:
                        self._data[CONF_AMBER_API_KEY] = api_key
                        self._amber_sites = sites

                        if len(sites) == 1:
                            self._data[CONF_AMBER_SITE_ID] = sites[0]["id"]
                            return await self.async_step_region()
                        else:
                            return await self.async_step_amber_site()

            except Exception as e:
                _LOGGER.error("Error validating Amber API key: %s", e)
                errors["base"] = "invalid_api_key"

        return self.async_show_form(
            step_id="amber",
            data_schema=vol.Schema({
                vol.Required(CONF_AMBER_API_KEY): str,
            }),
            errors=errors,
            description_placeholders={
                "amber_url": "https://app.amber.com.au/developers",
            },
        )

    async def async_step_amber_site(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Amber site selection."""
        if user_input is not None:
            self._data[CONF_AMBER_SITE_ID] = user_input[CONF_AMBER_SITE_ID]
            return await self.async_step_region()

        site_options = [
            selector.SelectOptionDict(
                value=site["id"],
                label=f"{site.get('nmi', 'Unknown NMI')} - {site.get('network', 'Unknown Network')}",
            )
            for site in self._amber_sites
        ]

        return self.async_show_form(
            step_id="amber_site",
            data_schema=vol.Schema({
                vol.Required(CONF_AMBER_SITE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=site_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_region(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle NEM region selection."""
        if user_input is not None:
            self._data[CONF_NEM_REGION] = user_input[CONF_NEM_REGION]
            return await self.async_step_pricing()

        region_options = [
            selector.SelectOptionDict(value=code, label=f"{code} - {name}")
            for code, name in NEM_REGIONS.items()
        ]

        return self.async_show_form(
            step_id="region",
            data_schema=vol.Schema({
                vol.Required(CONF_NEM_REGION, default="NSW1"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=region_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle pricing configuration."""
        if user_input is not None:
            self._data.update(user_input)

            # Create the entry
            title = f"Flow Power ({self._data[CONF_NEM_REGION]})"
            return self.async_create_entry(title=title, data=self._data)

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema({
                vol.Required(CONF_BASE_RATE, default=DEFAULT_BASE_RATE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_PEA_ENABLED, default=True): bool,
                vol.Optional(CONF_PEA_CUSTOM_VALUE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-50,
                        max=50,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_INCLUDE_NETWORK_TARIFF, default=False): bool,
                vol.Optional(CONF_NETWORK_FLAT_RATE, default=DEFAULT_NETWORK_FLAT_RATE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=50,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_OTHER_FEES, default=DEFAULT_OTHER_FEES): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=20,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_INCLUDE_GST, default=DEFAULT_INCLUDE_GST): bool,
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FlowPowerSyncOptionsFlow:
        """Get the options flow for this handler."""
        return FlowPowerSyncOptionsFlow(config_entry)


class FlowPowerSyncOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Flow Power Sync."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_BASE_RATE,
                    default=current.get(CONF_BASE_RATE, DEFAULT_BASE_RATE),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PEA_ENABLED,
                    default=current.get(CONF_PEA_ENABLED, True),
                ): bool,
                vol.Optional(
                    CONF_PEA_CUSTOM_VALUE,
                    description={"suggested_value": current.get(CONF_PEA_CUSTOM_VALUE)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-50,
                        max=50,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_INCLUDE_NETWORK_TARIFF,
                    default=current.get(CONF_INCLUDE_NETWORK_TARIFF, False),
                ): bool,
                vol.Optional(
                    CONF_NETWORK_FLAT_RATE,
                    default=current.get(CONF_NETWORK_FLAT_RATE, DEFAULT_NETWORK_FLAT_RATE),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=50,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_OTHER_FEES,
                    default=current.get(CONF_OTHER_FEES, DEFAULT_OTHER_FEES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=20,
                        step=0.1,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_INCLUDE_GST,
                    default=current.get(CONF_INCLUDE_GST, DEFAULT_INCLUDE_GST),
                ): bool,
            }),
        )
