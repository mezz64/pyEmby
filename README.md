[![PyPI](https://img.shields.io/pypi/v/pyEmby.svg)](https://pypi.python.org/pypi/pyEmby)
[![Tests](https://github.com/mezz64/pyEmby/actions/workflows/tests.yml/badge.svg)](https://github.com/mezz64/pyEmby/actions/workflows/tests.yml)

# Introduction

pyEmby is an asyncio Python library for the [Emby Media Server](https://emby.media) API. It tracks the server's client sessions in real time and can control playback on them:

* **`EmbyServer`**: connects to the server, loads the current sessions, then subscribes to session updates over a websocket. It keeps a dictionary of `EmbyDevice` objects and calls your callbacks when devices appear, change, or go away. The websocket reconnects automatically if the connection drops.
* **`EmbyDevice`**: one Emby client session (a TV app, web browser, phone, etc.). It exposes what is playing (title, series/season/episode, album/artist, artwork, position, runtime) and the playback state, and can send play, pause, stop, next, previous, and seek commands.

pyEmby was written mainly for the [Home Assistant Emby integration](https://www.home-assistant.io/integrations/emby/). The integration's source is a complete usage example.

Code is licensed under the MIT license.

# Requirements

* Python 3.10+
* [aiohttp](https://pypi.org/project/aiohttp/) >= 3.0
* [async_timeout](https://pypi.org/project/async-timeout/)
* An Emby server and an API key (Emby dashboard → *Advanced* → *API Keys*)

# Installation

```
pip install pyemby
```

# Usage

`EmbyServer` runs on an asyncio event loop. Pass in your application's running loop. If you pass `loop=None`, pyEmby creates a loop of its own, and `start()` blocks while that loop runs.

```python
import asyncio
from pyemby import EmbyServer


async def main():
    emby = EmbyServer('192.168.1.10', 'YOUR_API_KEY', port=8096, ssl=False,
                      loop=asyncio.get_running_loop())

    def devices_changed(_):
        for dev_id, device in emby.devices.items():
            print(dev_id, device.name, device.state, device.media_title)

    def device_stale(dev_id):
        print('Device went away:', dev_id)

    emby.add_new_devices_callback(devices_changed)
    emby.add_stale_devices_callback(device_stale)

    emby.start()          # registers with the server and opens the websocket
    try:
        await asyncio.sleep(3600)
    finally:
        await emby.stop()

asyncio.run(main())
```

Device keys in `emby.devices` have the form `<DeviceId>.<Client>`. Use these keys with `add_update_callback` to watch one device:

```python
emby.add_update_callback(lambda dev_id: print('Updated:', dev_id), 'abc123.Emby Web')
```

Playback commands are coroutines:

```python
device = emby.devices['abc123.Emby Web']
await device.media_pause()
await device.media_seek(90)   # seconds
```

## `EmbyServer`

`EmbyServer(host, api_key, port=8096, ssl=False, loop=None)`

### Callbacks
* `add_new_devices_callback(callback)`: called when new devices are discovered, or when a known device becomes active again.
* `add_stale_devices_callback(callback)`: called with the device key when a device is no longer in the session list.
* `add_update_callback(callback, device_key)` / `remove_update_callback(callback, device_key)`: called with the device key when that device's playback state changes.

### Properties
* `devices`: dictionary of `EmbyDevice` objects keyed by `<DeviceId>.<Client>`
* `sessions`: the most recent raw session list from the server
* `unique_id`: the client ID pyEmby registers with the server
* `api_key`: the configured API key

### Functions
* `start()`: register with the server and open the websocket. This call blocks if pyEmby owns the event loop.
* `stop()` *(coroutine)*: close the websocket and stop reconnecting
* `async_get_latest_items(user_id, limit=3, is_played='false', include_item_types='episode')` *(coroutine)*: the most recently added library items for a user

## `EmbyDevice`

### Properties
* Identity: `unique_id`, `session_id`, `name`, `client`, `username`, `supports_remote_control`
* State: `state` (`Playing`, `Paused`, `Idle`, `Off`), `is_active`, `is_nowplaying`
* Media: `media_id`, `media_type`, `media_title`, `media_series_title`, `media_season`, `media_episode`, `media_album_name`, `media_artist`, `media_album_artist`, `media_image_url`
* Progress (in seconds): `media_position`, `media_runtime`, `media_percent_played`
* `session_raw`: the raw session dictionary from the server

### Functions (coroutines)
* `media_play()`, `media_pause()`, `media_stop()`, `media_next()`, `media_previous()`
* `media_seek(position)`: seek to `position` seconds
* `set_playstate(state, pos=0)`: send any Emby playstate command

# Development

```
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s test -v
```

The tests run offline against sample session data. They run on every pull request across all supported Python versions.

## Releasing

Releases go to PyPI automatically through [trusted publishing](https://docs.pypi.org/trusted-publishers/):

1. Bump `__version__` in `pyemby/constants.py`. `setup.py` reads the version from there.
2. Commit and merge to `master`.
3. Tag the commit with the exact version and push the tag:
   ```
   git tag 1.11 -m "1.11 release"
   git push origin 1.11
   ```

The *Publish to PyPI* workflow checks that the tag matches the package version, then builds the sdist and wheel and uploads them.

# Version history

* **1.0+**: Rewritten on asyncio. Device updates arrive over a websocket, so polling is no longer needed.
* **0.2 and earlier**: A simple, synchronous `requests`-based interface. It is still available from the `0.2` tag and the `original` branch.
