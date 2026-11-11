"""M4.5: web overlay server — pages, token, WebSocket events, timing, reconnect."""

import asyncio
import json
import threading
import time

import aiohttp
import pytest

from subwizard.config import WebConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.pipeline.board import SubtitleView
from subwizard.web.qr import qr_matrix, qr_text
from subwizard.web.server import WebOverlayServer, local_ip


def _view(line_id=1, text="Kafanı eğ.", speaker="LUCIA"):
    return SubtitleView(line_id, text, speaker, "Keep your head down.", "gemini", False, 0.0, 300.0)


@pytest.fixture
def server():
    srv = WebOverlayServer(WebConfig(host="127.0.0.1", port=0, heartbeat_s=1.0), MetricsHub())
    assert srv.start()
    yield srv
    srv.stop()


def run(coro):
    return asyncio.run(coro)


async def _get(url):
    async with aiohttp.ClientSession() as s, s.get(url) as r:
        return r.status, await r.text()


def test_pages_are_served_with_defaults(server):
    status, html = run(_get(server.url("/", host="127.0.0.1")))
    assert status == 200 and "WebSocket" in html and '"font": 48' in html
    status, html = run(_get(server.url("/obs", host="127.0.0.1")))
    assert status == 200 and "obs" in html


def test_websocket_gets_hello_subtitle_and_clear_quickly(server):
    async def scenario():
        async with aiohttp.ClientSession() as s, s.ws_connect(
                server.url("/ws", host="127.0.0.1")) as ws:  # fmt: skip
            hello = await ws.receive_json(timeout=2)
            assert hello["type"] == "hello" and hello["lines"] == []
            sent = time.perf_counter()
            server.publish_view(_view())
            event = await ws.receive_json(timeout=2)
            delay_ms = (time.perf_counter() - sent) * 1000
            server.publish_view(None)
            clear = await ws.receive_json(timeout=2)
            return event, clear, delay_ms

    event, clear, delay_ms = run(scenario())
    assert event["type"] == "subtitle" and event["translation"] == "Kafanı eğ."
    assert {"id", "speaker", "source", "translation", "ts", "engine"} <= event.keys()
    assert clear["type"] == "clear"
    assert delay_ms < 100  # M4.5 acceptance: page within 100 ms of the PC overlay


def test_new_page_receives_recent_lines(server):
    server.publish_view(_view(1, "Bir."))
    server.publish_view(_view(1, "Bir, iki."))  # same line, better text: replaced
    server.publish_view(_view(2, "Üç."))
    time.sleep(0.05)

    async def scenario():
        async with aiohttp.ClientSession() as s, s.ws_connect(
                server.url("/ws", host="127.0.0.1")) as ws:  # fmt: skip
            return await ws.receive_json(timeout=2)

    hello = run(scenario())
    assert [e["translation"] for e in hello["lines"]] == ["Bir, iki.", "Üç."]
    assert hello["showing"] is True


def test_client_count_metric(server):
    metrics = server._metrics

    async def scenario():
        async with aiohttp.ClientSession() as s, s.ws_connect(
                server.url("/ws", host="127.0.0.1")) as ws:  # fmt: skip
            await ws.receive_json(timeout=2)
            return server.client_count, metrics.snapshot().gauge(M.WEB_CLIENTS)

    assert run(scenario()) == (1, 1)
    deadline = time.time() + 2
    while server.client_count and time.time() < deadline:
        time.sleep(0.01)
    assert server.client_count == 0


def test_token_is_required_when_set():
    srv = WebOverlayServer(WebConfig(host="127.0.0.1", port=0, token="s3cret"), MetricsHub())
    assert srv.start()
    try:
        bare = f"http://127.0.0.1:{srv.port}/"
        assert run(_get(bare))[0] == 403
        assert run(_get(srv.url("/", host="127.0.0.1")))[0] == 200  # url() adds ?t=
    finally:
        srv.stop()


def test_port_in_use_is_reported_not_raised(server):
    other = WebOverlayServer(WebConfig(host="127.0.0.1", port=server.port), MetricsHub())
    assert other.start() is False
    assert other.error and not other.running


def test_server_restart_on_the_same_port_accepts_a_reconnect():
    """What the page's reconnect loop relies on: after a restart (port change, app
    restart) the same address works again."""
    srv = WebOverlayServer(WebConfig(host="127.0.0.1", port=0), MetricsHub())
    assert srv.start()
    port = srv.port
    srv.stop()
    srv2 = WebOverlayServer(WebConfig(host="127.0.0.1", port=port), MetricsHub())
    try:
        assert srv2.start()
        assert run(_get(f"http://127.0.0.1:{port}/"))[0] == 200
    finally:
        srv2.stop()


def test_page_reconnect_logic_is_present():
    from subwizard.web.server import STATIC_DIR

    js = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "onclose" in js and "setTimeout(connect" in js


def test_publish_without_server_is_a_no_op():
    WebOverlayServer(WebConfig(), MetricsHub()).publish_view(_view())


def test_qr_helpers_and_local_ip():
    m = qr_matrix("http://192.168.1.20:8787/")
    assert len(m) == len(m[0]) > 20 and any(any(r) for r in m)
    assert qr_text("http://x/").count("\n") > 5
    assert local_ip().count(".") == 3


def test_server_runs_on_its_own_thread(server):
    assert any(t.name == "web-overlay" for t in threading.enumerate())
