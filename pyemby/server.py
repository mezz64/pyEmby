"""
pyemby.server
~~~~~~~~~~~~~~~~~~~~
Provides api for Emby server
Copyright (c) 2017 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.

"""

import logging
import json
import uuid
import asyncio
import aiohttp
import async_timeout

from pyemby.device import EmbyDevice
from pyemby.constants import (
    __version__, DEFAULT_TIMEOUT, DEFAULT_HEADERS, API_URL, SOCKET_URL,
    STATE_PAUSED, STATE_PLAYING, STATE_IDLE)
from pyemby.helpers import deprecated_name

_LOGGER = logging.getLogger(__name__)

# pylint: disable=invalid-name,no-member
try:
    ensure_future = asyncio.ensure_future
except AttributeError:
    # Python 3.4.3 and earlier has this as async
    ensure_future = asyncio.async

"""
Some general project notes that don't fit anywhere else:

Emby api workflow:
Any command-style actions are completed through the http api, not websockets.
Websocket provides play start and play stop notifications by default.
Can request session updates via:
{"MessageType":"SessionsStart", "Data": "0,1500"}
{"MessageType":"SessionsStop", "Data": ""}

Http api and websocket connection are handled async,
everything else can be done with normal methods
"""


class EmbyServer(object):
    """Emby test."""
    def __init__(self, host, api_key, port=8096, ssl=False, loop=None):
        """Initialize base class."""
        self._host = host
        self._api_key = api_key
        self._port = port
        self._ssl = ssl

        self._sessions = None
        self._devices = {}

        if loop is None:
            _LOGGER.info("Creating our own event loop.")
            self._event_loop = asyncio.new_event_loop()
            self._own_loop = True
        else:
            _LOGGER.info("Latching onto an existing event loop.")
            self._event_loop = loop
            self._own_loop = False

        asyncio.set_event_loop(self._event_loop)

        # Enable for asyncio debug logging
        # self._event_loop.set_debug(True)

        self._api_id = uuid.getnode()

        headers = DEFAULT_HEADERS.copy()
        headers.update({'x-emby-authorization':
                        'MediaBrowser Client="pyEmby",'
                        'Device="HomeAssistant",'
                        'DeviceId="{}",'
                        'Version="{}"'.format(
                            self._api_id, __version__)})

        conn = aiohttp.TCPConnector(verify_ssl=False)
        self._api_session = aiohttp.ClientSession(
            connector=conn, headers=headers, loop=self._event_loop)

        self.wsck = None

        # Callbacks
        self._new_devices_callbacks = []
        self._stale_devices_callbacks = []
        self._update_callbacks = []

        self._shutdown = False
        self._registered = False

    @property
    def unique_id(self):
        """Return unique ID for connection to Emby."""
        return self._api_id

    @property
    def api_key(self):
        """ Return api key. """
        return self._api_key

    @property
    @deprecated_name('get_sessions')
    def sessions(self):
        """ Return sessions json. """
        return self._sessions

    @property
    def devices(self):
        """ Return devices dictionary. """
        return self._devices

    def add_new_devices_callback(self, callback):
        """Register as callback for when new devices are added. """
        self._new_devices_callbacks.append(callback)
        _LOGGER.debug('Added new devices callback to %s', callback)

    def _do_new_devices_callback(self, msg):
        """Call registered callback functions."""
        for callback in self._new_devices_callbacks:
            _LOGGER.debug('Devices callback %s', callback)
            self._event_loop.call_soon(callback, msg)

    def add_stale_devices_callback(self, callback):
        """Register as callback for when stale devices exist. """
        self._stale_devices_callbacks.append(callback)
        _LOGGER.debug('Added stale devices callback to %s', callback)

    def _do_stale_devices_callback(self, msg):
        """Call registered callback functions."""
        for callback in self._stale_devices_callbacks:
            _LOGGER.debug('Stale Devices callback %s', callback)
            self._event_loop.call_soon(callback, msg)

    def add_update_callback(self, callback, device):
        """Register as callback for when a matching device changes."""
        self._update_callbacks.append([callback, device])
        _LOGGER.debug('Added update callback to %s on %s', callback, device)

    def remove_update_callback(self, callback, device):
        """ Remove a registered update callback. """
        if [callback, device] in self._update_callbacks:
            self._update_callbacks.remove([callback, device])
            _LOGGER.debug('Removed update callback %s for %s',
                          callback, device)

    def _do_update_callback(self, msg):
        """Call registered callback functions."""
        for callback, device in self._update_callbacks:
            if device == msg:
                _LOGGER.debug('Update callback %s for device %s by %s',
                              callback, device, msg)
                self._event_loop.call_soon(callback, msg)

    def start(self):
        """Public method for initiating connectivity with the emby server."""
        ensure_future(self.register(), loop=self._event_loop)

        if self._own_loop:
            _LOGGER.info("Starting up our own event loop.")
            self._event_loop.run_forever()
            self._event_loop.close()
            _LOGGER.info("Connection shut down.")

    @asyncio.coroutine
    def stop(self):
        """Async method for stopping connectivity with the emby server."""
        self._shutdown = True

        if self.wsck:
            _LOGGER.info('Closing Emby server websocket.')
            yield from self.wsck.close()
            self.wsck = None

        if self._own_loop:
            _LOGGER.info("Shutting down Emby server loop...")
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)

    def construct_url(self, style):
        """ Return http/https or ws/wss url. """
        if style is API_URL:
            if self._ssl:
                return 'https://{}:{}'.format(self._host, self._port)
            else:
                return 'http://{}:{}'.format(self._host, self._port)
        elif style is SOCKET_URL:
            if self._ssl:
                return 'wss://{}:{}'.format(self._host, self._port)
            else:
                return 'ws://{}:{}'.format(self._host, self._port)
        else:
            return None

    @asyncio.coroutine
    def register(self):
        """Register library device id and get initial device list. """
        url = '{}/Sessions'.format(self.construct_url(API_URL))
        params = {'api_key': self._api_key}

        reg = yield from self.api_request(url, params)
        if reg is None:
            self._registered = False
            _LOGGER.error('Unable to register emby client.')
        else:
            self._registered = True
            _LOGGER.info('Emby client registered!, Id: %s', self.unique_id)
            self._sessions = reg

            # Build initial device list.
            self.update_device_list(self._sessions)

            ensure_future(self.socket_connection(), loop=self._event_loop)

    @asyncio.coroutine
    def api_post(self, url, params):
        """Make api post request."""
        post = None
        try:
            with async_timeout.timeout(DEFAULT_TIMEOUT, loop=self._event_loop):
                post = yield from self._api_session.post(
                    url, params=params)
            if post.status != 204:
                _LOGGER.error('Error posting Emby data: %s', post.status)
                return None

            post_result = yield from post.text()
            return post_result

        except (aiohttp.ClientError, asyncio.TimeoutError,
                ConnectionRefusedError) as err:
            _LOGGER.error('Error posting Emby data: %s', err)
            return None

    @asyncio.coroutine
    def api_request(self, url, params):
        """Make api fetch request."""
        request = None
        try:
            with async_timeout.timeout(DEFAULT_TIMEOUT, loop=self._event_loop):
                request = yield from self._api_session.get(
                    url, params=params)
            if request.status != 200:
                _LOGGER.error('Error fetching Emby data: %s', request.status)
                return None

            request_json = yield from request.json()
            if 'error' in request_json:
                _LOGGER.error('Error converting Emby data to json: %s: %s',
                              request_json['error']['code'],
                              request_json['error']['message'])
                return None
            return request_json
        except (aiohttp.ClientError, asyncio.TimeoutError,
                ConnectionRefusedError) as err:
            _LOGGER.error('Error fetching Emby data: %s', err)
            return None

    @asyncio.coroutine
    def socket_connection(self):
        """ Open websocket connection. """
        if not self._registered:
            _LOGGER.error('Client not registered, cannot start socket.')
            return

        url = '{}?DeviceID={}&api_key={}'.format(
            self.construct_url(SOCKET_URL), self._api_id, self._api_key)

        fail_count = 0
        while True:
            _LOGGER.debug('Attempting Socket Connection.')
            try:
                with async_timeout.timeout(DEFAULT_TIMEOUT,
                                           loop=self._event_loop):
                    self.wsck = yield from self._api_session.ws_connect(url)

                # Enable sever session updates:
                try:
                    self.wsck.send_str(
                        '{"MessageType":"SessionsStart", "Data": "0,1500"}')
                except Exception as err:
                    # Catch all for now
                    _LOGGER.error('Failure setting session updates: %s', err)
                    raise ValueError('Session updates error.')

                _LOGGER.debug('Socket Connected!')
                fail_count = 0
                while True:
                    msg = yield from self.wsck.receive()
                    if msg.tp == aiohttp.WSMsgType.text:
                        # Process data
                        self.process_msg(msg.data)

                    elif msg.tp == aiohttp.WSMsgType.closed:
                        raise ValueError('Websocket was closed.')
                    elif msg.tp == aiohttp.WSMsgType.error:
                        _LOGGER.debug(
                            'Websocket encountered an error: %s', msg)
                        raise ValueError('Websocket error.')

            except (aiohttp.ClientError, asyncio.TimeoutError,
                    aiohttp.WSServerHandshakeError,
                    ConnectionRefusedError, OSError, ValueError) as err:
                if not self._shutdown:
                    fail_count += 1
                    _LOGGER.debug('Websocket unintentionally closed.'
                                  ' Trying reconnect in %ss. Error: %s',
                                  (fail_count * 5) + 5, err)
                    yield from asyncio.sleep(15, self._event_loop)
                    continue
                else:
                    break

    def process_msg(self, msg):
        """Process messages from the event stream."""
        jmsg = json.loads(msg)
        msgtype = jmsg['MessageType']
        msgdata = jmsg['Data']

        _LOGGER.debug('New websocket message recieved of type: %s', msgtype)
        if msgtype == 'Sessions':
            self._sessions = msgdata
            # Check for new devices and update as needed.
            self.update_device_list(self._sessions)
        """
        May process other message types in the future.
        Other known types are:
        - PlaybackStarted
        - PlaybackStopped
        - SessionEnded
        """

    def update_device_list(self, sessions):
        """ Update device list. """
        if sessions is None:
            _LOGGER.error('Error updating Emby devices.')
            return

        new_devices = []
        active_devices = []
        dev_update = False
        for device in sessions:
            dev_name = '{}.{}'.format(device['DeviceId'], device['Client'])

            try:
                _LOGGER.debug('Session msg on %s of type: %s, themeflag: %s',
                              dev_name, device['NowPlayingItem']['Type'],
                              device['NowPlayingItem']['IsThemeMedia'])
            except KeyError:
                pass

            active_devices.append(dev_name)
            if dev_name not in self._devices and \
                    device['DeviceId'] != str(self._api_id):
                _LOGGER.debug('New Emby DeviceID: %s. Adding to device list.',
                              dev_name)
                new = EmbyDevice(device, self)
                self._devices[dev_name] = new
                new_devices.append(new)
            elif device['DeviceId'] != str(self._api_id):
                # Before we send in new data check for changes to state
                # to decide if we need to fire the update callback
                if not self._devices[dev_name].is_active:
                    # Device wasn't active on the last update
                    # We need to fire a device callback to let subs now
                    dev_update = True

                do_update = self.update_check(
                    self._devices[dev_name], device)
                self._devices[dev_name].update_data(device)
                self._devices[dev_name].set_active(True)
                if dev_update:
                    self._do_new_devices_callback(0)
                    dev_update = False
                if do_update:
                    self._do_update_callback(dev_name)

        # Need to check for new inactive devices and flag
        for dev_id in self._devices:
            if dev_id not in active_devices:
                # Device no longer active
                if self._devices[dev_id].is_active:
                    self._devices[dev_id].set_active(False)
                    self._do_update_callback(dev_id)
                    self._do_stale_devices_callback(dev_id)

        # Call device callback if new devices were found.
        if new_devices:
            self._do_new_devices_callback(0)

    def update_check(self, existing, new):
        """ Check device state to see if we need to fire the callback.

        True if either state is 'Playing'
        False if both states are: 'Paused', 'Idle', or 'Off'
        True on any state transition.
        """
        old_state = existing.state
        if 'NowPlayingItem' in existing.session_raw:
            try:
                old_theme = existing.session_raw['NowPlayingItem']['IsThemeMedia']
            except KeyError:
                old_theme = False
        else:
            old_theme = False

        if 'NowPlayingItem' in new:
            if new['PlayState']['IsPaused']:
                new_state = STATE_PAUSED
            else:
                new_state = STATE_PLAYING

            try:
                new_theme = new['NowPlayingItem']['IsThemeMedia']
            except KeyError:
                new_theme = False

        else:
            new_state = STATE_IDLE
            new_theme = False

        if old_theme or new_theme:
            return False
        elif old_state == STATE_PLAYING or new_state == STATE_PLAYING:
            return True
        elif old_state != new_state:
            return True
        else:
            return False

    def get_latest_items(self, user_id, limit=3, is_played='false',
                         include_item_types='episode'):
        """ Get latest items by scheduling the worker method. """
        if not self._registered:
            _LOGGER.debug('Client not registered, cannot get items.')
            return

        def return_result(future):
            """ Return result. """
            return future.result()

        run_coro = ensure_future(self.async_get_latest_items(
            user_id, limit, is_played, include_item_types),
                                 loop=self._event_loop)
        run_coro.add_done_callback(return_result)

    @asyncio.coroutine
    def async_get_latest_items(self, user_id, limit=3, is_played='false',
                               include_item_types='episode'):
        """ Return XX most recent movie or episode additions to library"""
        if not self._registered:
            _LOGGER.debug('Client not registered, cannot get items.')
            return

        url = '{0}/Users/{1}/Items/Latest'.format(
            self.construct_url(API_URL), user_id)
        params = {'api_key': self._api_key,
                  'IncludeItemTypes': include_item_types,
                  'Limit': limit,
                  'IsPlayed': is_played}

        items = yield from self.api_request(url, params)
        if items is None:
            _LOGGER.debug('Unable to fetch items.')
        else:
            return items
