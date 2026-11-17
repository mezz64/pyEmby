"""Connection reliability tests (no network access).

Covers two failure modes reported against the Home Assistant integration:

* The server being unreachable at startup left the integration dead until a
  restart, because registration was attempted once and never retried
  (home-assistant/core#34446, #36512, #71551, #115730).
* An unexpected exception while handling one websocket message ended the
  websocket task, freezing every device until a restart
  (home-assistant/core#38501, #90230, mezz64/pyEmby#7).
"""

import asyncio
import copy
import json
import unittest
from unittest import mock

import aiohttp

from pyemby import EmbyDevice, EmbyServer
from pyemby.constants import STATE_PLAYING

from test.test_device import IDLE_SESSION
from test.test_requests import FakeResponse

DEV_NAME = 'device-1.Emby Theater'


def sessions_msg(*sessions):
    return json.dumps({'MessageType': 'Sessions',
                       'Data': [copy.deepcopy(s) for s in sessions]})


class ScriptedWebSocket:
    """Websocket that delivers queued messages, then stays open."""

    def __init__(self, messages=()):
        self.queue = asyncio.Queue()
        for msg in messages:
            self.feed(msg)

    def feed(self, item):
        """Queue a text message, a WSMessage, or an exception to raise."""
        if isinstance(item, str):
            item = aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, item, None)
        self.queue.put_nowait(item)

    async def send_str(self, data):
        pass

    async def receive(self):
        item = await self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.feed(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None))


class ScriptedSession:
    """Session whose GET responses and websockets are scripted per call."""

    def __init__(self, get_results=(), websockets=()):
        self.get_results = list(get_results)
        self.websockets = list(websockets)
        self.get_calls = 0
        self.ws_calls = 0

    async def get(self, *args, **kwargs):
        self.get_calls += 1
        result = (self.get_results.pop(0) if len(self.get_results) > 1
                  else self.get_results[0])
        if isinstance(result, BaseException):
            raise result
        return result

    async def ws_connect(self, *args, **kwargs):
        self.ws_calls += 1
        if self.websockets:
            return self.websockets.pop(0)
        return ScriptedWebSocket()


class ReliabilityTestCase(unittest.IsolatedAsyncioTestCase):

    def make_server(self, session):
        server = EmbyServer('emby.local', 'secret',
                            loop=asyncio.get_running_loop(), session=session)
        # No real waiting between retries in tests.
        server._reconnect_delay = lambda fail_count: 0
        self.addAsyncCleanup(server.stop)
        return server

    async def wait_for(self, predicate, timeout=1):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(poll(), timeout)


class TestRegistrationRetry(ReliabilityTestCase):

    async def test_server_unreachable_at_startup_is_retried(self):
        session = ScriptedSession(get_results=[
            aiohttp.ClientConnectionError('refused'),
            asyncio.TimeoutError(),
            FakeResponse(503),
            FakeResponse(200, [copy.deepcopy(IDLE_SESSION)]),
        ])
        server = self.make_server(session)
        server.start()

        await self.wait_for(lambda: server._registered)
        self.assertEqual(session.get_calls, 4)
        self.assertIn(DEV_NAME, server.devices)
        await self.wait_for(lambda: session.ws_calls == 1)

    async def test_retry_logs_one_warning_not_one_per_attempt(self):
        session = ScriptedSession(get_results=[FakeResponse(503)])
        server = self.make_server(session)
        with self.assertLogs('pyemby.server', 'WARNING') as logs:
            server.start()
            await self.wait_for(lambda: session.get_calls >= 5)
            await server.stop()
        # First attempt: one error from the request plus one warning that
        # retrying has started. Later attempts log only at debug level.
        self.assertEqual([r.levelname for r in logs.records],
                         ['ERROR', 'WARNING'])
        self.assertTrue(logs.records[1].getMessage()
                        .startswith('Unable to register'))

    async def test_register_reports_success(self):
        session = ScriptedSession(get_results=[FakeResponse(503)])
        server = self.make_server(session)
        self.assertFalse(await server.register())
        session.get_results = [FakeResponse(200, [])]
        self.assertTrue(await server.register())


class TestMessageErrorIsolation(ReliabilityTestCase):

    async def start_connected(self, websocket):
        session = ScriptedSession(get_results=[FakeResponse(200, [])],
                                  websockets=[websocket])
        server = self.make_server(session)
        server.start()
        await self.wait_for(lambda: session.ws_calls == 1)
        return server, session

    async def test_bad_messages_do_not_drop_connection(self):
        websocket = ScriptedWebSocket()
        server, session = await self.start_connected(websocket)

        with self.assertLogs('pyemby.server', 'ERROR'):
            websocket.feed('not json at all')
            websocket.feed(json.dumps({'MessageType': 'Sessions',
                                       'Data': 'unexpected string'}))
            websocket.feed(json.dumps({'MessageType': 'Sessions',
                                       'Data': [42, None, {'Id': 'x'}]}))
            websocket.feed(sessions_msg(IDLE_SESSION))
            await self.wait_for(lambda: DEV_NAME in server.devices)

        self.assertEqual(session.ws_calls, 1)  # never reconnected
        self.assertFalse(server._socket_task.done())

    async def test_exception_inside_processing_is_contained(self):
        websocket = ScriptedWebSocket()
        server, session = await self.start_connected(websocket)

        with mock.patch.object(server, 'update_device_list',
                               side_effect=[RuntimeError('boom'), None]):
            with self.assertLogs('pyemby.server', 'ERROR') as logs:
                websocket.feed(sessions_msg(IDLE_SESSION))
                websocket.feed(sessions_msg(IDLE_SESSION))
                await self.wait_for(
                    lambda: server.update_device_list.call_count == 2)

        self.assertIn('Error processing Emby message',
                      logs.records[0].getMessage())
        self.assertEqual(session.ws_calls, 1)
        self.assertFalse(server._socket_task.done())

    async def test_unexpected_receive_error_reconnects(self):
        """An exception type nobody anticipated reconnects, not dies."""
        first = ScriptedWebSocket([RuntimeError('unexpected')])
        second = ScriptedWebSocket([sessions_msg(IDLE_SESSION)])
        session = ScriptedSession(get_results=[FakeResponse(200, [])],
                                  websockets=[first, second])
        server = self.make_server(session)
        server.start()

        await self.wait_for(lambda: DEV_NAME in server.devices)
        self.assertEqual(session.ws_calls, 2)
        self.assertFalse(server._socket_task.done())


class TestTolerantParsing(unittest.TestCase):

    def test_now_playing_without_playstate(self):
        session = dict(IDLE_SESSION, NowPlayingItem={'Name': 'x'})
        del session['PlayState']
        device = EmbyDevice(session, server=None)
        self.assertEqual(device.state, STATE_PLAYING)

    def test_missing_supports_remote_control(self):
        session = dict(IDLE_SESSION)
        del session['SupportsRemoteControl']
        self.assertFalse(EmbyDevice(session, server=None)
                         .supports_remote_control)

    def test_update_check_without_playstate(self):
        server = EmbyServer.__new__(EmbyServer)
        existing = EmbyDevice(dict(IDLE_SESSION), server)
        new = dict(IDLE_SESSION, NowPlayingItem={'Name': 'x'})
        del new['PlayState']
        self.assertTrue(server.update_check(existing, new))


if __name__ == '__main__':
    unittest.main()
