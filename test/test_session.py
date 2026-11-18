"""Tests for EmbyServer aiohttp session handling (no network access)."""

import asyncio
import unittest

import aiohttp

from pyemby import EmbyServer

from test.test_requests import FakeResponse, FakeSession


class TestConstruction(unittest.TestCase):

    def test_construct_outside_running_loop(self):
        """Creating the server must not need a running event loop."""
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        server = EmbyServer('emby.local', 'secret', loop=loop)
        self.assertIsNone(server._api_session)


class TestOwnSession(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.server = EmbyServer('emby.local', 'secret',
                                 loop=asyncio.get_running_loop())

    async def test_session_created_lazily_and_reused(self):
        session = self.server._get_session()
        self.assertIsInstance(session, aiohttp.ClientSession)
        self.assertIs(self.server._get_session(), session)
        await self.server.stop()

    async def test_stop_closes_own_session(self):
        session = self.server._get_session()
        await self.server.stop()
        self.assertTrue(session.closed)
        self.assertIsNone(self.server._api_session)

    async def test_session_recreated_after_stop(self):
        first = self.server._get_session()
        await self.server.stop()
        second = self.server._get_session()
        self.assertIsNot(first, second)
        self.assertFalse(second.closed)
        await self.server.stop()


class TestExternalSession(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.session = FakeSession(result=FakeResponse(200, []))
        self.server = EmbyServer('emby.local', 'secret',
                                 loop=asyncio.get_running_loop(),
                                 session=self.session)

    async def test_external_session_used(self):
        self.assertIs(self.server._get_session(), self.session)
        await self.server.api_request('http://x', {'api_key': 'k'})
        kwargs = self.session.calls[0][1]
        self.assertIn('x-emby-authorization', kwargs['headers'])
        self.assertIs(kwargs['ssl'], False)

    async def test_stop_leaves_external_session_alone(self):
        self.session.closed = False

        async def close():
            self.session.closed = True
        self.session.close = close

        await self.server.stop()
        self.assertFalse(self.session.closed)
        self.assertIs(self.server._api_session, self.session)


if __name__ == '__main__':
    unittest.main()
