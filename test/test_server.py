"""Tests for EmbyServer session/device tracking (no network access).

EmbyServer.__init__ opens an aiohttp session, so these tests build a bare
instance and only exercise the pure message/device handling logic.
"""

import asyncio
import copy
import json
import unittest

from pyemby import EmbyDevice, EmbyServer
from pyemby.constants import STATE_OFF
from pyemby.helpers import clean_none_dict_values

from test.test_device import AUDIO_SESSION, EPISODE_SESSION, IDLE_SESSION

DEV_NAME = 'device-1.Emby Theater'
OWN_API_ID = 123456


def make_server():
    server = EmbyServer.__new__(EmbyServer)
    server._event_loop = asyncio.new_event_loop()
    server._api_id = OWN_API_ID
    server._api_key = 'secret'
    server._host = 'emby.local'
    server._port = 8096
    server._ssl = False
    server._headers = {}
    server._api_session = None
    server._own_session = True
    server._own_loop = False
    server._shutdown = False
    server._registered = False
    server._register_task = None
    server._socket_task = None
    server.wsck = None
    server._sessions = None
    server._devices = {}
    server._new_devices_callbacks = []
    server._stale_devices_callbacks = []
    server._update_callbacks = []
    return server


class ServerTestCase(unittest.TestCase):

    def setUp(self):
        self.server = make_server()
        self.new_calls = []
        self.stale_calls = []
        self.update_calls = []
        self.server.add_new_devices_callback(self.new_calls.append)
        self.server.add_stale_devices_callback(self.stale_calls.append)
        self.server.add_update_callback(self.update_calls.append, DEV_NAME)

    def tearDown(self):
        self.server._event_loop.close()

    def feed(self, *sessions):
        """Process a Sessions websocket message and run queued callbacks."""
        msg = json.dumps({'MessageType': 'Sessions',
                          'Data': [copy.deepcopy(s) for s in sessions]})
        self.server.process_msg(msg)
        loop = self.server._event_loop
        loop.call_soon(loop.stop)
        loop.run_forever()


class TestDeviceTracking(ServerTestCase):

    def test_new_device_added(self):
        self.feed(IDLE_SESSION)
        self.assertIn(DEV_NAME, self.server.devices)
        self.assertIsInstance(self.server.devices[DEV_NAME], EmbyDevice)
        self.assertEqual(self.new_calls, [0])

    def test_own_session_ignored(self):
        own = dict(IDLE_SESSION, DeviceId=str(OWN_API_ID))
        self.feed(own)
        self.assertEqual(self.server.devices, {})
        self.assertEqual(self.new_calls, [])

    def test_state_change_fires_update(self):
        self.feed(IDLE_SESSION)
        self.feed(EPISODE_SESSION)
        self.assertEqual(self.update_calls, [DEV_NAME])

    def test_unchanged_idle_does_not_fire_update(self):
        self.feed(IDLE_SESSION)
        self.feed(IDLE_SESSION)
        self.assertEqual(self.update_calls, [])

    def test_device_going_away_is_marked_stale(self):
        self.feed(IDLE_SESSION)
        self.feed()
        self.assertEqual(self.server.devices[DEV_NAME].state, STATE_OFF)
        self.assertEqual(self.stale_calls, [DEV_NAME])
        self.assertEqual(self.update_calls, [DEV_NAME])

    def test_device_returning_fires_new_devices_callback(self):
        self.feed(IDLE_SESSION)
        self.feed()
        self.feed(IDLE_SESSION)
        self.assertEqual(self.new_calls, [0, 0])
        self.assertTrue(self.server.devices[DEV_NAME].is_active)

    def test_non_session_messages_ignored(self):
        self.server.process_msg(json.dumps({'MessageType': 'ForceKeepAlive',
                                            'Data': 60}))
        self.assertIsNone(self.server.sessions)


def session(device_id, client, device_name):
    return dict(IDLE_SESSION, Id='s-' + device_id, DeviceId=device_id,
                Client=client, DeviceName=device_name,
                SupportsRemoteControl=False, PlayableMediaTypes=[])


class TestPyEmbySessionsHidden(ServerTestCase):
    """pyEmby sessions are not media players and must not become devices.

    Mirrors sessions seen on a real Emby 4.11 server: this instance, an
    older run of it, another Home Assistant's pyEmby, and look-alike
    non-pyEmby clients that must stay visible.
    """

    HIDDEN = [
        session(str(OWN_API_ID), 'pyEmby', 'HomeAssistant'),
        session(str(OWN_API_ID), 'pyEmby-Test', 'HomeAssistant'),
        session('old-node-id', 'pyEmby', 'HomeAssistant'),
        session('other-host', 'pyemby', 'SomeOtherHost'),
    ]
    VISIBLE = [
        session('cd0822f3', 'Ersatz', 'HomeAssistant'),
        session('cd0822f3', 'Autopulse', 'HomeAssistant'),
        session('web-1', 'Emby Web', 'HomeAssistant'),
        session('ha-tablet', 'HomeAssistant', 'Kitchen Tablet'),
        # An older pyEmby's websocket session, labeled by Emby with the API
        # key's name. Indistinguishable from any client using a key with
        # that name, so it is left alone (it ages out after upgrading).
        session('77094902115788', 'HomeAssistant', 'HomeAssistant'),
    ]

    def test_only_real_players_become_devices(self):
        self.feed(IDLE_SESSION, *self.HIDDEN, *self.VISIBLE)
        expected = {DEV_NAME} | {
            '{}.{}'.format(s['DeviceId'], s['Client']) for s in self.VISIBLE}
        self.assertEqual(set(self.server.devices), expected)

    def test_hidden_sessions_never_fire_callbacks(self):
        self.feed(*self.HIDDEN)
        self.feed()  # they all go away again
        self.assertEqual(self.server.devices, {})
        self.assertEqual(self.new_calls, [])
        self.assertEqual(self.stale_calls, [])

    def test_hidden_session_does_not_disturb_real_device(self):
        self.feed(IDLE_SESSION, *self.HIDDEN)
        self.feed(IDLE_SESSION)  # pyEmby sessions drop off, player stays
        self.assertTrue(self.server.devices[DEV_NAME].is_active)
        self.assertEqual(self.stale_calls, [])


class TestDeviceId(unittest.TestCase):

    def make(self, **kwargs):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        return EmbyServer('emby.local', 'secret', loop=loop, **kwargs)

    def test_default_is_node_id(self):
        import uuid
        self.assertEqual(self.make().unique_id, uuid.getnode())

    def test_stable_device_id_is_used_everywhere(self):
        server = self.make(device_id='ha-entry-1234')
        self.assertEqual(server.unique_id, 'ha-entry-1234')
        self.assertIn('DeviceId="ha-entry-1234"',
                      server._headers['x-emby-authorization'])
        self.assertIn('Client="pyEmby"',
                      server._headers['x-emby-authorization'])
        self.assertTrue(server._is_pyemby_session(
            {'DeviceId': 'ha-entry-1234', 'Client': 'anything'}))


class TestUpdateCheck(unittest.TestCase):

    def setUp(self):
        self.server = make_server()

    def tearDown(self):
        self.server._event_loop.close()

    def check(self, old, new):
        existing = EmbyDevice(copy.deepcopy(old), self.server)
        return self.server.update_check(existing, copy.deepcopy(new))

    def test_playing_always_updates(self):
        self.assertTrue(self.check(EPISODE_SESSION, EPISODE_SESSION))

    def test_transition_updates(self):
        self.assertTrue(self.check(AUDIO_SESSION, IDLE_SESSION))

    def test_steady_paused_does_not_update(self):
        self.assertFalse(self.check(AUDIO_SESSION, AUDIO_SESSION))

    def test_theme_media_suppressed(self):
        theme = copy.deepcopy(EPISODE_SESSION)
        theme['NowPlayingItem']['IsThemeMedia'] = True
        self.assertFalse(self.check(IDLE_SESSION, theme))


class TestHelpers(unittest.TestCase):

    def test_clean_none_dict_values(self):
        data = [{'a': None, 'b': {'c': None, 'd': 1}, 'e': [{'f': None}]}]
        self.assertEqual(clean_none_dict_values(data),
                         [{'b': {'d': 1}, 'e': [{}]}])

    def test_clean_none_passes_scalars_through(self):
        self.assertEqual(clean_none_dict_values('x'), 'x')
        self.assertEqual(clean_none_dict_values(5), 5)


if __name__ == '__main__':
    unittest.main()
