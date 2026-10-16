
import os

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NWOLOGGER_OFFLINE", "1")
os.environ.setdefault("NWOLOGGER_SETTINGS", os.path.join(os.path.dirname(__file__), ".pytest-settings.ini"))


class FakeKeyring:
    """An in-memory keychain, so tests never read or write the real one."""

    from keyring import errors

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.refuse_delete = False

    def get_password(self, service, account):
        return self.store.get((service, account))

    def set_password(self, service, account, secret):
        self.store[(service, account)] = secret

    def delete_password(self, service, account):
        if self.refuse_delete or (service, account) not in self.store:
            raise self.errors.PasswordDeleteError("not deleted")
        del self.store[(service, account)]


@pytest.fixture(autouse=True)
def no_real_tqsl_or_log(monkeypatch, tmp_path):
    """Tests never read your TQSL station locations or write to your NWOlogger log folder."""
    monkeypatch.setenv("TQSLDIR", str(tmp_path / "no-tqsl"))
    monkeypatch.setenv("NWOLOGGER_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture(autouse=True)
def keyring_store(monkeypatch):
    from nwologger.core import secrets

    fake = FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Service requests fail the test unless it installs its own handler with ``mock_net``."""
    from nwologger.core import net

    def refuse(req):
        raise AssertionError(f"a test tried to reach the network: {req.url.host}")

    monkeypatch.setattr(net, "transport", httpx.MockTransport(refuse))


@pytest.fixture
def mock_net(monkeypatch):
    """``mock_net(handler)`` answers service requests with ``handler(request) -> httpx.Response``; the list it
    returns records every request that reached the transport."""
    from nwologger.core import net

    def use(handler):
        seen: list[httpx.Request] = []

        def h(req):
            seen.append(req)
            return handler(req)

        monkeypatch.setattr(net, "transport", httpx.MockTransport(h))
        return seen

    return use
