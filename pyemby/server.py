"""
pyemby.server
~~~~~~~~~~~~~~~~~~~~
Provides api for Emby server
Copyright (c) 2017-2021 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.

"""

import logging
import json
import uuid
import asyncio
import aiohttp

from pyemby.device import EmbyDevice
from pyemby.constants import (
    __version__, DEFAULT_TIMEOUT, DEFAULT_HEADERS, API_URL, SOCKET_URL,
    RECONNECT_MAX_DELAY, PYEMBY_CLIENT, PYEMBY_DEVICE_NAME,
    STATE_PAUSED, STATE_PLAYING, STATE_IDLE)
from pyemby.helpers import deprecated_name, clean_none_dict_values

_LOGGER = logging.getLogger(__name__)

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
    def __init__(self, host, api_key, port=8096, ssl=False, loop=None,
                 session=None, device_id=None):
        """Initialize base class.

        session: optional aiohttp.ClientSession to use for all requests.
        If omitted, pyEmby creates its own session on first use and closes
        it in stop(). A session passed in is never closed by pyEmby.

        device_id: optional stable ID that pyEmby registers with the server.
        Defaults to uuid.getnode(), which can change between restarts (for
        example when a container gets a new network address); each change
        leaves another stale session and device entry on the server.
        """
        self._host = host
        self._api_key = api_key
        self._port = port
        self._ssl = ssl

        self._sessions = None
        self._devices = {}

        _LOGGER.debug("pyEmby %s initializing new server at: %s",
                      __version__, host)

        if loop is None:
            _LOGGER.info("Creating our own event loop.")
            self._event_loop = asyncio.new_event_loop()
            self._own_loop = True
        else:
            _LOGGER.info("Latching onto an existing event loop.")
            self._event_loop = loop
            self._own_loop = False

        # Enable for asyncio debug logging
        # self._event_loop.set_debug(True)

        self._api_id = (str(device_id) if device_id is not None
                        else uuid.getnode())

        self._headers = DEFAULT_HEADERS.copy()
        self._headers.update({'x-emby-authorization':
                              'MediaBrowser Client="{}",'
                              'Device="{}",'
                              'DeviceId="{}",'
                              'Version="{}"'.format(
                                  PYEMBY_CLIENT, PYEMBY_DEVICE_NAME,
                                  self._api_id, __version__)})

        # The session is created lazily (see _get_session) so that the
        # server object can be constructed outside a running event loop.
        self._api_session = session
        self._own_session = session is None

        self.wsck = None

        # Callbacks
        self._new_devices_callbacks = []
        self._stale_devices_callbacks = []
        self._update_callbacks = []

        self._shutdown = False
        self._registered = False

        # Keep references to background tasks so they are not garbage
        # collected while running, and so stop() can cancel them.
        self._register_task = None
        self._socket_task = None

    def _get_session(self):
        """Return the aiohttp session, creating our own if needed."""
        if self._own_session and (self._api_session is None
                                  or self._api_session.closed):
            self._api_session = aiohttp.ClientSession()
        return self._api_session

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
        self._shutdown = False
        self._register_task = self._event_loop.create_task(
            self._register_loop())

        if self._own_loop:
            _LOGGER.info("Starting up our own event loop.")
            self._event_loop.run_forever()
            self._event_loop.close()
            _LOGGER.info("Connection shut down.")

    async def stop(self):
        """Async method for stopping connectivity with the emby server."""
        self._shutdown = True

        current = asyncio.current_task()
        tasks = [task for task in (self._register_task, self._socket_task)
                 if task is not None and task is not current
                 and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._register_task = None
        self._socket_task = None

        if self.wsck:
            _LOGGER.info('Closing Emby server websocket.')
            await self.wsck.close()
            self.wsck = None

        if self._own_session and self._api_session is not None:
            await self._api_session.close()
            self._api_session = None

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

    async def _register_loop(self):
        """Register with the server, retrying until it succeeds.

        The server may be unreachable when we start (powered off, still
        booting, network down). Keep retrying with backoff instead of
        giving up, so a later-started server is picked up automatically.
        """
        fail_count = 0
        while not self._shutdown:
            if await self.register(log_errors=fail_count == 0):
                if fail_count:
                    _LOGGER.info('Emby server reachable again after %s '
                                 'failed attempt(s).', fail_count)
                return
            fail_count += 1
            delay = self._reconnect_delay(fail_count)
            if fail_count == 1:
                _LOGGER.warning('Unable to register with Emby server at %s, '
                                'will keep retrying (next in %ss).',
                                self.construct_url(API_URL), delay)
            else:
                _LOGGER.debug('Emby registration attempt %s failed, '
                              'retrying in %ss.', fail_count, delay)
            await asyncio.sleep(delay)

    async def register(self, log_errors=True):
        """Register library device id and get initial device list.

        Returns True on success, False if the server could not be reached.
        """
        url = '{}/Sessions'.format(self.construct_url(API_URL))
        params = {'api_key': self._api_key}

        reg = await self.api_request(url, params, log_errors=log_errors)
        if reg is None:
            self._registered = False
            _LOGGER.debug('Unable to register emby client.')
            return False
        else:
            self._registered = True
            _LOGGER.info('Emby client registered!, Id: %s', self.unique_id)
            self._sessions = clean_none_dict_values(reg)

            # Build initial device list.
            self.update_device_list(self._sessions)

            self._socket_task = self._event_loop.create_task(
                self.socket_connection())
            return True

    async def api_post(self, url, params):
        """Make api post request."""
        post = None
        try:
            post = await asyncio.wait_for(
                self._get_session().post(
                    url, params=params, headers=self._headers, ssl=False),
                DEFAULT_TIMEOUT)
            if post.status != 204:
                _LOGGER.error('Error posting Emby data: %s', post.status)
                return None

            post_result = await post.text()
            return post_result

        except (aiohttp.ClientError, asyncio.TimeoutError,
                ConnectionRefusedError) as err:
            _LOGGER.error('Error posting Emby data: %s', err)
            return None

    async def api_request(self, url, params, log_errors=True):
        """Make api fetch request.

        Failures are logged at error level, or at debug level when
        log_errors is False (used for repeated registration retries).
        """
        log = _LOGGER.error if log_errors else _LOGGER.debug
        request = None
        try:
            request = await asyncio.wait_for(
                self._get_session().get(
                    url, params=params, headers=self._headers, ssl=False),
                DEFAULT_TIMEOUT)
            if request.status != 200:
                log('Error fetching Emby data: %s', request.status)
                return None

            request_json = await request.json()
            if 'error' in request_json:
                log('Error converting Emby data to json: %s: %s',
                              request_json['error']['code'],
                              request_json['error']['message'])
                return None
            return request_json
        except (aiohttp.ClientError, asyncio.TimeoutError,
                ConnectionRefusedError) as err:
            log('Error fetching Emby data: %s', err)
            return None

    async def socket_connection(self):
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
                self.wsck = await asyncio.wait_for(
                    self._get_session().ws_connect(
                        url, heartbeat=300, headers=self._headers, ssl=False),
                    DEFAULT_TIMEOUT)

                # Enable sever session updates:
                try:
                    msg = await self.wsck.send_str(
                        '{"MessageType":"SessionsStart", "Data": "0,1500"}')
                except Exception as err:
                    # Catch all for now
                    _LOGGER.error('Failure setting session updates: %s', err)
                    raise ValueError('Session updates error.')

                _LOGGER.debug('Socket Connected!')
                fail_count = 0
                while True:
                    msg = await self.wsck.receive()
                    if msg.type == aiohttp.WSMsgType.text:
                        # A single bad message must never take down the
                        # connection; log it and keep listening.
                        try:
                            self.process_msg(msg.data)
                        except Exception:  # pylint: disable=broad-except
                            _LOGGER.exception(
                                'Error processing Emby message: %.500s',
                                msg.data)

                    elif msg.type == aiohttp.WSMsgType.closed:
                        raise ValueError('Websocket was closed.')
                    elif msg.type == aiohttp.WSMsgType.error:
                        _LOGGER.debug(
                            'Websocket encountered an error: %s', msg)
                        raise ValueError('Websocket error.')

            except Exception as err:  # pylint: disable=broad-except
                # Any failure reconnects; an unexpected exception type must
                # not end this task and leave devices frozen. Cancellation
                # (stop()) is a BaseException and is not caught here.
                if not self._shutdown:
                    fail_count += 1
                    delay = self._reconnect_delay(fail_count)
                    _LOGGER.debug('Websocket unintentionally closed.'
                                  ' Trying reconnect in %ss. Error: %s',
                                  delay, err)
                    await asyncio.sleep(delay)
                    continue
                else:
                    break

    @staticmethod
    def _reconnect_delay(fail_count):
        """Seconds to wait before reconnect attempt number fail_count."""
        return min(5 + 5 * fail_count, RECONNECT_MAX_DELAY)

    def process_msg(self, msg):
        """Process messages from the event stream."""
        jmsg = json.loads(msg)
        msgtype = jmsg.get('MessageType', 'unknown')
        msgdata = jmsg.get('Data', None)

        _LOGGER.debug('New websocket message recieved of type: %s', msgtype)
        if msgtype == 'Sessions':
            self._sessions = clean_none_dict_values(msgdata)
            # Check for new devices and update as needed.
            self.update_device_list(self._sessions)
        """
        May process other message types in the future.
        Other known types are:
        - Ping (no data)
        - PlaybackStarted
        - PlaybackStopped
        - SessionEnded
        """

    def _is_pyemby_session(self, session):
        """Return True if a session belongs to pyEmby rather than a player.

        Matches this instance's own session, any session whose client name
        starts with "pyEmby" (other or earlier pyEmby instances), and the
        "HomeAssistant" client/device name pair that Home Assistant's
        pyEmby sessions have been observed to report.
        """
        if str(session.get('DeviceId')) == str(self._api_id):
            return True
        client = session.get('Client') or ''
        if client.lower().startswith(PYEMBY_CLIENT.lower()):
            return True
        return (client == PYEMBY_DEVICE_NAME
                and session.get('DeviceName') == PYEMBY_DEVICE_NAME)

    def update_device_list(self, sessions):
        """ Update device list. """
        if not isinstance(sessions, list):
            _LOGGER.error('Error updating Emby devices, unexpected session '
                          'data: %.200r', sessions)
            return

        new_devices = []
        active_devices = []
        dev_update = False
        for device in sessions:
            try:
                dev_name = '{}.{}'.format(device['DeviceId'], device['Client'])
            except (KeyError, TypeError):
                _LOGGER.debug('Skipping session without DeviceId/Client: '
                              '%.200r', device)
                continue

            try:
                _LOGGER.debug('Session msg on %s of type: %s, themeflag: %s',
                              dev_name, device['NowPlayingItem']['Type'],
                              device['NowPlayingItem']['IsThemeMedia'])
            except KeyError:
                pass

            if self._is_pyemby_session(device):
                # pyEmby's own session, or another pyEmby instance's (e.g.
                # a second Home Assistant). Not a media player.
                continue

            active_devices.append(dev_name)
            if dev_name not in self._devices:
                _LOGGER.debug('New Emby DeviceID: %s. Adding to device list.',
                              dev_name)
                new = EmbyDevice(device, self)
                self._devices[dev_name] = new
                new_devices.append(new)
            else:
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
            if new.get('PlayState', {}).get('IsPaused'):
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

        return self._event_loop.create_task(self.async_get_latest_items(
            user_id, limit, is_played, include_item_types))

    async def async_get_latest_items(self, user_id, limit=3, is_played='false',
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

        items = await self.api_request(url, params)
        if items is None:
            _LOGGER.debug('Unable to fetch items.')
        else:
            return items
