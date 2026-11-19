"""Tests against a real Emby server.

Skipped unless EMBY_HOST and EMBY_API_KEY are set (EMBY_PORT and EMBY_SSL
optional), so they never run in CI. Run them with:

    EMBY_HOST=192.168.1.10 EMBY_API_KEY=... \\
        .venv/bin/python -m unittest test.test_live -v

No playback commands are sent. The only write is updating pyEmby's own
session capabilities, to make the server push a session update.
"""

import asyncio
import os
import unittest

from pyemby import EmbyDevice, EmbyServer
from pyemby.constants import API_URL

HOST = os.environ.get('EMBY_HOST')
API_KEY = os.environ.get('EMBY_API_KEY')
SSL = os.environ.get('EMBY_SSL', '') in ('1', 'true')
PORT = int(os.environ.get('EMBY_PORT') or (8920 if SSL else 8096))


async def wait_until(predicate, timeout=15):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.1)
    await asyncio.wait_for(poll(), timeout)


@unittest.skipUnless(HOST and API_KEY,
                     'set EMBY_HOST and EMBY_API_KEY to run live tests')
class TestLiveServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.emby = EmbyServer(HOST, API_KEY, port=PORT, ssl=SSL,
                               loop=asyncio.get_running_loop())
        self.addAsyncCleanup(self.emby.stop)

    async def test_register_and_list_devices(self):
        self.assertTrue(await self.emby.register())
        self.assertIsInstance(self.emby.sessions, list)
        for key, device in self.emby.devices.items():
            self.assertIsInstance(device, EmbyDevice)
            # pyEmby's own sessions (this or another instance) are hidden.
            self.assertFalse(self.emby._is_pyemby_session(device.session_raw),
                             key)
            self.assertEqual(key, '{}.{}'.format(device.unique_id,
                                                 device.client))
            # Every property must be readable on real data.
            for prop in ('name', 'username', 'state', 'is_nowplaying',
                         'supports_remote_control', 'media_title',
                         'media_type', 'media_artist', 'media_image_url',
                         'media_position', 'media_runtime',
                         'media_percent_played'):
                getattr(device, prop)

    async def test_websocket_receives_session_updates(self):
        received = []
        original = self.emby.process_msg

        def record(msg):
            received.append(msg)
            original(msg)
        self.emby.process_msg = record

        self.emby.start()
        await wait_until(lambda: self.emby.wsck is not None)
        await asyncio.sleep(0.5)  # let SessionsStart reach the server

        # Emby 4.x only pushes "Sessions" messages when a session changes,
        # not on a timer. Trigger a harmless change to pyEmby's own session
        # (its reported capabilities) so an update is sent.
        url = '{}/Sessions/Capabilities'.format(
            self.emby.construct_url(API_URL))
        await self.emby.api_post(url, {'api_key': API_KEY,
                                       'PlayableMediaTypes': '',
                                       'SupportsMediaControl': 'false'})
        await wait_until(lambda: any('"Sessions"' in m for m in received))
        self.assertIsNotNone(self.emby.wsck)
        self.assertFalse(self.emby._socket_task.done())

    async def test_stop_is_clean(self):
        self.emby.start()
        await wait_until(lambda: self.emby.wsck is not None)
        await asyncio.wait_for(self.emby.stop(), 5)
        self.assertIsNone(self.emby.wsck)
        self.assertIsNone(self.emby._socket_task)

    async def test_bad_api_key_is_rejected(self):
        bad = EmbyServer(HOST, 'not-a-real-key', port=PORT, ssl=SSL,
                         loop=asyncio.get_running_loop())
        self.addAsyncCleanup(bad.stop)
        with self.assertLogs('pyemby.server', 'ERROR'):
            self.assertFalse(await bad.register())


if __name__ == '__main__':
    unittest.main()
