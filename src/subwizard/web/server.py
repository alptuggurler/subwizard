"""Web overlay server (SPEC 3.10): `/` for a phone / tablet / second monitor, `/obs` for
an OBS Browser Source, `/ws` pushing every translation as JSON. Qt-free.

aiohttp runs its own asyncio loop on a daemon thread. `publish()` is called from the
translator thread the moment a translation is ready (not via the Qt event loop), so the
page and the PC overlay show it at the same time.

Events on /ws:
  {"type": "hello", "config": {...}, "lines": [subtitle, ...]}   on connect (recent lines)
  {"type": "subtitle", "id", "speaker", "source", "translation", "ts", "engine", "fallback"}
  {"type": "clear", "ts"}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import socket
import threading
import time
from collections import deque
from pathlib import Path

from aiohttp import WSMsgType, web

from subwizard.config import WebConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.i18n import t
from subwizard.pipeline.board import SubtitleView

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
START_TIMEOUT_S = 5.0
STOP_TIMEOUT_S = 3.0
RECENT_LINES = 10  # sent on connect, so a page opened mid-scene is not empty
PROBE_ADDRESS = ("10.255.255.255", 1)  # never contacted: only picks the outgoing interface


def local_ip() -> str:
    """This PC's address on the local network (what a phone should open)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(PROBE_ADDRESS)
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def subtitle_event(view: SubtitleView) -> dict:
    return {"type": "subtitle", "id": view.line_id, "speaker": view.speaker or "",
            "source": view.source, "translation": view.text, "ts": time.time(),
            "engine": view.engine, "fallback": view.fallback}  # fmt: skip


class WebOverlayServer:
    def __init__(self, config: WebConfig, metrics: MetricsHub) -> None:
        self._config = config
        self._metrics = metrics
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._recent: deque[dict] = deque(maxlen=RECENT_LINES)
        self._current: dict | None = None
        self.port: int | None = None  # the bound port (config port, or the OS's pick for 0)
        self.error = ""

    # --- lifecycle (any thread) ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.port is not None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def start(self) -> bool:
        if self.running:
            return True
        ready = threading.Event()
        self.error = ""
        self._loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._serve())
            except OSError as exc:  # port in use, no permission
                self.error = str(exc)
                log.error("Web overlay başlatılamadı (port %s): %s", self._config.port, exc)
                ready.set()
                return
            ready.set()
            self._loop.run_forever()
            self._loop.run_until_complete(self._shutdown())
            self._loop.close()

        self._thread = threading.Thread(target=run, name="web-overlay", daemon=True)
        self._thread.start()
        ready.wait(START_TIMEOUT_S)
        if self.running:
            log.info("Web overlay: %s  (OBS: %s)", self.url(), self.url("/obs"))
        return self.running

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(STOP_TIMEOUT_S)
        self._loop = self._thread = None
        self.port = None
        self._metrics.set_gauge(M.WEB_CLIENTS, 0)

    def url(self, path: str = "/", host: str | None = None) -> str:
        host = host or (local_ip() if self._config.host in ("0.0.0.0", "") else self._config.host)
        token = f"?t={self._config.token}" if self._config.token else ""
        return f"http://{host}:{self.port or self._config.port}{path}{token}"

    # --- publishing (any thread) ------------------------------------------------------
    def publish_view(self, view: SubtitleView | None) -> None:
        if view is None:
            self.publish({"type": "clear", "ts": time.time()})
        else:
            self.publish(subtitle_event(view))

    def publish(self, event: dict) -> None:
        loop = self._loop
        if loop is None or not self.running:
            return
        loop.call_soon_threadsafe(self._broadcast, event)

    def _broadcast(self, event: dict) -> None:
        if event["type"] == "subtitle":
            if self._current is not None and self._current.get("id") == event["id"]:
                self._recent.pop()  # same line, better text (typewriter / retranslate)
            self._recent.append(event)
            self._current = event
        else:
            self._current = None
        data = json.dumps(event, ensure_ascii=False)
        for ws in list(self._clients):
            if not ws.closed:
                asyncio.ensure_future(self._send(ws, data))

    @staticmethod
    async def _send(ws: web.WebSocketResponse, data: str) -> None:
        try:
            await ws.send_str(data)
        except (ConnectionError, RuntimeError):
            pass  # the client is gone; its handler removes it

    # --- aiohttp side (loop thread) -----------------------------------------------------
    async def _serve(self) -> None:
        app = web.Application(middlewares=[self._auth])
        app.router.add_get("/", self._page("index.html"))
        app.router.add_get("/obs", self._page("index.html"))  # same page; transparent on /obs
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._config.host, self._config.port)
        await site.start()
        server = site._server  # noqa: SLF001 - the only way to read the bound port (port 0)
        self.port = server.sockets[0].getsockname()[1] if server and server.sockets else self._config.port

    async def _shutdown(self) -> None:
        for ws in list(self._clients):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    @web.middleware
    async def _auth(self, request: web.Request, handler):  # noqa: ANN001, ANN202
        token = self._config.token
        if token and not hmac.compare_digest(request.query.get("t", ""), token):
            raise web.HTTPForbidden(text="token")
        return await handler(request)

    def _page(self, name: str):  # noqa: ANN202
        async def handler(request: web.Request) -> web.Response:
            html = (STATIC_DIR / name).read_text(encoding="utf-8")
            cfg = self._config
            defaults = {"font": cfg.font_px, "lines": cfg.lines, "source": int(cfg.show_source),
                        "reconnect": cfg.reconnect_ms,
                        "text": {k: t(f"web.page.{k}") for k in ("connected", "reconnecting")}
                        | {"fallback": t("subtitle.fallback")}}  # fmt: skip
            html = html.replace("/*DEFAULTS*/{}", json.dumps(defaults))
            return web.Response(text=html, content_type="text/html", charset="utf-8",
                                headers={"Cache-Control": "no-store"})  # fmt: skip

        return handler

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=self._config.heartbeat_s)
        await ws.prepare(request)
        self._clients.add(ws)
        self._metrics.set_gauge(M.WEB_CLIENTS, len(self._clients))
        log.info("Web overlay: istemci bağlandı (%s), toplam %d", request.remote, len(self._clients))
        try:
            await ws.send_str(json.dumps({"type": "hello", "lines": list(self._recent),
                                          "showing": self._current is not None},
                                         ensure_ascii=False))  # fmt: skip
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
            self._metrics.set_gauge(M.WEB_CLIENTS, len(self._clients))
            log.info("Web overlay: istemci ayrıldı, toplam %d", len(self._clients))
        return ws
