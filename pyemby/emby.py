"""
pyemby.emby
~~~~~~~~~~~~~~~~~~~~
Provides python api for Emby mediaserver
Copyright (c) 2016 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.
"""
import logging
import uuid
import requests

from pyemby.constants import (
    __version__, DEFAULT_HEADERS)

_LOGGER = logging.getLogger(__name__)


class EmbyRemote(object):
    """Emby API Connection Handler."""

    def __init__(self, api_key, server_url):
        """Initialize Emby API class."""
        self.api_key = api_key
        self.server_url = server_url
        self.emby_id = uuid.uuid4().hex

        # Build requests session
        self.emby_request = requests.Session()
        self.emby_request.timeout = 5
        self.emby_request.stream = False
        self.emby_request.params = {'api_key': self.api_key}
        self.emby_request.headers.update(DEFAULT_HEADERS)

    @property
    def unique_id(self):
        """Return unique ID for connection to Emby."""
        return self.emby_id

    @property
    def get_sessions_url(self):
        """Return the session url."""
        return self.server_url + '/Sessions'

    def get_sessions(self):
        """Return active client sessions."""
        try:
            response = self.emby_request.get(self.get_sessions_url)
        except requests.exceptions.RequestException as err:
            _LOGGER.error('Requests error getting sessions: %s', err)
            return
        else:
            clients = response.json()
            return clients

    def set_playstate(self, session, state):
        """Send media commands to client."""
        # url = self.playstate_url.format(
        #    session['Id'], state, self.api_key)
        # url = self.playstate_url(session['Id'], state)
        url = '{}/{}/Playing/{}'.format(
            self.get_sessions_url, session['Id'], state)
        headers = {'x-emby-authorization':
                   'MediaBrowser Client="Emby Mobile",'
                   'Device="pyEmby",'
                   'DeviceId="{}",'
                   'Version="{}"'.format(
                       self.unique_id, __version__)}

        _LOGGER.debug('Playstate request state: %s, URL: %s', state, url)

        try:
            self.emby_request.post(url, headers=headers)
        except requests.exceptions.RequestException as err:
            _LOGGER.error('Requests error setting playstate: %s', err)
            return

    def play(self, session):
        """Call play command."""
        self.set_playstate(session, 'unpause')

    def pause(self, session):
        """Call pause command."""
        self.set_playstate(session, 'pause')

    def stop(self, session):
        """Call stop command."""
        self.set_playstate(session, 'stop')

    def next_track(self, session):
        """Call next track command."""
        self.set_playstate(session, 'nexttrack')

    def previous_track(self, session):
        """Call previous track command."""
        self.set_playstate(session, 'previoustrack')

    def get_image(self, item_id, style, played=0):
        """Return media image."""
        return '{0}/Items/{1}/Images/{2}?api_key={3}&PercentPlayed={4}'.format(
            self.server_url, item_id, style, self.api_key, played)
