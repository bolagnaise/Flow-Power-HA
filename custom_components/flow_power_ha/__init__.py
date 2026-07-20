"""Flow Power HA integration for Home Assistant.

This integration provides electricity pricing sensors for Flow Power customers,
with support for both Amber Electric and AEMO (direct wholesale) price sources.

Features:
- PEA (Price Efficiency Adjustment) calculation
- Happy Hour export pricing
- Price forecasts for EMHASS and HAEO integrations
- Configurable network tariffs and fees
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

try:
    from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue, async_delete_issue
except ImportError:
    from homeassistant.components.repairs import IssueSeverity, async_create_issue, async_delete_issue

from .const import (
    CONF_FLOWPOWER_API_KEY,
    CONF_PRICE_SOURCE,
    DOMAIN,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_FLOWPOWER,
)
from .coordinator import FlowPowerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flow Power HA from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge data and options for current config
    config = {**entry.data, **entry.options}

    # Create and initialize coordinator
    coordinator = FlowPowerCoordinator(hass, config)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    # Reload the integration when options change
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Shutdown coordinator
        coordinator: FlowPowerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        new_data = {**config_entry.data}

        if new_data.get("price_source") == "amber":
            _LOGGER.info(
                "Migrating Amber config entry to AEMO — removing Amber credentials"
            )
            new_data["price_source"] = "aemo"
            new_data.pop("amber_api_key", None)
            new_data.pop("amber_site_id", None)

        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)
        _LOGGER.info("Migrated config entry from version 1 to version 2")

    if config_entry.version == 2:
        new_data = {**config_entry.data}
        new_options = {**config_entry.options}
        legacy_portal = any(
            key in values
            for values in (new_data, new_options)
            for key in ("flowpower_email", "flowpower_password")
        )

        for values in (new_data, new_options):
            values.pop("flowpower_email", None)
            values.pop("flowpower_password", None)
            values.pop("connect_flowpower", None)
            values.pop("reauth_flowpower", None)

        api_key = new_options.get(
            CONF_FLOWPOWER_API_KEY,
            new_data.get(CONF_FLOWPOWER_API_KEY),
        )
        if not api_key and new_options.get(
            CONF_PRICE_SOURCE,
            new_data.get(CONF_PRICE_SOURCE),
        ) == PRICE_SOURCE_FLOWPOWER:
            if CONF_PRICE_SOURCE in new_options:
                new_options[CONF_PRICE_SOURCE] = PRICE_SOURCE_AEMO
            else:
                new_data[CONF_PRICE_SOURCE] = PRICE_SOURCE_AEMO

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=3,
        )
        await Store(hass, 1, f"{DOMAIN}.fp_session").async_remove()

        # Do not carry portal-derived values into the official API cache. The
        # next coordinator refresh repopulates it from Web Data Access.
        await Store(hass, 1, f"{DOMAIN}.fp_portal_data").async_remove()

        if legacy_portal and not api_key:
            await Store(hass, 1, f"{DOMAIN}.fp_account_data").async_remove()
            async_create_issue(
                hass,
                DOMAIN,
                "web_data_api_required",
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key="web_data_api_required",
            )
        else:
            async_delete_issue(hass, DOMAIN, "web_data_api_required")

        _LOGGER.info(
            "Migrated config entry to version 3: removed legacy Flow Power portal access"
        )

    _LOGGER.debug("Migration to version %s successful", config_entry.version)
    return True
