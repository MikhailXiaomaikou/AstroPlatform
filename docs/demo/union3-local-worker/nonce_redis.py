#!/usr/bin/env python
"""Small RESP2 server for the local demo's nonce replay store.

This is deliberately not a production Redis substitute.  It implements the
atomic ``SET ... NX EX`` operation used by signed Worker requests, plus the
few connection-management commands issued by redis-py.  Unknown commands fail
closed, which also keeps Celery work off this local fixture; the durable
PostgreSQL reconciler handles the demo's verification wake-up instead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time
from dataclasses import dataclass, field


class ProtocolError(RuntimeError):
    """Raised for malformed RESP input."""


async def _read_command(reader: asyncio.StreamReader) -> list[bytes] | None:
    line = await reader.readline()
    if not line:
        return None
    if not line.endswith(b"\r\n") or not line.startswith(b"*"):
        raise ProtocolError("only RESP2 arrays are accepted")
    try:
        count = int(line[1:-2])
    except ValueError as exc:
        raise ProtocolError("invalid array length") from exc
    if count <= 0 or count > 64:
        raise ProtocolError("invalid command length")
    values: list[bytes] = []
    for _ in range(count):
        size_line = await reader.readline()
        if not size_line.endswith(b"\r\n") or not size_line.startswith(b"$"):
            raise ProtocolError("commands must contain bulk strings")
        try:
            size = int(size_line[1:-2])
        except ValueError as exc:
            raise ProtocolError("invalid bulk-string length") from exc
        if size < 0 or size > 1_048_576:
            raise ProtocolError("bulk string exceeds the demo limit")
        value = await reader.readexactly(size)
        if await reader.readexactly(2) != b"\r\n":
            raise ProtocolError("bulk string terminator is missing")
        values.append(value)
    return values


def _simple(value: str) -> bytes:
    return b"+" + value.encode("utf-8") + b"\r\n"


def _error(value: str) -> bytes:
    return b"-ERR " + value.encode("utf-8") + b"\r\n"


def _integer(value: int) -> bytes:
    return f":{value}\r\n".encode("ascii")


def _bulk(value: bytes | None) -> bytes:
    if value is None:
        return b"$-1\r\n"
    return f"${len(value)}\r\n".encode("ascii") + value + b"\r\n"


@dataclass
class NonceStore:
    values: dict[bytes, bytes] = field(default_factory=dict)
    expiries: dict[bytes, float] = field(default_factory=dict)

    def _expire(self, key: bytes) -> None:
        deadline = self.expiries.get(key)
        if deadline is not None and deadline <= time.monotonic():
            self.values.pop(key, None)
            self.expiries.pop(key, None)

    def get(self, key: bytes) -> bytes | None:
        self._expire(key)
        return self.values.get(key)

    def set(
        self,
        key: bytes,
        value: bytes,
        *,
        only_if_missing: bool,
        ttl_seconds: float | None,
    ) -> bool:
        self._expire(key)
        if only_if_missing and key in self.values:
            return False
        self.values[key] = value
        if ttl_seconds is None:
            self.expiries.pop(key, None)
        else:
            self.expiries[key] = time.monotonic() + ttl_seconds
        return True


def _set_response(store: NonceStore, parts: list[bytes]) -> bytes:
    if len(parts) < 3:
        return _error("wrong number of arguments for SET")
    only_if_missing = False
    ttl_seconds: float | None = None
    index = 3
    while index < len(parts):
        option = parts[index].upper()
        if option == b"NX":
            only_if_missing = True
            index += 1
        elif option in {b"EX", b"PX"} and index + 1 < len(parts):
            try:
                duration = int(parts[index + 1])
            except ValueError:
                return _error("invalid expire time in SET")
            if duration <= 0:
                return _error("invalid expire time in SET")
            ttl_seconds = duration if option == b"EX" else duration / 1000
            index += 2
        else:
            return _error("unsupported SET option")
    stored = store.set(
        parts[1],
        parts[2],
        only_if_missing=only_if_missing,
        ttl_seconds=ttl_seconds,
    )
    return _simple("OK") if stored else _bulk(None)


async def _serve_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    store: NonceStore,
) -> None:
    try:
        while True:
            parts = await _read_command(reader)
            if parts is None:
                break
            command = parts[0].upper()
            if command == b"PING":
                response = _simple("PONG") if len(parts) == 1 else _bulk(parts[1])
            elif command in {b"CLIENT", b"SELECT"}:
                response = _simple("OK")
            elif command == b"SET":
                response = _set_response(store, parts)
            elif command == b"GET" and len(parts) == 2:
                response = _bulk(store.get(parts[1]))
            elif command == b"PUBLISH":
                response = _integer(0)
            elif command == b"QUIT":
                writer.write(_simple("OK"))
                await writer.drain()
                break
            else:
                response = _error(
                    f"command {command.decode('ascii', errors='replace')} "
                    "is outside the nonce fixture"
                )
            writer.write(response)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, ProtocolError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


async def _main(port: int) -> None:
    store = NonceStore()
    server = await asyncio.start_server(
        lambda reader, writer: _serve_connection(reader, writer, store),
        host="127.0.0.1",
        port=port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6399)
    arguments = parser.parse_args()
    asyncio.run(_main(arguments.port))
