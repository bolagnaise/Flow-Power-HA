"""Flow Power Sync integration for Home Assistant.

This integration provides electricity pricing sensors for Flow Power customers,
with support for both Amber Electric and AEMO (direct wholesale) price sources.

Features:
- PEA (Price Efficiency Adjustment) calculation
- Happy Hour export pricing
- Price forecasts for EMHASS integration
- Configurable network tariffs and fees
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import FlowPowerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flow Power Sync from a config entry."""
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
        # Current version, no migration needed
        pass

    _LOGGER.debug("Migration to version %s successful", config_entry.version)
    return True
