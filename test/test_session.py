"""Tests for EmbyServer aiohttp session handling (no network access)."""

import asyncio
import unittest

import aiohttp

from pyemby import EmbyDevice, EmbyServer

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


class TestApiKeyNotInUrls(unittest.IsolatedAsyncioTestCase):
    """The API key travels in the X-Emby-Token header, never in the URL.

    With ?api_key= in the websocket URL, Emby labels pyEmby's session with
    the API key's name instead of Client="pyEmby", so it could not be
    recognized and hidden. It also keeps the key out of URLs and logs.
    """

    async def asyncSetUp(self):
        self.session = RecordingSession()
        self.server = EmbyServer('emby.local', 'secret',
                                 loop=asyncio.get_running_loop(),
                                 session=self.session)
        # register() starts the websocket task; always shut it down.
        self.addAsyncCleanup(self.server.stop)

    def assert_clean(self, call):
        url, kwargs = call
        self.assertNotIn('secret', url)
        self.assertNotIn('api_key', kwargs.get('params') or {})
        self.assertEqual(kwargs['headers']['X-Emby-Token'], 'secret')
        self.assertIn('Client="pyEmby"',
                      kwargs['headers']['x-emby-authorization'])

    async def test_register_request(self):
        await self.server.register()
        self.assert_clean(self.session.calls['get'][0])

    async def test_websocket_url(self):
        self.server._registered = True
        self.server._shutdown = True  # one attempt only
        await self.server.socket_connection()
        url, _ = self.session.calls['ws_connect'][0]
        self.assertTrue(url.endswith('?DeviceID={}'.format(
            self.server.unique_id)), url)
        self.assert_clean(self.session.calls['ws_connect'][0])

    async def test_playback_command(self):
        device = EmbyDevice({'Id': 'sess-1'}, self.server)
        await device.media_seek(30)
        url, kwargs = self.session.calls['post'][0]
        self.assert_clean(self.session.calls['post'][0])
        self.assertEqual(kwargs['params']['SeekPositionTicks'], 300000000)

    async def test_latest_items(self):
        self.server._registered = True
        await self.server.async_get_latest_items('user-1')
        self.assert_clean(self.session.calls['get'][0])

    def test_image_url_still_carries_key(self):
        """Artwork URLs are fetched separately (e.g. by Home Assistant's
        image proxy) without pyEmby's headers, so they keep the key."""
        device = EmbyDevice({
            'PlayState': {}, 'NowPlayingItem': {
                'Id': 'item-1', 'ImageTags': {'Primary': 'tag'}}},
            self.server)
        self.assertIn('api_key=secret', device.media_image_url)


class RecordingSession:
    """Fake aiohttp session that records every call by method."""

    def __init__(self):
        self.calls = {'get': [], 'post': [], 'ws_connect': []}
        self.ws_exc = aiohttp.ClientConnectionError('no websocket in tests')
        self.closed = False

    async def get(self, url, **kwargs):
        self.calls['get'].append((url, kwargs))
        return FakeResponse(200, [])

    async def post(self, url, **kwargs):
        self.calls['post'].append((url, kwargs))
        return FakeResponse(204)

    async def ws_connect(self, url, **kwargs):
        self.calls['ws_connect'].append((url, kwargs))
        raise self.ws_exc
