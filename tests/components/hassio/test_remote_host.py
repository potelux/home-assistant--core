"""Tests for remote host management in the hassio integration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from cryptography.fernet import Fernet
import pytest

from homeassistant.components.hassio.remote_host import (
    REMOTE_HOST_STATUS_CONNECTED,
    REMOTE_HOST_STATUS_UNAUTHORIZED,
    REMOTE_HOST_STATUS_UNREACHABLE,
    RemoteHost,
    RemoteHostAuthError,
    RemoteHostConnectionError,
    RemoteHostManager,
    RemoteHostNotFoundError,
    RemoteHostStore,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from tests.test_util.aiohttp import AiohttpClientMocker

REMOTE_URL = "http://192.168.1.50:8123"
REMOTE_NAME = "Basement HA"


def _make_host(
    host_id: str = "test-host-id",
    name: str = REMOTE_NAME,
    url: str = REMOTE_URL,
    status: str = REMOTE_HOST_STATUS_CONNECTED,
    fernet: Fernet | None = None,
) -> RemoteHost:
    """Return a minimal RemoteHost for testing."""
    f = fernet or Fernet(Fernet.generate_key())
    encrypted_token = f.encrypt(b"test-token").decode()
    return RemoteHost(
        id=host_id,
        name=name,
        url=url,
        encrypted_token=encrypted_token,
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen=datetime(2026, 1, 2, tzinfo=UTC),
        status=status,
    )


class TestRemoteHost:
    """Unit tests for the RemoteHost dataclass."""

    def test_to_dict_excludes_token(self) -> None:
        """to_dict should not expose the encrypted token."""
        host = _make_host()
        result = host.to_dict()
        assert "encrypted_token" not in result
        assert result["id"] == "test-host-id"
        assert result["name"] == REMOTE_NAME
        assert result["url"] == REMOTE_URL
        assert result["status"] == REMOTE_HOST_STATUS_CONNECTED

    def test_to_storage_dict_includes_token(self) -> None:
        """to_storage_dict should include the encrypted token."""
        host = _make_host()
        result = host.to_storage_dict()
        assert "encrypted_token" in result

    def test_round_trip_storage_dict(self) -> None:
        """from_storage_dict(to_storage_dict(x)) should reconstruct the host."""
        original = _make_host()
        restored = RemoteHost.from_storage_dict(original.to_storage_dict())
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.url == original.url
        assert restored.encrypted_token == original.encrypted_token
        assert restored.status == original.status


class TestRemoteHostStore:
    """Unit tests for RemoteHostStore."""

    async def test_encrypt_decrypt_round_trip(self, hass: HomeAssistant) -> None:
        """Encrypting and then decrypting a token should return the original."""
        store = RemoteHostStore(hass)
        with patch.object(store._store, "async_load", return_value=None):
            await store.async_load()

        token = "super-secret-token"
        encrypted = store.encrypt_token(token)
        assert encrypted != token
        assert store.decrypt_token(encrypted) == token

    async def test_add_and_get(self, hass: HomeAssistant) -> None:
        """Adding a host should make it retrievable."""
        store = RemoteHostStore(hass)
        with patch.object(store._store, "async_load", return_value=None):
            await store.async_load()

        host = _make_host()
        with patch.object(store._store, "async_delay_save"):
            store.add(host)

        assert store.get(host.id) is host
        assert host in store.get_all()

    async def test_remove_existing(self, hass: HomeAssistant) -> None:
        """Removing an existing host should return True."""
        store = RemoteHostStore(hass)
        with patch.object(store._store, "async_load", return_value=None):
            await store.async_load()

        host = _make_host()
        with patch.object(store._store, "async_delay_save"):
            store.add(host)
            removed = store.remove(host.id)

        assert removed is True
        assert store.get(host.id) is None

    async def test_remove_nonexistent(self, hass: HomeAssistant) -> None:
        """Removing a host that doesn't exist should return False."""
        store = RemoteHostStore(hass)
        with patch.object(store._store, "async_load", return_value=None):
            await store.async_load()

        with patch.object(store._store, "async_delay_save"):
            assert store.remove("missing-id") is False

    async def test_load_persisted_data(self, hass: HomeAssistant) -> None:
        """Hosts saved previously should be loaded on async_load."""
        key = Fernet.generate_key()
        f = Fernet(key)
        encrypted_token = f.encrypt(b"persisted-token").decode()
        stored_data = {
            "encryption_key": key.decode(),
            "hosts": [
                {
                    "id": "persisted-id",
                    "name": "Persisted Host",
                    "url": "http://10.0.0.1:8123",
                    "encrypted_token": encrypted_token,
                    "added_at": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-02T00:00:00+00:00",
                    "status": REMOTE_HOST_STATUS_CONNECTED,
                }
            ],
        }

        store = RemoteHostStore(hass)
        with patch.object(store._store, "async_load", return_value=stored_data):
            await store.async_load()

        host = store.get("persisted-id")
        assert host is not None
        assert host.name == "Persisted Host"
        assert store.decrypt_token(host.encrypted_token) == "persisted-token"


class TestRemoteHostManager:
    """Unit tests for RemoteHostManager."""

    async def test_get_hosts_empty(self, hass: HomeAssistant) -> None:
        """get_hosts should return an empty list when no hosts are connected."""
        session = MagicMock(spec=aiohttp.ClientSession)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        assert manager.get_hosts() == []

    async def test_get_host_not_found(self, hass: HomeAssistant) -> None:
        """get_host should return None for an unknown ID."""
        session = MagicMock(spec=aiohttp.ClientSession)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        assert manager.get_host("nonexistent") is None

    async def test_connect_success(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_connect should create and store a RemoteHost on success."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        # Mock the remote HA authentication responses
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow",
            json={"flow_id": "abc123", "type": "form"},
        )
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow/abc123",
            json={
                "type": "create_entry",
                "result": {"access_token": "short-lived-token"},
            },
        )
        aioclient_mock.get(
            f"{REMOTE_URL}/api/",
            json={"location_name": REMOTE_NAME},
        )

        with patch.object(manager._store._store, "async_delay_save"):
            host = await manager.async_connect(REMOTE_URL, "admin", "password")

        assert host.name == REMOTE_NAME
        assert host.url == REMOTE_URL
        assert host.status == REMOTE_HOST_STATUS_CONNECTED
        assert manager.get_host(host.id) is not None

    async def test_connect_bad_credentials(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_connect should raise RemoteHostAuthError on 401/bad response."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow",
            json={"flow_id": "abc123", "type": "form"},
        )
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow/abc123",
            status=401,
        )

        with pytest.raises(RemoteHostAuthError):
            await manager.async_connect(REMOTE_URL, "admin", "wrong-password")

    async def test_connect_real_ha_auth_flow(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_connect should exchange the auth code for a token (real HA flow)."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        # Real HA returns an authorization code string, not access_token directly
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow",
            json={"flow_id": "abc123", "type": "form"},
        )
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow/abc123",
            json={"type": "create_entry", "result": "auth-code-xyz"},
        )
        aioclient_mock.post(
            f"{REMOTE_URL}/auth/token",
            json={"access_token": "real-access-token", "token_type": "Bearer"},
        )
        aioclient_mock.get(
            f"{REMOTE_URL}/api/",
            json={"location_name": REMOTE_NAME},
        )

        with patch.object(manager._store._store, "async_delay_save"):
            host = await manager.async_connect(REMOTE_URL, "admin", "password")

        assert host.name == REMOTE_NAME
        assert host.status == REMOTE_HOST_STATUS_CONNECTED

    async def test_connect_unreachable(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_connect should raise RemoteHostConnectionError when network fails."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        aioclient_mock.post(
            f"{REMOTE_URL}/auth/login_flow",
            exc=aiohttp.ClientConnectionError("Connection refused"),
        )

        with pytest.raises(RemoteHostConnectionError):
            await manager.async_connect(REMOTE_URL, "admin", "password")

    async def test_ping_connected(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_ping_host should mark a reachable host as connected."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(f"{REMOTE_URL}/api/", json={"location_name": REMOTE_NAME})

        with patch.object(manager._store._store, "async_delay_save"):
            updated = await manager.async_ping_host(host.id)

        assert updated.status == REMOTE_HOST_STATUS_CONNECTED
        assert updated.last_seen is not None

    async def test_ping_unreachable(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_ping_host should mark a timed-out host as unreachable."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(
            f"{REMOTE_URL}/api/",
            exc=aiohttp.ServerTimeoutError(),
        )

        with patch.object(manager._store._store, "async_delay_save"):
            updated = await manager.async_ping_host(host.id)

        assert updated.status == REMOTE_HOST_STATUS_UNREACHABLE

    async def test_ping_unauthorized(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_ping_host should mark a 401 response as unauthorized."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(f"{REMOTE_URL}/api/", status=401)

        with patch.object(manager._store._store, "async_delay_save"):
            updated = await manager.async_ping_host(host.id)

        assert updated.status == REMOTE_HOST_STATUS_UNAUTHORIZED

    async def test_ping_not_found(self, hass: HomeAssistant) -> None:
        """async_ping_host should raise RemoteHostNotFoundError for unknown ID."""
        session = MagicMock(spec=aiohttp.ClientSession)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        with pytest.raises(RemoteHostNotFoundError):
            await manager.async_ping_host("missing-id")

    async def test_remove_host(self, hass: HomeAssistant) -> None:
        """async_remove_host should remove the host and return True."""
        session = MagicMock(spec=aiohttp.ClientSession)
        # Make the DELETE call a no-op
        session.delete = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock(status=200)),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)
            result = await manager.async_remove_host(host.id)

        assert result is True
        assert manager.get_host(host.id) is None

    async def test_remove_nonexistent_host(self, hass: HomeAssistant) -> None:
        """async_remove_host should return False for an unknown ID."""
        session = MagicMock(spec=aiohttp.ClientSession)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        assert await manager.async_remove_host("missing-id") is False

    async def test_get_remote_addons(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_get_remote_addons should return the addon list from the remote."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(
            f"{REMOTE_URL}/api/hassio/addons",
            json={"data": {"addons": [{"slug": "test", "name": "Test Addon"}]}},
        )

        addons = await manager.async_get_remote_addons(host.id)

        assert len(addons) == 1
        assert addons[0]["slug"] == "test"

    async def test_get_remote_addons_unauthorized(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_get_remote_addons should raise RemoteHostAuthError on 401."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(f"{REMOTE_URL}/api/hassio/addons", status=401)

        with pytest.raises(RemoteHostAuthError):
            await manager.async_get_remote_addons(host.id)

    async def test_get_remote_addons_host_not_found(self, hass: HomeAssistant) -> None:
        """async_get_remote_addons should raise RemoteHostNotFoundError for unknown ID."""
        session = MagicMock(spec=aiohttp.ClientSession)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        with pytest.raises(RemoteHostNotFoundError):
            await manager.async_get_remote_addons("missing-id")

    async def test_get_remote_addon_info(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_get_remote_addon_info should return addon details from the remote."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(
            f"{REMOTE_URL}/api/hassio/addons/test/info",
            json={"data": {"slug": "test", "name": "Test Addon", "version": "1.0.0"}},
        )

        info = await manager.async_get_remote_addon_info(host.id, "test")

        assert info["slug"] == "test"
        assert info["version"] == "1.0.0"

    async def test_get_remote_addon_info_not_found(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_get_remote_addon_info should raise RemoteHostNotFoundError on 404."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        aioclient_mock.get(f"{REMOTE_URL}/api/hassio/addons/missing/info", status=404)

        with pytest.raises(RemoteHostNotFoundError):
            await manager.async_get_remote_addon_info(host.id, "missing")

    async def test_get_remote_addon_logs(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """async_get_remote_addon_logs should return log text from the remote."""
        session = async_get_clientsession(hass)
        manager = RemoteHostManager(hass, session)
        with patch.object(manager._store._store, "async_load", return_value=None):
            await manager.async_setup()

        fernet = Fernet(manager._store._encryption_key)
        host = _make_host(fernet=fernet)
        with patch.object(manager._store._store, "async_delay_save"):
            manager._store.add(host)

        expected_logs = "[2026-01-01 00:00:00] Addon started\n"
        aioclient_mock.get(
            f"{REMOTE_URL}/api/hassio/addons/test/logs",
            text=expected_logs,
        )

        logs = await manager.async_get_remote_addon_logs(host.id, "test")

        assert logs == expected_logs
