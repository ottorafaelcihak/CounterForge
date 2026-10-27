import socket

import pytest
from pytest_socket import SocketBlockedError


def test_default_suite_cannot_dispatch_outbound_provider_calls(monkeypatch):
    monkeypatch.setenv("RUN_META_LIVE_TESTS", "1")
    with pytest.warns(UserWarning, match="socket.socket"), pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
