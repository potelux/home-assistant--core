"""Remote Home Assistant host management for Hass.io."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any
import uuid

import aiohttp
from cryptography.fernet import Fernet, InvalidToken

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REMOTE_HOST_STORAGE_KEY = f"{DOMAIN}_remote_hosts"
REMOTE_HOST_STORAGE_VERSION = 1
STORE_DELAY_SAVE = 30
CONNECTION_TIMEOUT = 10

REMOTE_HOST_STATUS_CONNECTED = "connected"
REMOTE_HOST_STATUS_UNREACHABLE = "unreachable"
REMOTE_HOST_STATUS_UNAUTHORIZED = "unauthorized"
REMOTE_HOST_STATUS_UNKNOWN = "unknown"


class RemoteHostError(Exception):
    """Error related to remote host management."""


class RemoteHostAuthError(RemoteHostError):
    """Authentication error for remote host."""


class RemoteHostConnectionError(RemoteHostError):
    """Connection error for remote host."""


class RemoteHostNotFoundError(RemoteHostError):
    """Remote host not found."""


@dataclass
class RemoteHost:
    """Represent a connected remote Home Assistant host."""

    id: str
    name: str
    url: str
    encrypted_token: str
    added_at: datetime
    last_seen: datetime | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization (token excluded from public output)."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "added_at": self.added_at.isoformat(),
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "status": self.status,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        """Convert to dict for persistent storage (includes encrypted token)."""
        return {
            **self.to_dict(),
            "encrypted_token": self.encrypted_token,
        }

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> RemoteHost:
        """Create from a storage dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            url=data["url"],
            encrypted_token=data["encrypted_token"],
            added_at=datetime.fromisoformat(data["added_at"]),
            last_seen=(
                datetime.fromisoformat(data["last_seen"])
                if data.get("last_seen")
                else None
            ),
            status=data.get("status", REMOTE_HOST_STATUS_UNKNOWN),
        )


class RemoteHostStore:
    """Handle persistent storage for remote hosts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize storage."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            REMOTE_HOST_STORAGE_VERSION,
            REMOTE_HOST_STORAGE_KEY,
        )
        self._hosts: dict[str, RemoteHost] = {}
        self._fernet: Fernet | None = None
        self._encryption_key: bytes = b""

    async def async_load(self) -> None:
        """Load stored data and initialize encryption."""
        data = await self._store.async_load()

        if data is None:
            self._encryption_key = Fernet.generate_key()
            self._fernet = Fernet(self._encryption_key)
            self._async_schedule_save()
            return

        raw_key = data.get("encryption_key", "")
        key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
        try:
            self._fernet = Fernet(key_bytes)
            self._encryption_key = key_bytes
        except ValueError:
            _LOGGER.warning(
                "Invalid encryption key in storage, generating new one — "
                "existing remote hosts will need to be re-added"
            )
            self._encryption_key = Fernet.generate_key()
            self._fernet = Fernet(self._encryption_key)
            self._async_schedule_save()
            return

        for host_data in data.get("hosts", []):
            try:
                host = RemoteHost.from_storage_dict(host_data)
                self._hosts[host.id] = host
            except (KeyError, ValueError) as err:
                _LOGGER.warning("Skipping malformed remote host entry: %s", err)

    @callback
    def _async_schedule_save(self) -> None:
        """Schedule a delayed save."""
        self._store.async_delay_save(self._data_to_save, STORE_DELAY_SAVE)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Return data to save."""
        return {
            "encryption_key": self._encryption_key.decode(),
            "hosts": [host.to_storage_dict() for host in self._hosts.values()],
        }

    def get_all(self) -> list[RemoteHost]:
        """Return all hosts."""
        return list(self._hosts.values())

    def get(self, host_id: str) -> RemoteHost | None:
        """Return a specific host by ID."""
        return self._hosts.get(host_id)

    def add(self, host: RemoteHost) -> None:
        """Add or replace a host and persist."""
        self._hosts[host.id] = host
        self._async_schedule_save()

    def remove(self, host_id: str) -> bool:
        """Remove a host and persist. Returns True if the host existed."""
        if host_id not in self._hosts:
            return False
        del self._hosts[host_id]
        self._async_schedule_save()
        return True

    def encrypt_token(self, token: str) -> str:
        """Encrypt an access token."""
        if not self._fernet:
            raise RemoteHostError("Encryption not initialized")
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt an access token."""
        if not self._fernet:
            raise RemoteHostError("Encryption not initialized")
        try:
            return self._fernet.decrypt(encrypted_token.encode()).decode()
        except InvalidToken as err:
            raise RemoteHostError("Failed to decrypt access token") from err


class RemoteHostManager:
    """Manage connections to remote Home Assistant instances."""

    def __init__(self, hass: HomeAssistant, websession: aiohttp.ClientSession) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._websession = websession
        self._store = RemoteHostStore(hass)

    async def async_setup(self) -> None:
        """Load persisted remote hosts."""
        await self._store.async_load()

    async def async_discover(self) -> list[dict[str, Any]]:
        """Discover Home Assistant instances on the local network via mDNS.

        Returns a list of dicts with keys: hostname, ip, port, url.
        """
        discovered: list[dict[str, Any]] = []

        try:
            from zeroconf import ServiceStateChange  # noqa: PLC0415
            from zeroconf.asyncio import (  # noqa: PLC0415
                AsyncServiceBrowser,
                AsyncServiceInfo,
            )

            from homeassistant.components.zeroconf import (  # noqa: PLC0415
                async_get_async_instance,
            )

            aiozc = await async_get_async_instance(self._hass)
            zc = aiozc.zeroconf
            service_type = "_home-assistant._tcp.local."
            found: list[str] = []

            def _on_change(
                zeroconf_instance: Any,
                service_type_str: str,
                name: str,
                state_change: ServiceStateChange,
            ) -> None:
                if state_change == ServiceStateChange.Added:
                    found.append(name)

            browser = AsyncServiceBrowser(zc, [service_type], handlers=[_on_change])
            await asyncio.sleep(3)
            await browser.async_cancel()

            for name in found:
                info = AsyncServiceInfo(service_type, name)
                if await info.async_request(zc, 3000):
                    addresses = info.parsed_addresses()
                    if addresses:
                        ip = addresses[0]
                        port = info.port or 8123
                        hostname = name.removesuffix(f".{service_type}").removesuffix(
                            "."
                        )
                        discovered.append(
                            {
                                "hostname": hostname,
                                "ip": ip,
                                "port": port,
                                "url": f"http://{ip}:{port}",
                            }
                        )

        except ImportError:
            _LOGGER.debug("zeroconf not available for mDNS discovery")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mDNS discovery error: %s", err)

        return discovered

    async def async_connect(self, url: str, username: str, password: str) -> RemoteHost:
        """Authenticate with a remote HA instance and store the connection.

        Performs HA's login flow to obtain an access token, then verifies the
        connection and persists the encrypted token.
        """
        url = url.rstrip("/")

        # Step 1: Initiate login flow
        try:
            async with self._websession.post(
                f"{url}/auth/login_flow",
                json={
                    "client_id": url,
                    "handler": ["homeassistant", None],
                    "redirect_uri": url,
                },
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    raise RemoteHostAuthError(
                        f"Could not start login flow on {url}: HTTP {resp.status}"
                    )
                flow_data = await resp.json()
                flow_id = flow_data.get("flow_id")
                if not flow_id:
                    raise RemoteHostAuthError("No flow_id in login flow response")
        except aiohttp.ClientError as err:
            raise RemoteHostConnectionError(f"Cannot reach {url}: {err}") from err

        # Step 2: Submit credentials
        try:
            async with self._websession.post(
                f"{url}/auth/login_flow/{flow_id}",
                json={"username": username, "password": password},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    raise RemoteHostAuthError("Invalid credentials")
                auth_data = await resp.json()
                if auth_data.get("type") != "create_entry":
                    raise RemoteHostAuthError(
                        "Authentication did not complete successfully"
                    )
                # The result may be a short-lived token or a code depending on HA version
                result = auth_data.get("result", {})
                access_token: str = (
                    result.get("access_token", "")
                    if isinstance(result, dict)
                    else str(result)
                )
                if not access_token:
                    raise RemoteHostAuthError(
                        "No access_token in authentication result"
                    )
        except aiohttp.ClientError as err:
            raise RemoteHostConnectionError(f"Cannot reach {url}: {err}") from err

        # Step 3: Verify the token works and fetch the instance name
        try:
            async with self._websession.get(
                f"{url}/api/",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise RemoteHostAuthError("Access token rejected by remote")
                if resp.status != 200:
                    raise RemoteHostConnectionError(
                        f"Token verification returned HTTP {resp.status}"
                    )
                api_info = await resp.json()
                name: str = api_info.get("location_name") or url
        except aiohttp.ClientError as err:
            raise RemoteHostConnectionError(
                f"Cannot verify connection to {url}: {err}"
            ) from err

        encrypted_token = self._store.encrypt_token(access_token)
        now = utcnow()
        host = RemoteHost(
            id=str(uuid.uuid4()),
            name=name,
            url=url,
            encrypted_token=encrypted_token,
            added_at=now,
            last_seen=now,
            status=REMOTE_HOST_STATUS_CONNECTED,
        )
        self._store.add(host)
        _LOGGER.info("Connected to remote host '%s' at %s", name, url)
        return host

    def get_hosts(self) -> list[RemoteHost]:
        """Return all connected remote hosts."""
        return self._store.get_all()

    def get_host(self, host_id: str) -> RemoteHost | None:
        """Return a specific remote host by ID, or None."""
        return self._store.get(host_id)

    async def async_remove_host(self, host_id: str) -> bool:
        """Remove a remote host, revoking the token on a best-effort basis."""
        host = self._store.get(host_id)
        if host is None:
            return False

        # Attempt to revoke the token on the remote (best-effort)
        try:
            token = self._store.decrypt_token(host.encrypted_token)
            async with self._websession.delete(
                f"{host.url}/auth/token",
                json={"token": token},
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status not in (200, 404):
                    _LOGGER.debug(
                        "Token revocation for '%s' returned HTTP %s (ignored)",
                        host.name,
                        resp.status,
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not revoke token for '%s': %s (ignored)", host.name, err
            )

        removed = self._store.remove(host_id)
        if removed:
            _LOGGER.info("Removed remote host '%s'", host.name)
        return removed

    async def async_ping_host(self, host_id: str) -> RemoteHost:
        """Test connectivity to a remote host and update its status and last_seen."""
        host = self._store.get(host_id)
        if host is None:
            raise RemoteHostNotFoundError(f"Remote host {host_id!r} not found")

        new_status = REMOTE_HOST_STATUS_UNREACHABLE
        new_last_seen = host.last_seen

        try:
            token = self._store.decrypt_token(host.encrypted_token)
            async with self._websession.get(
                f"{host.url}/api/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    new_status = REMOTE_HOST_STATUS_CONNECTED
                    new_last_seen = utcnow()
                elif resp.status == 401:
                    new_status = REMOTE_HOST_STATUS_UNAUTHORIZED
                else:
                    new_status = REMOTE_HOST_STATUS_UNREACHABLE
        except TimeoutError, aiohttp.ClientError:
            new_status = REMOTE_HOST_STATUS_UNREACHABLE

        updated = RemoteHost(
            id=host.id,
            name=host.name,
            url=host.url,
            encrypted_token=host.encrypted_token,
            added_at=host.added_at,
            last_seen=new_last_seen,
            status=new_status,
        )
        self._store.add(updated)
        return updated

    async def async_get_remote_addons(self, host_id: str) -> list[dict[str, Any]]:
        """Query a remote host for its installed addon list (no caching)."""
        host = self._store.get(host_id)
        if host is None:
            raise RemoteHostNotFoundError(f"Remote host {host_id!r} not found")

        try:
            token = self._store.decrypt_token(host.encrypted_token)
            async with self._websession.get(
                f"{host.url}/api/hassio/addons",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise RemoteHostAuthError(
                        f"Token for '{host.name}' is invalid or expired"
                    )
                if resp.status != 200:
                    raise RemoteHostConnectionError(
                        f"Remote '{host.name}' returned HTTP {resp.status}"
                    )
                data = await resp.json()
                return data.get("data", {}).get("addons", [])
        except (TimeoutError, aiohttp.ClientError) as err:
            raise RemoteHostConnectionError(
                f"Remote '{host.name}' is unreachable: {err}"
            ) from err

    async def async_get_remote_addon_info(
        self, host_id: str, addon_slug: str
    ) -> dict[str, Any]:
        """Fetch detailed info for a specific addon on a remote host."""
        host = self._store.get(host_id)
        if host is None:
            raise RemoteHostNotFoundError(f"Remote host {host_id!r} not found")

        try:
            token = self._store.decrypt_token(host.encrypted_token)
            async with self._websession.get(
                f"{host.url}/api/hassio/addons/{addon_slug}/info",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise RemoteHostAuthError(
                        f"Token for '{host.name}' is invalid or expired"
                    )
                if resp.status == 404:
                    raise RemoteHostNotFoundError(
                        f"Addon '{addon_slug}' not found on '{host.name}'"
                    )
                if resp.status != 200:
                    raise RemoteHostConnectionError(
                        f"Remote '{host.name}' returned HTTP {resp.status}"
                    )
                data = await resp.json()
                return data.get("data", {})
        except (TimeoutError, aiohttp.ClientError) as err:
            raise RemoteHostConnectionError(
                f"Remote '{host.name}' is unreachable: {err}"
            ) from err

    async def async_get_remote_addon_logs(self, host_id: str, addon_slug: str) -> str:
        """Fetch logs for a specific addon on a remote host."""
        host = self._store.get(host_id)
        if host is None:
            raise RemoteHostNotFoundError(f"Remote host {host_id!r} not found")

        try:
            token = self._store.decrypt_token(host.encrypted_token)
            async with self._websession.get(
                f"{host.url}/api/hassio/addons/{addon_slug}/logs",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise RemoteHostAuthError(
                        f"Token for '{host.name}' is invalid or expired"
                    )
                if resp.status == 404:
                    raise RemoteHostNotFoundError(
                        f"Addon '{addon_slug}' not found on '{host.name}'"
                    )
                if resp.status != 200:
                    raise RemoteHostConnectionError(
                        f"Remote '{host.name}' returned HTTP {resp.status}"
                    )
                return await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise RemoteHostConnectionError(
                f"Remote '{host.name}' is unreachable: {err}"
            ) from err
