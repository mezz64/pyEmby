"""
pyemby.constants
~~~~~~~~~~~~~~~~~~~~
Constants list
Copyright (c) 2017-2026 John Mihalic <https://github.com/mezz64>
Licensed under the MIT license.
"""

__version__ = '1.10'

DEFAULT_TIMEOUT = 10

# Websocket reconnect backoff: 10s, 15s, 20s, ... capped at this value.
RECONNECT_MAX_DELAY = 60

DEFAULT_HEADERS = {
    'Content-Type': "application/json",
    'Accept': "application/json",
}

API_URL = 'api'
SOCKET_URL = 'socket'

STATE_PLAYING = 'Playing'
STATE_PAUSED = 'Paused'
STATE_IDLE = 'Idle'
STATE_OFF = 'Off'
