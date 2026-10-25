"""AR003 T010：真实 HTTP embedding 客户端契约（FR4-1，禁 mock 后端）。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jev.errors import JevProtocolError, JevTimeoutError, JevUnavailableError
from jev.kernel.cerebrum.embeddings import EmbeddingClient, EmbeddingService


class _EmbedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", "0"))
        server.requests.append(json.loads(self.rfile.read(length).decode("utf-8")))
        if server.delay_s:
            time.sleep(server.delay_s)
        status, payload, raw = server.status, server.payload, None
        if isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass


class _EmbedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _EmbedHandler)
        self.requests: list[dict] = []
        self.status = 200
        self.payload: object = {"data": []}
        self.delay_s = 0.0


@pytest.fixture()
def embed_server():
    server = _EmbedServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def client(embed_server):
    instance = EmbeddingClient(
        f"http://127.0.0.1:{embed_server.server_address[1]}", timeout_s=2.0
    )
    yield instance
    instance.close()


def _vectors(*rows):
    return {"data": [{"index": i, "embedding": list(row)} for i, row in enumerate(rows)]}


def test_success_returns_vectors_in_request_order(embed_server, client):
    embed_server.payload = _vectors([0.1, 0.2], [0.3, 0.4])
    vectors = client.embed(["甲", "乙"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert embed_server.requests[0] == {"model": "local", "input": ["甲", "乙"]}
    embed_server.payload = _vectors([0.7])
    assert client.available() is True


def test_empty_input_skips_http(embed_server, client):
    assert client.embed([]) == []
    assert embed_server.requests == []


def test_too_many_texts_rejected(client):
    with pytest.raises(ValueError):
        client.embed(["x"] * 65)


def test_http_error_is_protocol_error(embed_server, client):
    embed_server.status = 500
    embed_server.payload = {"error": "boom"}
    with pytest.raises(JevProtocolError):
        client.embed(["甲"])
    assert client.available() is False


def test_invalid_json_is_protocol_error(embed_server, client):
    embed_server.payload = "not-json"
    with pytest.raises(JevProtocolError):
        client.embed(["甲"])


def test_count_mismatch_rejected(embed_server, client):
    embed_server.payload = _vectors([0.1])
    with pytest.raises(JevProtocolError):
        client.embed(["甲", "乙"])


def test_missing_vector_rejected(embed_server, client):
    embed_server.payload = {"data": [{"index": 0}]}
    with pytest.raises(JevProtocolError):
        client.embed(["甲"])


def test_non_numeric_vector_rejected(embed_server, client):
    embed_server.payload = {"data": [{"embedding": ["NaN?"]}]}
    with pytest.raises(JevProtocolError):
        client.embed(["甲"])


def test_dimension_mismatch_rejected(embed_server, client):
    embed_server.payload = _vectors([0.1, 0.2], [0.3])
    with pytest.raises(JevProtocolError):
        client.embed(["甲", "乙"])


def test_timeout_maps_to_jev_timeout(embed_server):
    client = EmbeddingClient(
        f"http://127.0.0.1:{embed_server.server_address[1]}", timeout_s=0.05
    )
    embed_server.delay_s = 0.3
    try:
        with pytest.raises(JevTimeoutError):
            client.embed(["甲"])
    finally:
        client.close()


def test_unreachable_maps_to_unavailable():
    client = EmbeddingClient("http://127.0.0.1:9", timeout_s=0.5)
    try:
        with pytest.raises(JevUnavailableError):
            client.embed(["甲"])
        assert client.available() is False
    finally:
        client.close()


def test_direct_service_proxies_client(embed_server, client):
    service = EmbeddingService(None, client)
    embed_server.payload = _vectors([0.5, 0.5])
    assert service.base_url.endswith(str(embed_server.server_address[1]))
    assert service.available() is True
    assert service.embed(["甲"]) == [[0.5, 0.5]]
    service.close()
    service.close()
