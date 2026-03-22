"""Config flow for Flow Power HA integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api_clients import AmberClient, FlowPowerPortalClient
from .const import (
    CONF_AMBER_API_KEY,
    CONF_AMBER_SITE_ID,
    CONF_BASE_RATE,
    CONF_FLOWPOWER_EMAIL,
    CONF_FLOWPOWER_PASSWORD,
    CONF_NEM_REGION,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DOMAIN,
    NEM_REGIONS,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_AMBER,
    PRICE_SOURCE_FLOWPOWER,
)

_LOGGER = logging.getLogger(__name__)


class FlowPowerSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Flow Power Sync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._amber_sites: list[dict[str, Any]] = []
        self._fp_client: FlowPowerPortalClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select price source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_PRICE_SOURCE] = user_input[CONF_PRICE_SOURCE]

            if user_input[CONF_PRICE_SOURCE] == PRICE_SOURCE_AMBER:
                return await self.async_step_amber()
            elif user_input[CONF_PRICE_SOURCE] == PRICE_SOURCE_FLOWPOWER:
                return await self.async_step_flowpower_login()
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
                            selector.SelectOptionDict(value=PRICE_SOURCE_FLOWPOWER, label="Flow Power (Portal login)"),
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

    async def async_step_flowpower_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power portal email and password entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_FLOWPOWER_EMAIL]
            password = user_input[CONF_FLOWPOWER_PASSWORD]

            try:
                self._fp_client = FlowPowerPortalClient()
                result = await self._fp_client.authenticate(email, password)

                if result.get("status") == "mfa_required":
                    self._data[CONF_FLOWPOWER_EMAIL] = email
                    self._data[CONF_FLOWPOWER_PASSWORD] = password
                    # Store client so it can be passed to coordinator after MFA
                    self._fp_client = self._fp_client
                    return await self.async_step_flowpower_mfa()

            except ValueError as e:
                _LOGGER.error("Flow Power login error: %s", e)
                errors["base"] = "invalid_credentials"
                self._fp_client = None
            except Exception as e:
                _LOGGER.error("Flow Power connection error: %s", e)
                errors["base"] = "cannot_connect"
                self._fp_client = None

        return self.async_show_form(
            step_id="flowpower_login",
            data_schema=vol.Schema({
                vol.Required(CONF_FLOWPOWER_EMAIL): str,
                vol.Required(CONF_FLOWPOWER_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "portal_url": "https://flowpower.kwatch.com.au",
            },
        )

    async def async_step_flowpower_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power SMS MFA verification."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["mfa_code"]

            try:
                success = await self._fp_client.verify_mfa(code)

                if success:
                    # Stash authenticated client for coordinator to pick up
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN]["_pending_fp_client"] = self._fp_client
                    return await self.async_step_region()
                else:
                    errors["base"] = "invalid_mfa_code"
            except Exception as e:
                _LOGGER.error("Flow Power MFA error: %s", e)
                errors["base"] = "mfa_verification_failed"

        return self.async_show_form(
            step_id="flowpower_mfa",
            data_schema=vol.Schema({
                vol.Required("mfa_code"): str,
            }),
            errors=errors,
            description_placeholders={
                "email": self._data.get(CONF_FLOWPOWER_EMAIL, ""),
            },
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
                        step=0.01,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_PEA_ENABLED, default=True): bool,
                vol.Optional(CONF_PEA_CUSTOM_VALUE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-50,
                        max=50,
                        step=0.01,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FlowPowerSyncOptionsFlow:
        """Get the options flow for this handler."""
        return FlowPowerSyncOptionsFlow()


class FlowPowerSyncOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Flow Power Sync."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._fp_client: FlowPowerPortalClient | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Check if user wants to connect/re-authenticate Flow Power portal
            if user_input.pop("reauth_flowpower", False) or user_input.pop("connect_flowpower", False):
                return await self.async_step_flowpower_reauth()
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        # Build schema - add re-auth option for Flow Power portal users
        schema_fields: dict[Any, Any] = {
            vol.Required(
                CONF_BASE_RATE,
                default=current.get(CONF_BASE_RATE, DEFAULT_BASE_RATE),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=0.01,
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
                    step=0.01,
                    unit_of_measurement="c/kWh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }

        # Flow Power portal: show connect or re-authenticate option
        if current.get(CONF_FLOWPOWER_EMAIL):
            # Already connected — offer re-authentication
            schema_fields[
                vol.Optional("reauth_flowpower", default=False)
            ] = selector.BooleanSelector()
        else:
            # Not connected — offer to connect
            schema_fields[
                vol.Optional("connect_flowpower", default=False)
            ] = selector.BooleanSelector()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_flowpower_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power portal authentication/re-authentication."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            email = user_input.get(CONF_FLOWPOWER_EMAIL, current.get(CONF_FLOWPOWER_EMAIL, ""))
            password = user_input.get(CONF_FLOWPOWER_PASSWORD, current.get(CONF_FLOWPOWER_PASSWORD, ""))
            # Store credentials for the coordinator
            self._fp_email = email
            self._fp_password = password

            try:
                self._fp_client = FlowPowerPortalClient()
                result = await self._fp_client.authenticate(email, password)

                if result.get("status") == "mfa_required":
                    return await self.async_step_flowpower_mfa()

            except ValueError as e:
                _LOGGER.error("Flow Power re-auth error: %s", e)
                errors["base"] = "invalid_credentials"
                self._fp_client = None
            except Exception as e:
                _LOGGER.error("Flow Power re-auth connection error: %s", e)
                errors["base"] = "cannot_connect"
                self._fp_client = None

        return self.async_show_form(
            step_id="flowpower_reauth",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_FLOWPOWER_EMAIL,
                    default=current.get(CONF_FLOWPOWER_EMAIL, ""),
                ): str,
                vol.Required(CONF_FLOWPOWER_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "portal_url": "https://flowpower.kwatch.com.au",
            },
        )

    async def async_step_flowpower_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power SMS MFA verification during re-auth."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["mfa_code"]

            try:
                success = await self._fp_client.verify_mfa(code)

                if success:
                    # Stash authenticated client for coordinator to pick up
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN]["_pending_fp_client"] = self._fp_client
                    # Save credentials so coordinator can use them
                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_FLOWPOWER_EMAIL: getattr(self, "_fp_email", ""),
                            CONF_FLOWPOWER_PASSWORD: getattr(self, "_fp_password", ""),
                        },
                    )
                else:
                    errors["base"] = "invalid_mfa_code"
            except Exception as e:
                _LOGGER.error("Flow Power MFA re-auth error: %s", e)
                errors["base"] = "mfa_verification_failed"

        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="flowpower_mfa",
            data_schema=vol.Schema({
                vol.Required("mfa_code"): str,
            }),
            errors=errors,
            description_placeholders={
                "email": current.get(CONF_FLOWPOWER_EMAIL, ""),
            },
        )
