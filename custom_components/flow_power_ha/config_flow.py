"""Config flow for Flow Power HA integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .const import (
    CONF_BASE_RATE,
    CONF_FLOWPOWER_API_KEY,
    CONF_FLOWPOWER_NMI,
    CONF_HAPPY_HOUR_EXPORT_RATE,
    CONF_FP_NETWORK,
    CONF_FP_TARIFF_CODE,
    CONF_FP_TWAP_OVERRIDE,
    CONF_NEM_REGION,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DOMAIN,
    FLOWPOWER_KWATCH_REGIONS,
    NETWORK_API_NAME,
    NETWORK_TARIFF_URL,
    NEM_REGIONS,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_FLOWPOWER,
    REGION_NETWORKS,
)
from .flow_power_api import FlowPowerAPIClient, FlowPowerAPIError
from .tariff_utils import get_network_tariff_rate, get_tariff_codes_for_network

_LOGGER = logging.getLogger(__name__)


async def validate_flowpower_api_key(
    hass,
    api_key: str,
    region: str = "NSW1",
) -> dict[str, Any]:
    """Validate a Flow Power KWatch API key."""
    if not api_key:
        return {"success": False, "error": "invalid_api_key"}

    site_lookup_error: str | None = None
    client = FlowPowerAPIClient(api_key, async_get_clientsession(hass))
    try:
        sites = await client.get_residential_sites()
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        site_lookup_error = str(err)
        sites = []
    except aiohttp.ClientError:
        site_lookup_error = "cannot_connect"
        sites = []
    except Exception as err:
        _LOGGER.exception("Flow Power API site validation failed: %s", err)
        site_lookup_error = "cannot_connect"
        sites = []

    if sites:
        return {"success": True, "sites": sites}

    api_region = FLOWPOWER_KWATCH_REGIONS.get(region, str(region).lower())
    dispatch: list[dict[str, Any]] = []
    forecast_30: list[dict[str, Any]] = []
    forecast_5: list[dict[str, Any]] = []
    try:
        dispatch = await client.dispatch5mins(api_region, period=60)
        forecast_30 = await client.predispatch30mins(api_region, period=1)
        forecast_5 = await client.predispatch5mins(api_region, period=60)
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError:
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Flow Power API price validation failed: %s", err)
        return {"success": False, "error": "cannot_connect"}

    if dispatch:
        return {
            "success": True,
            "sites": [],
            "site_lookup_error": site_lookup_error or "no_sites",
            "has_forecast": bool(forecast_30 or forecast_5),
        }
    return {
        "success": False,
        "error": "cannot_connect" if site_lookup_error else "no_sites",
    }


def _flowpower_site_label(site: dict[str, Any]) -> str:
    """Return a display label for a Flow Power API site."""
    nmi = site.get("nmi", "")
    tariff = site.get("networkTariff")
    return f"{nmi} - {tariff}" if tariff else str(nmi)


class FlowPowerSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Flow Power Sync."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._region: str = "NSW1"
        self._flowpower_sites: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - select price source."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_PRICE_SOURCE] = user_input[CONF_PRICE_SOURCE]

            return await self.async_step_region()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_PRICE_SOURCE, default=PRICE_SOURCE_AEMO): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=PRICE_SOURCE_AEMO, label="AEMO (Direct wholesale)"),
                            selector.SelectOptionDict(value=PRICE_SOURCE_FLOWPOWER, label="Flow Power API (KWatch)"),
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_region(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle NEM region selection."""
        if user_input is not None:
            self._data[CONF_NEM_REGION] = user_input[CONF_NEM_REGION]
            self._region = user_input[CONF_NEM_REGION]
            if self._data.get(CONF_PRICE_SOURCE) == PRICE_SOURCE_FLOWPOWER and not self._data.get(CONF_FLOWPOWER_API_KEY):
                return await self.async_step_flowpower_api_key()
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

    async def async_step_flowpower_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power KWatch API key entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_FLOWPOWER_API_KEY, "").strip()
            result = await validate_flowpower_api_key(
                self.hass,
                api_key,
                self._data.get(CONF_NEM_REGION, "NSW1"),
            )
            if result["success"]:
                self._data[CONF_FLOWPOWER_API_KEY] = api_key
                self._flowpower_sites = result.get("sites", [])
                if len(self._flowpower_sites) == 1:
                    self._data[CONF_FLOWPOWER_NMI] = self._flowpower_sites[0]["nmi"]
                    return await self.async_step_tariff()
                if self._flowpower_sites:
                    return await self.async_step_flowpower_site()
                return await self.async_step_tariff()
            errors["base"] = result.get("error", "cannot_connect")

        return self.async_show_form(
            step_id="flowpower_api_key",
            data_schema=vol.Schema({
                vol.Required(CONF_FLOWPOWER_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_flowpower_site(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power residential site selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_nmi = user_input.get(CONF_FLOWPOWER_NMI)
            site = next(
                (
                    item for item in self._flowpower_sites
                    if item.get("nmi") == selected_nmi
                ),
                None,
            )
            if site:
                self._data[CONF_FLOWPOWER_NMI] = selected_nmi
                return await self.async_step_tariff()
            errors["base"] = "invalid_site"

        return self.async_show_form(
            step_id="flowpower_site",
            data_schema=vol.Schema({
                vol.Required(CONF_FLOWPOWER_NMI): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=site["nmi"],
                                label=_flowpower_site_label(site),
                            )
                            for site in self._flowpower_sites
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
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
            description_placeholders={
                "network": fp_network,
                "tariff_url": NETWORK_TARIFF_URL.get(fp_network, "your distributor's website"),
            },
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle pricing configuration."""
        if user_input is not None:
            if not user_input.get(CONF_FP_TWAP_OVERRIDE):
                user_input[CONF_FP_TWAP_OVERRIDE] = None
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
                vol.Optional(CONF_FP_TWAP_OVERRIDE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.01,
                        unit_of_measurement="c/kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_HAPPY_HOUR_EXPORT_RATE): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step=0.01,
                        unit_of_measurement="$/kWh",
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
        self._options_data: dict[str, Any] = {}
        self._flowpower_sites: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            if not user_input.get(CONF_FP_TWAP_OVERRIDE):
                user_input[CONF_FP_TWAP_OVERRIDE] = None
            api_key = user_input.get(CONF_FLOWPOWER_API_KEY, "")
            if isinstance(api_key, str):
                api_key = api_key.strip()

            current = {**self.config_entry.data, **self.config_entry.options}
            if api_key:
                result = await validate_flowpower_api_key(
                    self.hass,
                    api_key,
                    current.get(CONF_NEM_REGION, "NSW1"),
                )
                if not result["success"]:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=self._init_schema(current),
                        errors={"base": result.get("error", "cannot_connect")},
                    )
                user_input[CONF_FLOWPOWER_API_KEY] = api_key
                self._flowpower_sites = result.get("sites", [])
                if len(self._flowpower_sites) == 1:
                    user_input[CONF_FLOWPOWER_NMI] = self._flowpower_sites[0]["nmi"]
                elif self._flowpower_sites:
                    self._options_data = user_input
                    return await self.async_step_flowpower_site_options()
            elif current.get(CONF_FLOWPOWER_API_KEY):
                user_input[CONF_FLOWPOWER_API_KEY] = current[CONF_FLOWPOWER_API_KEY]
                if current.get(CONF_FLOWPOWER_NMI):
                    user_input[CONF_FLOWPOWER_NMI] = current[CONF_FLOWPOWER_NMI]

            self._options_data = user_input

            # If a network is selected, go to tariff code step
            fp_network = user_input.get(CONF_FP_NETWORK, "")
            if fp_network:
                return await self.async_step_options_tariff_code()

            # No network — save directly (clear any old tariff code)
            user_input.pop(CONF_FP_TARIFF_CODE, None)
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=self._init_schema(current),
        )

    def _init_schema(self, current: dict[str, Any]) -> vol.Schema:
        """Build the options form schema."""

        # Determine network options for the configured region
        region = current.get(CONF_NEM_REGION, "NSW1")
        networks = REGION_NETWORKS.get(region, [])
        network_options = [
            selector.SelectOptionDict(value="", label="None (flat rate)"),
        ] + [
            selector.SelectOptionDict(value=n, label=n)
            for n in networks
        ]

        # The Flow Power Web Data API is the only supported account connection.
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
                CONF_FP_TWAP_OVERRIDE,
                description={"suggested_value": current.get(CONF_FP_TWAP_OVERRIDE)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=0.01,
                    unit_of_measurement="c/kWh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_HAPPY_HOUR_EXPORT_RATE,
                description={"suggested_value": current.get(CONF_HAPPY_HOUR_EXPORT_RATE)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1,
                    step=0.01,
                    unit_of_measurement="$/kWh",
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
            vol.Optional(CONF_FLOWPOWER_API_KEY): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD
                )
            ),
        }

        return vol.Schema(schema_fields)

    async def async_step_flowpower_site_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Flow Power residential site selection in options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_nmi = user_input.get(CONF_FLOWPOWER_NMI)
            site = next(
                (
                    item for item in self._flowpower_sites
                    if item.get("nmi") == selected_nmi
                ),
                None,
            )
            if site:
                self._options_data[CONF_FLOWPOWER_NMI] = selected_nmi
                fp_network = self._options_data.get(CONF_FP_NETWORK, "")
                if fp_network:
                    return await self.async_step_options_tariff_code()
                return self.async_create_entry(title="", data=self._options_data)
            errors["base"] = "invalid_site"

        return self.async_show_form(
            step_id="flowpower_site_options",
            data_schema=vol.Schema({
                vol.Required(CONF_FLOWPOWER_NMI): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=site["nmi"],
                                label=_flowpower_site_label(site),
                            )
                            for site in self._flowpower_sites
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_options_tariff_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle tariff code selection in options flow."""
        errors: dict[str, str] = {}
        fp_network = self._options_data.get(CONF_FP_NETWORK, "")

        if user_input is not None:
            fp_tariff_code = user_input.get(CONF_FP_TARIFF_CODE, "")
            api_name = NETWORK_API_NAME.get(fp_network)
            if api_name and fp_tariff_code:
                now = datetime.now(timezone.utc)
                rate = await self.hass.async_add_executor_job(
                    get_network_tariff_rate, now, api_name, fp_tariff_code
                )
                if rate is not None:
                    self._options_data[CONF_FP_TARIFF_CODE] = fp_tariff_code
                    return self.async_create_entry(title="", data=self._options_data)
            errors["base"] = "invalid_tariff"

        # Load available tariff codes for the selected network
        tariff_codes = await self.hass.async_add_executor_job(
            get_tariff_codes_for_network, fp_network
        )

        current = {**self.config_entry.data, **self.config_entry.options}

        if tariff_codes:
            code_options = [
                selector.SelectOptionDict(value=code, label=code)
                for code in sorted(tariff_codes)
            ]
            schema = vol.Schema({
                vol.Required(
                    CONF_FP_TARIFF_CODE,
                    default=current.get(CONF_FP_TARIFF_CODE, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=code_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
        else:
            schema = vol.Schema({
                vol.Required(
                    CONF_FP_TARIFF_CODE,
                    default=current.get(CONF_FP_TARIFF_CODE, ""),
                ): str,
            })

        return self.async_show_form(
            step_id="options_tariff_code",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "network": fp_network,
                "tariff_url": NETWORK_TARIFF_URL.get(fp_network, "your distributor's website"),
            },
        )
