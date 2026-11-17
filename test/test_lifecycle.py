"""Tests for EmbyServer start/stop and reconnect handling (no network access)."""

import asyncio
import threading
import unittest
from unittest import mock

import aiohttp

from pyemby import EmbyServer
from pyemby.constants import RECONNECT_MAX_DELAY

from test.test_requests import FakeResponse, FakeSession


class FakeWebSocket:
    """Websocket that stays open until closed."""

    def __init__(self):
        self.sent = []
        self._closed = asyncio.Event()

    async def send_str(self, data):
        self.sent.append(data)

    async def receive(self):
        await self._closed.wait()
        return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)

    async def close(self):
        self._closed.set()


class TestConstruction(unittest.TestCase):

    def test_does_not_change_current_event_loop(self):
        with mock.patch('asyncio.set_event_loop') as set_loop:
            server = EmbyServer('emby.local', 'secret')
            server._event_loop.close()
        set_loop.assert_not_called()


class TestReconnectDelay(unittest.TestCase):

    def test_backoff_grows_and_caps(self):
        delays = [EmbyServer._reconnect_delay(n) for n in range(1, 20)]
        self.assertEqual(delays[:3], [10, 15, 20])
        self.assertEqual(delays, sorted(delays))
        self.assertEqual(max(delays), RECONNECT_MAX_DELAY)


class TestInjectedLoop(unittest.IsolatedAsyncioTestCase):
    """Home Assistant style: the caller owns and runs the loop."""

    async def asyncSetUp(self):
        self.session = FakeSession(result=FakeResponse(200, []))
        self.server = EmbyServer('emby.local', 'secret',
                                 loop=asyncio.get_running_loop(),
                                 session=self.session)

    async def test_start_registers_and_opens_socket(self):
        websocket = FakeWebSocket()

        async def ws_connect(*args, **kwargs):
            return websocket
        self.session.ws_connect = ws_connect

        self.server.start()
        self.assertIsNotNone(self.server._register_task)
        await self.server._register_task
        self.assertTrue(self.server._registered)
        self.assertIsNotNone(self.server._socket_task)
        await asyncio.sleep(0)
        self.assertEqual(len(websocket.sent), 1)
        self.assertIn('SessionsStart', websocket.sent[0])
        await self.server.stop()
        self.assertIsNone(self.server.wsck)

    async def test_failed_registration_does_not_open_socket(self):
        self.session.result = FakeResponse(401)
        self.server.start()
        await self.server._register_task
        self.assertFalse(self.server._registered)
        self.assertIsNone(self.server._socket_task)
        await self.server.stop()

    async def test_stop_cancels_reconnect_backoff_promptly(self):
        """stop() must not wait out a pending reconnect delay."""
        self.session.exc = aiohttp.ClientError('refused')
        self.server._registered = True
        task = asyncio.get_running_loop().create_task(
            self.server.socket_connection())
        self.server._socket_task = task
        await asyncio.sleep(0.05)  # first attempt fails, now in backoff
        self.assertFalse(task.done())

        await asyncio.wait_for(self.server.stop(), 1)
        self.assertTrue(task.done())
        self.assertIsNone(self.server._socket_task)

    async def test_stop_before_start(self):
        await self.server.stop()

    async def test_get_latest_items_returns_task(self):
        self.server._registered = True
        self.session.result = FakeResponse(200, [{'Id': 'x'}])
        task = self.server.get_latest_items('user')
        self.assertEqual(await task, [{'Id': 'x'}])


class TestOwnLoop(unittest.TestCase):
    """loop=None: pyEmby runs its own loop and start() blocks."""

    def test_start_blocks_until_stop(self):
        session = FakeSession(result=FakeResponse(401))
        server = EmbyServer('emby.local', 'secret', session=session)
        thread = threading.Thread(target=server.start)
        thread.start()
        try:
            asyncio.run_coroutine_threadsafe(
                asyncio.sleep(0.05), server._event_loop).result(1)
            self.assertTrue(thread.is_alive())
            asyncio.run_coroutine_threadsafe(
                server.stop(), server._event_loop).result(1)
        finally:
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(server._event_loop.is_closed())


if __name__ == '__main__':
    unittest.main()
