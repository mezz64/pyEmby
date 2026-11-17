"""Tests for EmbyDevice session parsing (no network access)."""

import copy
import unittest

from pyemby import EmbyDevice
from pyemby.constants import (
    API_URL, STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING)

IDLE_SESSION = {
    'Id': 'session-1',
    'DeviceId': 'device-1',
    'DeviceName': 'Living Room TV',
    'Client': 'Emby Theater',
    'UserName': 'john',
    'SupportsRemoteControl': True,
    'PlayState': {'IsPaused': False},
}

EPISODE_SESSION = dict(
    IDLE_SESSION,
    PlayState={'IsPaused': False, 'PositionTicks': 6000000000},
    NowPlayingItem={
        'Id': 'item-42',
        'Name': 'Pilot',
        'Type': 'Episode',
        'SeriesName': 'Some Show',
        'ParentIndexNumber': 1,
        'IndexNumber': 1,
        'RunTimeTicks': 24000000000,
        'ImageTags': {'Primary': 'primary-tag'},
    },
)

AUDIO_SESSION = dict(
    IDLE_SESSION,
    PlayState={'IsPaused': True},
    NowPlayingItem={
        'Id': 'track-7',
        'Name': 'Song',
        'Type': 'Audio',
        'Album': 'Album',
        'AlbumArtist': 'Band',
        'Artists': ['Singer', 'Guest'],
        'ImageTags': {'Thumb': 'thumb-tag', 'Primary': 'primary-tag'},
    },
)


class FakeServer:
    """Minimal stand-in for EmbyServer used by EmbyDevice."""

    api_key = 'secret'

    def construct_url(self, style):
        assert style == API_URL
        return 'http://emby.local:8096'


def make_device(session):
    return EmbyDevice(copy.deepcopy(session), FakeServer())


class TestDeviceIdentity(unittest.TestCase):

    def test_identity_fields(self):
        dev = make_device(IDLE_SESSION)
        self.assertEqual(dev.session_id, 'session-1')
        self.assertEqual(dev.unique_id, 'device-1')
        self.assertEqual(dev.name, 'Living Room TV')
        self.assertEqual(dev.client, 'Emby Theater')
        self.assertEqual(dev.username, 'john')
        self.assertTrue(dev.supports_remote_control)

    def test_missing_fields_return_none(self):
        dev = make_device({})
        self.assertIsNone(dev.session_id)
        self.assertIsNone(dev.unique_id)
        self.assertIsNone(dev.name)
        self.assertIsNone(dev.media_title)
        self.assertIsNone(dev.media_position)
        self.assertIsNone(dev.media_runtime)
        self.assertIsNone(dev.media_percent_played)


class TestDeviceState(unittest.TestCase):

    def test_idle(self):
        dev = make_device(IDLE_SESSION)
        self.assertEqual(dev.state, STATE_IDLE)
        self.assertFalse(dev.is_nowplaying)
        self.assertIsNone(dev.media_image_url)

    def test_playing(self):
        dev = make_device(EPISODE_SESSION)
        self.assertEqual(dev.state, STATE_PLAYING)
        self.assertTrue(dev.is_nowplaying)

    def test_paused(self):
        dev = make_device(AUDIO_SESSION)
        self.assertEqual(dev.state, STATE_PAUSED)
        self.assertTrue(dev.is_nowplaying)

    def test_inactive_is_off(self):
        dev = make_device(EPISODE_SESSION)
        dev.set_active(False)
        self.assertEqual(dev.state, STATE_OFF)
        self.assertFalse(dev.is_nowplaying)


class TestDeviceMedia(unittest.TestCase):

    def test_episode_fields(self):
        dev = make_device(EPISODE_SESSION)
        self.assertEqual(dev.media_title, 'Pilot')
        self.assertEqual(dev.media_type, 'Episode')
        self.assertEqual(dev.media_series_title, 'Some Show')
        self.assertEqual(dev.media_season, 1)
        self.assertEqual(dev.media_episode, 1)
        self.assertEqual(dev.media_id, 'item-42')
        self.assertEqual(dev.media_position, 600)
        self.assertEqual(dev.media_runtime, 2400)
        self.assertEqual(dev.media_percent_played, 25)

    def test_audio_fields(self):
        dev = make_device(AUDIO_SESSION)
        self.assertEqual(dev.media_album_name, 'Album')
        self.assertEqual(dev.media_album_artist, 'Band')
        self.assertEqual(dev.media_artist, 'Singer, Guest')

    def test_artist_variants(self):
        cases = [
            (['Solo'], 'Solo'),
            (['Singer', 'Guest'], 'Singer, Guest'),
            ([], None),
            ('Already a string', 'Already a string'),
        ]
        for artists, expected in cases:
            with self.subTest(artists=artists):
                session = copy.deepcopy(AUDIO_SESSION)
                session['NowPlayingItem']['Artists'] = artists
                self.assertEqual(make_device(session).media_artist, expected)

    def test_artist_missing(self):
        session = copy.deepcopy(AUDIO_SESSION)
        del session['NowPlayingItem']['Artists']
        self.assertIsNone(make_device(session).media_artist)

    def test_image_url_prefers_thumb(self):
        dev = make_device(AUDIO_SESSION)
        self.assertEqual(
            dev.media_image_url,
            'http://emby.local:8096/Items/track-7/Images/Thumb'
            '?width=500&tag=thumb-tag&api_key=secret')

    def test_image_url_falls_back_to_primary(self):
        dev = make_device(EPISODE_SESSION)
        self.assertEqual(
            dev.media_image_url,
            'http://emby.local:8096/Items/item-42/Images/Primary'
            '?width=500&tag=primary-tag&api_key=secret')


if __name__ == '__main__':
    unittest.main()
