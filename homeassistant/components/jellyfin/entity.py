"""Base Entity for Jellyfin."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JellyfinDataUpdateCoordinator


class JellyfinEntity(CoordinatorEntity[JellyfinDataUpdateCoordinator]):
    """Defines a base Jellyfin entity."""

    _attr_has_entity_name = True


class JellyfinServerEntity(JellyfinEntity):
    """Defines a base Jellyfin server entity."""

    def __init__(self, coordinator: JellyfinDataUpdateCoordinator) -> None:
        """Initialize the Jellyfin entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.server_id)},
        )


class JellyfinClientEntity(JellyfinEntity):
    """Defines a base Jellyfin client entity.

    Keyed by device_id, which is stable across reconnections. Session data
    is only available when the device is actively connected; the entity
    persists showing state OFF when the device is offline.
    """

    def __init__(
        self,
        coordinator: JellyfinDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the Jellyfin entity."""
        super().__init__(coordinator)
        self.device_id: str = device_id

        device_info = coordinator.known_devices[device_id]
        self.device_name: str = device_info["DeviceName"]
        self.client_name: str = device_info["Client"]
        self.app_version: str = device_info["ApplicationVersion"]
        self.capabilities: dict[str, Any] = device_info.get("Capabilities", {})

        if self.capabilities.get("SupportsPersistentIdentifier", False):
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self.device_id)},
                manufacturer="Jellyfin",
                model=self.client_name,
                name=self.device_name,
                sw_version=self.app_version,
                via_device=(DOMAIN, coordinator.server_id),
            )
            self._attr_name = None
        else:
            self._attr_device_info = None
            self._attr_has_entity_name = False
            self._attr_name = self.device_name

    @property
    def session_data(self) -> dict[str, Any] | None:
        """Return active session data, or None if the device is offline."""
        return self.coordinator.data.get(self.device_id)

    @property
    def session_id(self) -> str | None:
        """Return the active session ID, or None if the device is offline."""
        session = self.session_data
        return session["Id"] if session else None

    @property
    def available(self) -> bool:
        """Return True when the Jellyfin server is reachable.

        Offline devices show state OFF rather than becoming unavailable,
        so the entity persists in the UI regardless of device connectivity.
        """
        return super().available
