#!/usr/bin/env python3
"""Exercise pyEmby against a real Emby server.

Connection settings come from flags or environment variables:

    EMBY_HOST      server host name or IP            (required)
    EMBY_API_KEY   API key from Dashboard > API Keys  (required)
    EMBY_PORT      port, default 8096 (8920 with --ssl)
    EMBY_SSL       set to 1 to use https/wss

Commands:

    monitor              watch devices and print every event (default)
    info                 register once, list sessions, exit
    control KEY ACTION   send play|pause|stop|next|previous|seek SECONDS
    capture FILE         save raw /Sessions JSON (for building test fixtures)

KEY for `control` may be the full device key (<DeviceId>.<Client>) or any
unique, case-insensitive substring of the key or device name.

Add -v for pyEmby debug logging.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from pyemby import EmbyServer
from pyemby.constants import API_URL

_LOGGER = logging.getLogger('emby_live')


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default=os.environ.get('EMBY_HOST'))
    parser.add_argument('--api-key', default=os.environ.get('EMBY_API_KEY'))
    parser.add_argument('--port', type=int,
                        default=int(os.environ['EMBY_PORT'])
                        if os.environ.get('EMBY_PORT') else None)
    parser.add_argument('--ssl', action='store_true',
                        default=os.environ.get('EMBY_SSL', '') in ('1', 'true'))
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='enable pyEmby debug logging')
    parser.add_argument('--timeout', type=float, default=15,
                        help='seconds to wait for the server (info/control/capture)')

    sub = parser.add_subparsers(dest='command')
    sub.add_parser('monitor', help='watch devices and events')
    sub.add_parser('info', help='list current sessions and exit')
    ctl = sub.add_parser('control', help='send a playback command')
    ctl.add_argument('device')
    ctl.add_argument('action', choices=['play', 'pause', 'stop', 'next',
                                        'previous', 'seek'])
    ctl.add_argument('position', nargs='?', type=float,
                     help='seconds, for seek')
    cap = sub.add_parser('capture', help='save raw session JSON')
    cap.add_argument('file')

    args = parser.parse_args()
    args.command = args.command or 'monitor'
    if not args.host or not args.api_key:
        parser.error('set EMBY_HOST and EMBY_API_KEY (or pass --host/--api-key)')
    if args.port is None:
        args.port = 8920 if args.ssl else 8096
    if args.command == 'control' and args.action == 'seek' \
            and args.position is None:
        parser.error('seek needs a position in seconds')
    return args


def describe(device):
    """One-line summary of a device's current state."""
    parts = [device.state]
    if device.is_nowplaying:
        title = device.media_title or '?'
        if device.media_series_title:
            title = '{} S{}E{} {}'.format(device.media_series_title,
                                         device.media_season,
                                         device.media_episode, title)
        elif device.media_artist:
            title = '{} - {}'.format(device.media_artist, title)
        parts.append('[{}] {}'.format(device.media_type, title))
        if device.media_position is not None and device.media_runtime:
            parts.append('{:.0f}/{:.0f}s ({:.0f}%)'.format(
                device.media_position, device.media_runtime,
                device.media_percent_played))
    if not device.supports_remote_control:
        parts.append('(no remote control)')
    return '  '.join(parts)


def find_device(emby, query):
    if query in emby.devices:
        return emby.devices[query]
    query = query.lower()
    matches = [dev for key, dev in emby.devices.items()
               if query in key.lower() or query in (dev.name or '').lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit('No device matches {!r}. Run `info` to list devices.'.format(query))
    sys.exit('{!r} matches several devices:\n  {}'.format(
        query, '\n  '.join('{} ({})'.format(d.name, key)
                           for key, d in emby.devices.items() if d in matches)))


def print_devices(emby):
    if not emby.devices:
        print('No client sessions reported by the server.')
    for key, device in sorted(emby.devices.items()):
        print('{}  ({}, user: {})\n    key: {}\n    {}'.format(
            device.name, device.client, device.username or '-', key,
            describe(device)))


async def wait_registered(emby, timeout):
    """Start the connection and wait until the initial session list loads."""
    emby.start()
    deadline = time.monotonic() + timeout
    while not emby._registered:  # pylint: disable=protected-access
        if time.monotonic() > deadline:
            raise SystemExit('Could not register with {} within {:.0f}s; '
                             'see the log above.'.format(
                                 emby.construct_url(API_URL), timeout))
        await asyncio.sleep(0.1)


async def monitor(emby):
    loop = asyncio.get_running_loop()
    watched = set()
    started = time.monotonic()

    def log(event, key):
        device = emby.devices.get(key)
        name = device.name if device else key
        detail = describe(device) if device else ''
        print('{:7.1f}s  {:<8} {}  {}'.format(
            time.monotonic() - started, event, name, detail), flush=True)

    def on_update(key):
        log('update', key)

    def on_new(_):
        for key in emby.devices:
            if key not in watched:
                watched.add(key)
                emby.add_update_callback(on_update, key)
                log('new', key)

    def on_stale(key):
        log('stale', key)

    emby.add_new_devices_callback(on_new)
    emby.add_stale_devices_callback(on_stale)
    emby.start()
    print('Monitoring {} (Ctrl-C to quit)'.format(emby.construct_url(API_URL)),
          flush=True)
    await loop.create_future()  # run until interrupted


async def run(args):
    emby = EmbyServer(args.host, args.api_key, port=args.port, ssl=args.ssl,
                      loop=asyncio.get_running_loop())
    try:
        if args.command == 'monitor':
            await monitor(emby)
        elif args.command == 'info':
            await wait_registered(emby, args.timeout)
            print_devices(emby)
        elif args.command == 'capture':
            await wait_registered(emby, args.timeout)
            with open(args.file, 'w', encoding='utf-8') as handle:
                json.dump(emby.sessions, handle, indent=2, sort_keys=True)
            print('Wrote {} session(s) to {}. It contains user names, device '
                  'IDs and IP addresses: review before sharing or committing.'
                  .format(len(emby.sessions or []), args.file))
        elif args.command == 'control':
            await wait_registered(emby, args.timeout)
            device = find_device(emby, args.device)
            print('Before: {}  {}'.format(device.name, describe(device)))
            action = {'play': device.media_play, 'pause': device.media_pause,
                      'stop': device.media_stop, 'next': device.media_next,
                      'previous': device.media_previous}.get(args.action)
            await (device.media_seek(args.position) if args.action == 'seek'
                   else action())
            await asyncio.sleep(2)  # let the websocket report the change
            print('After:  {}  {}'.format(device.name, describe(device)))
    finally:
        await emby.stop()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
        datefmt='%H:%M:%S')
    if not args.verbose:
        logging.getLogger('pyemby').setLevel(logging.WARNING)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
