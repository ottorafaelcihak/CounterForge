"""Muse Image scene editing and fail-closed image download policy."""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .transport import MAX_IMAGE_BYTES, MetaTransport, inspect_image
from .usage import quiet_wire_logs


class SceneError(ValueError):
    """Fixed code; never carries remote media, URLs, headers or provider errors."""


async def _resolve(host: str) -> tuple[str, ...]:
    results = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return tuple(sorted({result[4][0] for result in results}))


@dataclass(frozen=True)
class URLPolicy:
    # No official generated-CDN host has been verified: production defaults empty.
    trusted_hosts: frozenset[str] = frozenset()
    resolver: Callable[[str], Awaitable[tuple[str, ...]]] = _resolve

    async def validate(self, url: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (
                parsed.scheme != "https"
                or not host
                or parsed.port not in (None, 443)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or host not in self.trusted_hosts
                or host != host.lower()
                or not host.isascii()
                or host.endswith(".")
            ):
                raise ValueError
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError
            addresses = await self.resolver(host)
            if not addresses or any(
                not ipaddress.ip_address(address).is_global for address in addresses
            ):
                raise ValueError
            return host, addresses[0]
        except (ValueError, OSError, UnicodeError):
            raise SceneError("IMAGE_URL_DENIED") from None


class SceneGenerator:
    def __init__(
        self,
        *,
        transport: MetaTransport,
        url_policy: URLPolicy | None = None,
        download_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        quiet_wire_logs()
        self.transport = transport
        self.url_policy = url_policy or URLPolicy()
        self.download_transport = download_transport

    async def create_scene(self, source_bytes: bytes, instruction: str) -> bytes:
        response = await self.transport.image(source_bytes=source_bytes, instruction=instruction)
        if response.outcome != "COMPLETED":
            raise SceneError("IMAGE_PROVIDER_FAILURE")
        try:
            outputs = response.data["data"]
            if (
                not isinstance(outputs, list)
                or len(outputs) != 1
                or not isinstance(outputs[0], dict)
            ):
                raise SceneError("IMAGE_INVALID_RESPONSE")
            item = outputs[0]
            if isinstance(item.get("b64_json"), str):
                encoded = item["b64_json"]
                if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                    raise SceneError("IMAGE_TOO_LARGE")
                data = base64.b64decode(encoded, validate=True)
            elif isinstance(item.get("url"), str):
                data = await self._download(item["url"])
            elif isinstance(item.get("bytes"), bytes):
                data = item["bytes"]
            else:
                raise SceneError("IMAGE_INVALID_RESPONSE")
            inspect_image(data)
            return data
        except SceneError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
            raise SceneError("IMAGE_INVALID_RESPONSE") from None

    async def _download(self, url: str) -> bytes:
        # Pin the validated address, keep Host/SNI for TLS verification. This
        # closes the validation/connection DNS-rebinding gap.
        async with asyncio.timeout(20):
            for _ in range(4):
                host, address = await self.url_policy.validate(url)
                original = httpx.URL(url)
                destination = original.copy_with(host=address)
                try:
                    async with httpx.AsyncClient(
                        transport=self.download_transport,
                        trust_env=False,
                        follow_redirects=False,
                        verify=ssl.create_default_context(),
                        timeout=httpx.Timeout(10, connect=5),
                    ) as client:
                        async with client.stream(
                            "GET",
                            destination,
                            headers={"host": host, "accept-encoding": "identity"},
                            extensions={"sni_hostname": host.encode()},
                        ) as reply:
                            if reply.status_code in {301, 302, 303, 307, 308}:
                                location = reply.headers.get("location")
                                if not location:
                                    raise SceneError("IMAGE_DOWNLOAD_FAILED")
                                url = urljoin(url, location)
                                continue
                            if (
                                reply.status_code != 200
                                or reply.headers.get("content-encoding", "identity").lower()
                                != "identity"
                            ):
                                raise SceneError("IMAGE_DOWNLOAD_FAILED")
                            length = reply.headers.get("content-length")
                            if length and (not length.isdecimal() or int(length) > MAX_IMAGE_BYTES):
                                raise SceneError("IMAGE_TOO_LARGE")
                            chunks: list[bytes] = []
                            size = 0
                            async for chunk in reply.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_IMAGE_BYTES:
                                    raise SceneError("IMAGE_TOO_LARGE")
                                chunks.append(chunk)
                            return b"".join(chunks)
                except (httpx.HTTPError, OSError):
                    raise SceneError("IMAGE_DOWNLOAD_FAILED") from None
        raise SceneError("IMAGE_REDIRECT_LIMIT")
