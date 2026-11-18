"""Tests for EmbyServer HTTP/websocket request handling (no network access).

These are regression tests for the async_timeout 4.0+ incompatibility, where
`with async_timeout.timeout(...)` raised TypeError on every request.
"""

import asyncio
import unittest
from unittest import mock

import aiohttp

from pyemby import server as server_module

from test.test_server import make_server


class FakeResponse:

    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return ''


class FakeSession:
    """Stand-in for aiohttp.ClientSession returning canned results."""

    def __init__(self, result=None, delay=0, exc=None):
        self.result = result
        self.delay = delay
        self.exc = exc
        self.calls = []

    async def _respond(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result

    get = post = ws_connect = _respond


class RequestTestCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.server = make_server()
        self.addCleanup(self.server._event_loop.close)

    def use_session(self, **kwargs):
        self.server._api_session = FakeSession(**kwargs)
        self.server._own_session = False
        return self.server._api_session


class TestApiRequest(RequestTestCase):

    async def test_success_returns_json(self):
        session = self.use_session(result=FakeResponse(200, [{'Id': '1'}]))
        result = await self.server.api_request('http://x/Sessions',
                                               {'api_key': 'k'})
        self.assertEqual(result, [{'Id': '1'}])
        self.assertEqual(session.calls[0][1]['params'], {'api_key': 'k'})

    async def test_bad_status_returns_none(self):
        self.use_session(result=FakeResponse(401))
        self.assertIsNone(await self.server.api_request('http://x', {}))

    async def test_error_payload_returns_none(self):
        self.use_session(result=FakeResponse(
            200, {'error': {'code': 1, 'message': 'bad'}}))
        self.assertIsNone(await self.server.api_request('http://x', {}))

    async def test_client_error_returns_none(self):
        self.use_session(exc=aiohttp.ClientError('boom'))
        self.assertIsNone(await self.server.api_request('http://x', {}))

    async def test_timeout_returns_none(self):
        self.use_session(result=FakeResponse(200, []), delay=1)
        with mock.patch.object(server_module, 'DEFAULT_TIMEOUT', 0.01):
            self.assertIsNone(await self.server.api_request('http://x', {}))


class TestApiPost(RequestTestCase):

    async def test_success(self):
        self.use_session(result=FakeResponse(204))
        self.assertEqual(await self.server.api_post('http://x', {}), '')

    async def test_bad_status_returns_none(self):
        self.use_session(result=FakeResponse(500))
        self.assertIsNone(await self.server.api_post('http://x', {}))

    async def test_timeout_returns_none(self):
        self.use_session(result=FakeResponse(204), delay=1)
        with mock.patch.object(server_module, 'DEFAULT_TIMEOUT', 0.01):
            self.assertIsNone(await self.server.api_post('http://x', {}))


class TestSocketConnection(RequestTestCase):

    async def test_connect_timeout_is_handled(self):
        """A hung websocket handshake times out and is retried, not raised."""
        session = self.use_session(delay=1)
        self.server._registered = True
        self.server._shutdown = True  # exit after the first failure
        self.server.wsck = None
        with mock.patch.object(server_module, 'DEFAULT_TIMEOUT', 0.01):
            await self.server.socket_connection()
        self.assertEqual(len(session.calls), 1)
        self.assertIsNone(self.server.wsck)


if __name__ == '__main__':
    unittest.main()
