"""The settings page: a small local web server with a JSON API and live server-sent events."""

import asyncio
import json
from pathlib import Path

from aiohttp import web

from . import claude
from .config import SETTINGS_PORT, TRAINER_ACTIONS, parse_config
from .log import log

PAGE = Path(__file__).with_name("settings.html")
ALLOWED_HOSTS = {f"127.0.0.1:{SETTINGS_PORT}", f"localhost:{SETTINGS_PORT}"}


@web.middleware
async def local_only(request, handler):
    # Keeps other websites from poking the API (DNS rebinding, cross-site form posts).
    if request.host not in ALLOWED_HOSTS:
        raise web.HTTPForbidden()
    if request.method != "GET" and request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType()
    return await handler(request)


async def json_body(request):
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="expected JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return body


class SettingsServer:
    def __init__(self, bridge):
        self.bridge = bridge
        self.app = web.Application(middlewares=[local_only])
        self.app.add_routes([
            web.get("/", self.page),
            web.get("/api/state", self.state),
            web.put("/api/config", self.put_config),
            web.put("/api/paused", self.put_paused),
            web.put("/api/trainer", self.put_trainer),
            web.put("/api/quit", self.put_quit),
            web.get("/api/rides", self.rides),
            web.put("/api/claude-desktop", self.put_claude_desktop),
            web.get("/api/events", self.events),
        ])

    async def start(self):
        """Serve the page; raises OSError if the port is taken (Zwanki already running)."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", SETTINGS_PORT).start()

    async def page(self, request):
        return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    async def state(self, request):
        return web.json_response(self.bridge.snapshot())

    async def put_config(self, request):
        try:
            config = parse_config(await json_body(request))
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        await self.bridge.set_config(config)
        return web.json_response(self.bridge.snapshot())

    async def put_paused(self, request):
        self.bridge.set_paused(bool((await json_body(request)).get("paused")))
        return web.json_response(self.bridge.snapshot())

    async def put_trainer(self, request):
        action = (await json_body(request)).get("action")
        if action not in TRAINER_ACTIONS:
            raise web.HTTPBadRequest(text=f"action must be one of {', '.join(TRAINER_ACTIONS)}")
        result = await self.bridge.trainer_action(action)
        return web.json_response({"result": result, **self.bridge.snapshot()})

    async def rides(self, request):
        return web.json_response(self.bridge.rides_overview())

    async def put_claude_desktop(self, request):
        try:
            path = claude.add_to_claude_desktop()
        except (OSError, ValueError, RuntimeError) as e:
            raise web.HTTPBadRequest(text=f"couldn't update Claude Desktop's settings: {e}")
        log(f"added Zwanki to {path}")
        return web.json_response(claude.status())

    async def put_quit(self, request):
        asyncio.get_running_loop().call_later(0.2, self.bridge.quit)  # answer the page first
        return web.json_response({"quitting": True})

    async def events(self, request):
        """Server-sent events: live button, pressure, status, trainer and activity updates."""
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await resp.prepare(request)
        queue = asyncio.Queue()
        self.bridge.listeners.add(queue)
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except TimeoutError:
                    chunk = ": keep-alive\n\n"
                await resp.write(chunk.encode())
        except ConnectionResetError:
            pass  # page closed
        finally:
            self.bridge.listeners.discard(queue)
        return resp
