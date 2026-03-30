"""Config flow for Flow Power HA integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api_clients import FlowPowerPortalClient
from .const import (
    CONF_BASE_RATE,
    CONF_FLOWPOWER_EMAIL,
    CONF_FLOWPOWER_PASSWORD,
    CONF_FP_NETWORK,
    CONF_FP_TARIFF_CODE,
    CONF_NEM_REGION,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DOMAIN,
    NETWORK_API_NAME,
    NEM_REGIONS,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_FLOWPOWER,
    REGION_NETWORKS,
)
from .tariff_utils import get_network_tariff_rate, get_tariff_codes_for_network

_LOGGER = logging.getLogger(__name__)


class FlowPowerSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Flow Power Sync."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._region: str = "NSW1"
        self._fp_client: FlowPowerPortalClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select price source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_PRICE_SOURCE] = user_input[CONF_PRICE_SOURCE]

            if user_input[CONF_PRICE_SOURCE] == PRICE_SOURCE_FLOWPOWER:
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
                            selector.SelectOptionDict(value=PRICE_SOURCE_FLOWPOWER, label="Flow Power (Portal login)"),
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            errors=errors,
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
            self._region = user_input[CONF_NEM_REGION]
            return await self.async_step_tariff()

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

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle network (DNSP) selection."""
        if user_input is not None:
            fp_network = user_input.get(CONF_FP_NETWORK, "")

            if not fp_network or fp_network == "skip":
                return await self.async_step_pricing()

            self._data[CONF_FP_NETWORK] = fp_network
            return await self.async_step_tariff_code()

        networks = REGION_NETWORKS.get(self._region, [])
        network_options = [
            selector.SelectOptionDict(value="skip", label="Skip (flat rate)"),
        ] + [
            selector.SelectOptionDict(value=n, label=n)
            for n in networks
        ]

        return self.async_show_form(
            step_id="tariff",
            data_schema=vol.Schema({
                vol.Required(CONF_FP_NETWORK, default="skip"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=network_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_tariff_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle tariff code selection with dropdown of available codes."""
        errors: dict[str, str] = {}
        fp_network = self._data.get(CONF_FP_NETWORK, "")

        if user_input is not None:
            fp_tariff_code = user_input.get(CONF_FP_TARIFF_CODE, "")
            api_name = NETWORK_API_NAME.get(fp_network)
            if api_name and fp_tariff_code:
                now = datetime.now(timezone.utc)
                rate = await self.hass.async_add_executor_job(
                    get_network_tariff_rate, now, api_name, fp_tariff_code
                )
                if rate is not None:
                    self._data[CONF_FP_TARIFF_CODE] = fp_tariff_code
                    return await self.async_step_pricing()
            errors["base"] = "invalid_tariff"

        # Load available tariff codes for the selected network
        tariff_codes = await self.hass.async_add_executor_job(
            get_tariff_codes_for_network, fp_network
        )

        if tariff_codes:
            code_options = [
                selector.SelectOptionDict(value=code, label=code)
                for code in sorted(tariff_codes)
            ]
            schema = vol.Schema({
                vol.Required(CONF_FP_TARIFF_CODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=code_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
        else:
            # Fallback to text input if codes can't be loaded
            schema = vol.Schema({
                vol.Required(CONF_FP_TARIFF_CODE): str,
            })

        return self.async_show_form(
            step_id="tariff_code",
            data_schema=schema,
            errors=errors,
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

        # Determine network options for the configured region
        region = current.get(CONF_NEM_REGION, "NSW1")
        networks = REGION_NETWORKS.get(region, [])
        network_options = [
            selector.SelectOptionDict(value="", label="None (flat rate)"),
        ] + [
            selector.SelectOptionDict(value=n, label=n)
            for n in networks
        ]

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
            vol.Optional(
                CONF_FP_NETWORK,
                description={"suggested_value": current.get(CONF_FP_NETWORK, "")},
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=network_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_FP_TARIFF_CODE,
                description={"suggested_value": current.get(CONF_FP_TARIFF_CODE, "")},
            ): str,
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
        # Check for auto-reauth client (credentials already submitted, just needs MFA)
        pending_client = self.hass.data.get(DOMAIN, {}).pop("_pending_mfa_client", None)
        if pending_client is not None:
            self._fp_client = pending_client
            self._fp_email = self.hass.data.get(DOMAIN, {}).pop("_pending_mfa_email", "")
            self._fp_password = self.hass.data.get(DOMAIN, {}).pop("_pending_mfa_password", "")
            return await self.async_step_flowpower_mfa()

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
                    # Merge credentials into existing options (don't wipe them)
                    merged = {
                        **self.config_entry.options,
                        CONF_FLOWPOWER_EMAIL: getattr(self, "_fp_email", ""),
                        CONF_FLOWPOWER_PASSWORD: getattr(self, "_fp_password", ""),
                    }
                    return self.async_create_entry(title="", data=merged)
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
