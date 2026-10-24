"""T002 资产下载器单元测试（UT-05 / EX-06 / EX-10）。

约束 C1：不 mock 后端/网络。HTTP 行为由本地真实服务器提供
（http.server.ThreadingHTTPServer，绑定 127.0.0.1 随机端口，serve 临时目录真实文件）。
服务器路由约定：
- /ok/<name>        正常文件，支持 Range（206）
- /no-range/<name>  正常文件，忽略 Range（恒 200 全量）
- /corrupt/<name>   恒定返回被破坏内容（sha256 必不匹配）
- /flaky-corrupt/   首个 GET 返回破坏内容，其后返回正确内容
- /fail/<name>      恒定 503
- /html/<name>      200 + HTML 错误页（镜像限流/失效时真实出现过，曾致假资产）
"""

import hashlib
import http.server
import io
import pathlib
import socket
import threading
import zipfile

import pytest

from jev.errors import JevAssetError
from jev.fetch.downloader import AssetFetcher
from jev.fetch.manifest import AssetSpec

CONTENT = bytes(range(256)) * 32
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()
PART_SIZE = len(CONTENT) // 2


class _FileHandler(http.server.BaseHTTPRequestHandler):
    """真实文件服务 Handler：全量/Range/错误注入路由，并记录每个请求。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _record(self):
        self.server.request_log.append((self.command, self.path, self.headers.get("Range")))

    def _route(self):
        server = self.server
        name = self.path.rsplit("/", 1)[-1]
        if self.path.startswith("/fail/"):
            return 503, b"upstream unavailable"
        if self.path.startswith("/html/"):
            return 200, b"<!doctype html><html><head><title>error</title></head></html>"
        if self.path.startswith("/corrupt/"):
            return 200, (server.serve_dir / name).read_bytes() + b"CORRUPT"
        if self.path.startswith("/flaky-corrupt/"):
            # 仅统计 GET，避免实现方 HEAD 探测干扰"重试次数"断言
            if self.command == "GET":
                server.flaky_counts[self.path] = server.flaky_counts.get(self.path, 0) + 1
            data = (server.serve_dir / name).read_bytes()
            if server.flaky_counts.get(self.path, 0) == 1:
                return 200, data + b"CORRUPT"
            return 200, data
        if self.path.startswith("/ok/") or self.path.startswith("/no-range/"):
            return 200, (server.serve_dir / name).read_bytes()
        return 404, b"not found"

    def _send_full(self, status, data):
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record()
        status, data = self._route()
        range_header = self.headers.get("Range")
        if status == 200 and self.path.startswith("/ok/") and range_header:
            start = int(range_header.replace("bytes=", "").split("-", 1)[0])
            chunk = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self._send_full(status, data)

    def do_HEAD(self):
        self._record()
        status, data = self._route()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()


@pytest.fixture
def local_no_proxy(monkeypatch):
    """回环地址直连，屏蔽系统代理变量对本机真实 HTTP 的干扰（非 mock，仅环境配置）。"""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def file_server(tmp_path: pathlib.Path, local_no_proxy) -> http.server.ThreadingHTTPServer:
    serve_dir = tmp_path / "served"
    serve_dir.mkdir()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    server.serve_dir = serve_dir
    server.request_log = []
    server.flaky_counts = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _url(server: http.server.ThreadingHTTPServer, route: str) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}{route}"


def _part_path(dest: pathlib.Path) -> pathlib.Path:
    return dest.with_name(dest.name + ".part")


def _get_requests(server: http.server.ThreadingHTTPServer, path: str):
    return [e for e in server.request_log if e[0] == "GET" and e[1] == path]


def _spec(
    name: str,
    dest: str,
    url: str,
    mirrors: tuple[str, ...] = (),
    sha256: str = "",
    kind: str = "model",
) -> AssetSpec:
    return AssetSpec(
        name=name, kind=kind, dest=dest, url=url, mirrors=list(mirrors), sha256=sha256
    )


@pytest.fixture
def model_url(file_server: http.server.ThreadingHTTPServer) -> str:
    (file_server.serve_dir / "model.bin").write_bytes(CONTENT)
    return _url(file_server, "/ok/model.bin")


def test_fetch_downloads_file_to_dest_and_leaves_no_part(tmp_path, model_url):
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("m")

    assert dest == tmp_path / "models" / "m.bin"
    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_fetch_skips_in_place_matching_asset_without_any_http_request(
    tmp_path, file_server, model_url
):
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT)
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    result = fetcher.fetch("m")

    assert result == dest
    assert file_server.request_log == []
    assert dest.read_bytes() == CONTENT


def test_fetch_skips_in_place_asset_when_manifest_sha256_empty(tmp_path):
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    marker = b"no-checksum-content"
    dest.write_bytes(marker)
    spec = _spec("m", "models/m.bin", "http://127.0.0.1:1/never-contacted.bin", sha256="")
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    result = fetcher.fetch("m")

    assert result == dest
    assert dest.read_bytes() == marker


def test_fetch_skips_in_place_file_without_reverification_even_if_corrupted(
    tmp_path, file_server, model_url
):
    """srs AC：已就位资产再次 fetch 5s 内跳过——不重新校验、不重新下载。

    （篡改检测走 verify()，由 jev doctor 体检调用；大资产全量哈希会使跳过
    路径超时违约束。）
    """
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"stale corrupted content")
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    result = fetcher.fetch("m")

    assert result == dest
    assert file_server.request_log == []
    assert dest.read_bytes() == b"stale corrupted content"
    assert fetcher.verify("m") is False


def test_fetch_force_redownloads_even_when_in_place_asset_is_valid(
    tmp_path, file_server, model_url
):
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT)
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    fetcher.fetch("m", force=True)

    assert _get_requests(file_server, "/ok/model.bin")
    assert dest.read_bytes() == CONTENT


def test_fetch_falls_back_to_mirror_when_primary_source_fails(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    primary = _url(file_server, "/fail/m.bin")
    mirror = _url(file_server, "/ok/m.bin")
    spec = _spec("m", "models/m.bin", primary, mirrors=(mirror,), sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("m")

    paths = [e[1] for e in file_server.request_log]
    assert dest.read_bytes() == CONTENT
    assert paths.index("/fail/m.bin") < paths.index("/ok/m.bin")


def test_fetch_resumes_from_existing_part_file_via_range_request(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    url = _url(file_server, "/ok/m.bin")
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    _part_path(dest).write_bytes(CONTENT[:PART_SIZE])
    spec = _spec("m", "models/m.bin", url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    fetcher.fetch("m")

    ranges = [e[2] for e in _get_requests(file_server, "/ok/m.bin")]
    assert f"bytes={PART_SIZE}-" in ranges
    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_fetch_restarts_from_scratch_when_server_ignores_range(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    url = _url(file_server, "/no-range/m.bin")
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    _part_path(dest).write_bytes(CONTENT[:PART_SIZE])
    spec = _spec("m", "models/m.bin", url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    fetcher.fetch("m")

    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_fetch_retries_same_source_after_sha_mismatch_and_succeeds(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    url = _url(file_server, "/flaky-corrupt/m.bin")
    spec = _spec("m", "models/m.bin", url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("m")

    assert len(_get_requests(file_server, "/flaky-corrupt/m.bin")) == 2
    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_fetch_sha_mismatch_exhausts_retries_raises_and_cleans_part(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    url = _url(file_server, "/corrupt/m.bin")
    spec = _spec("m", "models/m.bin", url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)
    dest = tmp_path / "models" / "m.bin"

    with pytest.raises(JevAssetError):
        fetcher.fetch("m")

    # 首次下载 + 至多 2 次重试
    assert len(_get_requests(file_server, "/corrupt/m.bin")) <= 3
    assert not _part_path(dest).exists()
    assert not dest.exists()


def test_fetch_rejects_html_interstitial_and_falls_back_to_mirror_without_retry(
    tmp_path, file_server
):
    """镜像限流返回 200+HTML 错误页时：立即换源（同源重试无意义），不污染目标。"""
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    primary = _url(file_server, "/html/m.bin")
    mirror = _url(file_server, "/ok/m.bin")
    spec = _spec("m", "models/m.bin", primary, mirrors=(mirror,), sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("m")

    assert len(_get_requests(file_server, "/html/m.bin")) == 1  # HTML 页不重试同源
    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_fetch_without_sha_rejects_html_content_instead_of_placing_it(
    tmp_path, file_server
):
    """无 sha256 资产同样拒绝 HTML 错误页（防 200+HTML 假资产静默就位）。"""
    primary = _url(file_server, "/html/m.bin")
    spec = _spec("m", "models/m.bin", primary, sha256="")
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    with pytest.raises(JevAssetError) as excinfo:
        fetcher.fetch("m")

    assert "HTML" in str(excinfo.value)
    assert not (tmp_path / "models" / "m.bin").exists()


def test_fetch_all_sources_failed_raises_with_each_url_and_manual_guidance(tmp_path, file_server):
    (file_server.serve_dir / "m.bin").write_bytes(CONTENT)
    primary = _url(file_server, "/fail/primary.bin")
    mirror = _url(file_server, "/fail/mirror.bin")
    spec = _spec("m", "models/m.bin", primary, mirrors=(mirror,), sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    with pytest.raises(JevAssetError) as excinfo:
        fetcher.fetch("m")

    message = str(excinfo.value)
    assert primary in message
    assert mirror in message
    assert "503" in message
    assert "手动下载" in message


def test_fetch_connection_refused_raises_with_url_and_manual_guidance(tmp_path, local_no_proxy):
    # 取一个已释放的本地端口，制造真实"连接被拒绝"（无任何 mock）
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    url = f"http://127.0.0.1:{port}/m.bin"
    spec = _spec("m", "models/m.bin", url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    with pytest.raises(JevAssetError) as excinfo:
        fetcher.fetch("m")

    message = str(excinfo.value)
    assert url in message
    assert "手动下载" in message


def test_fetch_all_downloads_every_asset_in_manifest_order(tmp_path, file_server):
    (file_server.serve_dir / "a.bin").write_bytes(CONTENT)
    (file_server.serve_dir / "b.bin").write_bytes(b"BBBB")
    sha_b = hashlib.sha256(b"BBBB").hexdigest()
    spec_a = _spec("a", "models/a.bin", _url(file_server, "/ok/a.bin"), sha256=CONTENT_SHA256)
    spec_b = _spec("b", "models/b.bin", _url(file_server, "/ok/b.bin"), sha256=sha_b)
    fetcher = AssetFetcher(assets=[spec_a, spec_b], repo_root=tmp_path)

    paths = fetcher.fetch_all()

    assert paths == [tmp_path / "models" / "a.bin", tmp_path / "models" / "b.bin"]
    assert (tmp_path / "models" / "a.bin").read_bytes() == CONTENT
    assert (tmp_path / "models" / "b.bin").read_bytes() == b"BBBB"


def test_fetch_archive_extracts_zip_into_dest_directory(tmp_path, file_server):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bin/tool.exe", b"TOOL")
        archive.writestr("lib/data.txt", b"DATA")
    zip_bytes = buffer.getvalue()
    (file_server.serve_dir / "pack.zip").write_bytes(zip_bytes)
    spec = _spec(
        "pack",
        "vendor/pack",
        _url(file_server, "/ok/pack.zip"),
        sha256=hashlib.sha256(zip_bytes).hexdigest(),
        kind="archive",
    )
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("pack")

    assert dest == tmp_path / "vendor" / "pack"
    assert (dest / "bin" / "tool.exe").read_bytes() == b"TOOL"
    assert (dest / "lib" / "data.txt").read_bytes() == b"DATA"
    assert not _part_path(dest).exists()


def test_fetch_asset_without_sha256_downloads_without_verification(tmp_path, file_server):
    (file_server.serve_dir / "free.bin").write_bytes(CONTENT)
    spec = _spec("free", "models/free.bin", _url(file_server, "/ok/free.bin"), sha256="")
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    dest = fetcher.fetch("free")

    assert dest.read_bytes() == CONTENT
    assert not _part_path(dest).exists()


def test_verify_true_when_in_place_file_matches_sha256(tmp_path, model_url):
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(CONTENT)
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    assert fetcher.verify("m") is True


def test_verify_false_when_in_place_file_mismatches_sha256(tmp_path, model_url):
    dest = tmp_path / "models" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"wrong content")
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    assert fetcher.verify("m") is False


def test_verify_false_when_dest_file_missing(tmp_path, model_url):
    spec = _spec("m", "models/m.bin", model_url, sha256=CONTENT_SHA256)
    fetcher = AssetFetcher(assets=[spec], repo_root=tmp_path)

    assert fetcher.verify("m") is False
