"""
Live updates for open pages. Each page keeps one connection to /api/events (server-sent events);
whenever something changes an account's table (a roll on another device, a camera throw, a new
guest), every page that account has open hears about it and reloads what it shows.
"""

import asyncio
import json
import threading

_loop = None
_lock = threading.Lock()
_listeners = {}          # user id -> set of asyncio.Queue


def bind(loop):
    """Remember the server's event loop, so other threads can publish onto it."""
    global _loop
    _loop = loop


def listen(user_id):
    queue = asyncio.Queue(maxsize=100)
    with _lock:
        _listeners.setdefault(user_id, set()).add(queue)
    return queue


def forget(user_id, queue):
    with _lock:
        queues = _listeners.get(user_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                del _listeners[user_id]


def _deliver(queues, message):
    for queue in queues:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:          # a page that stopped reading; it reloads everything when it's back
            pass


def publish(user_id, kind, **data):
    """Tell this account's open pages. Safe to call from any thread."""
    with _lock:
        queues = list(_listeners.get(user_id, ()))
    if not queues or _loop is None:
        return
    message = json.dumps({"type": kind, **data})
    _loop.call_soon_threadsafe(_deliver, queues, message)


async def stream(user_id, queue, keepalive=20):
    """The text of the event stream. A comment every few seconds keeps proxies from closing it."""
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=keepalive)
                yield f"data: {message}\n\n"
            except asyncio.TimeoutError:
                yield ": still here\n\n"
    finally:
        forget(user_id, queue)
