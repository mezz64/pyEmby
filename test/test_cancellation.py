"""Cancellation must always stop pyEmby's background tasks.

On Python 3.11 and earlier, asyncio.wait_for swallows a cancellation that
arrives in the same loop iteration as the wrapped call finishing: instead
of raising CancelledError it returns the call's result or raises its
exception. pyEmby's retry loops treat that as an ordinary failure and keep
going, so a cancelled task could loop forever. stop() was already safe
(it sets the shutdown flag first), but anything that cancels the task
directly, such as an event loop shutting down, was not.

Each test cancels the task at exactly that moment: the wrapped call is
completed and the task is cancelled within the same loop iteration.
"""

import asyncio
import unittest

import aiohttp

from pyemby import EmbyServer
from pyemby.server import _wait_with_timeout

from test.test_requests import FakeResponse


class GatedSession:
    """Fake aiohttp session whose calls wait until the test releases them."""

    def __init__(self):
        self.gate = None
        self.calls = 0

    async def _wait(self, *args, **kwargs):
        self.calls += 1
        self.gate = asyncio.get_running_loop().create_future()
        return await self.gate

    get = post = ws_connect = _wait


class TestCancellationIsNeverSwallowed(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.session = GatedSession()
        self.server = EmbyServer('emby.local', 'secret',
                                 loop=asyncio.get_running_loop(),
                                 session=self.session)
        self.server._reconnect_delay = lambda fail_count: 0

    async def until_waiting(self):
        while self.session.gate is None or self.session.gate.done():
            await asyncio.sleep(0)

    async def cancel_as_call_finishes(self, task, finish):
        await self.until_waiting()
        finish(self.session.gate)  # the wrapped call completes...
        task.cancel()              # ...and the task is cancelled, same tick
        done, _ = await asyncio.wait({task}, timeout=1)
        self.assertTrue(done, 'task kept running after being cancelled')
        self.assertTrue(task.cancelled())

    async def test_websocket_connect_failing(self):
        self.server._registered = True
        task = asyncio.ensure_future(self.server.socket_connection())
        await self.cancel_as_call_finishes(
            task, lambda gate: gate.set_exception(
                aiohttp.ClientConnectionError('refused')))

    async def test_registration_failing(self):
        task = asyncio.ensure_future(self.server._register_loop())
        await self.cancel_as_call_finishes(
            task, lambda gate: gate.set_result(FakeResponse(503)))

    async def test_playback_command_cancelled(self):
        task = asyncio.ensure_future(self.server.api_post('http://x', {}))
        await self.cancel_as_call_finishes(
            task, lambda gate: gate.set_result(FakeResponse(204)))


class TestWaitWithTimeout(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result(self):
        async def value():
            return 42
        self.assertEqual(await _wait_with_timeout(value(), 1), 42)

    async def test_raises_inner_exception(self):
        async def boom():
            raise ValueError('boom')
        with self.assertRaises(ValueError):
            await _wait_with_timeout(boom(), 1)

    async def test_timeout_cancels_inner(self):
        inner = None

        async def slow():
            nonlocal inner
            inner = asyncio.current_task()
            await asyncio.sleep(10)
        with self.assertRaises(asyncio.TimeoutError):
            await _wait_with_timeout(slow(), 0.01)
        self.assertTrue(inner.cancelled())

    async def test_outer_cancel_cancels_inner(self):
        inner = None
        started = asyncio.Event()

        async def slow():
            nonlocal inner
            inner = asyncio.current_task()
            started.set()
            await asyncio.sleep(10)
        outer = asyncio.ensure_future(_wait_with_timeout(slow(), 10))
        await started.wait()
        outer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await outer
        await asyncio.sleep(0)
        self.assertTrue(inner.cancelled())


if __name__ == '__main__':
    unittest.main()
