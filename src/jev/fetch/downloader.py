"""资产下载器：HF→镜像回退、sha256 校验、断点续传、秒级跳过（design.md 流程图 C）。

双传输：
- 默认 requests（标准 env 代理）——单测与无代理环境路径
- NTLM 企业代理场景：curl.exe 子进程（`--proxy-ntlm` 是唯一能对 HTTPS
  CONNECT 隧道做 NTLM 认证的可用通道；requests-ntlm 只能处理 HTTP 响应层
  407 质询，无法作用于 urllib3 内部的隧道建立）
"""

import hashlib
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

import requests

from jev.errors import JevAssetError
from jev.fetch.manifest import AssetSpec

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1 << 20
_MAX_ATTEMPTS_PER_SOURCE = 3  # 首次 + 至多 2 次重试（sha 校验失败场景）
_DOWNLOAD_TIMEOUT_S = (10, 60)
_CURL_CONNECT_TIMEOUT_S = 15
_CURL_MAX_TIME_S = 0  # 不限总时长（大模型文件）
_CURL_RANGE_NOT_RESUMABLE = 33


class AssetFetcher:
    """按 manifest 管理仓库内模型资产：fetch 就位 / verify 校验 / 秒级跳过。"""

    def __init__(
        self,
        assets: list[AssetSpec],
        repo_root: Path | str,
        ntlm_proxy: bool = False,
        proxy_url: str = "",
    ) -> None:
        self._root = Path(repo_root)
        self._assets: dict[str, AssetSpec] = {}
        for spec in assets:
            if spec.name in self._assets:
                raise JevAssetError(f"manifest 中资产名重复: {spec.name}")
            self._assets[spec.name] = spec
        self._session = requests.Session()
        self._proxies: dict[str, str] | None = None
        self._curl_proxy = ""
        if ntlm_proxy and proxy_url:
            curl = shutil.which("curl")
            if curl is None:
                raise JevAssetError("NTLM 代理模式需要 curl 可执行文件（PATH 中未找到）")
            self._curl_proxy = proxy_url
        elif proxy_url:
            self._proxies = {"http": proxy_url, "https": proxy_url}

    # ------------------------------------------------------------------ public

    def fetch(self, name: str, force: bool = False) -> Path:
        """拉取单个资产并就位；已就位且校验通过时零网络请求快速返回。"""
        spec = self._require(name)
        dest = self._root / spec.dest
        if not force and self._in_place(spec, dest):
            logger.debug("资产 %s 已就位，跳过下载", name)
            return dest

        if spec.kind == "git":
            return self._fetch_git(spec, force)

        dest.parent.mkdir(parents=True, exist_ok=True)
        if spec.kind == "archive":
            return self._fetch_archive(spec, dest)

        part = dest.with_name(dest.name + ".part")
        self._download_with_retry(spec, part)
        dest.unlink(missing_ok=True)
        part.replace(dest)  # 原子重命名就位
        return dest

    def fetch_all(self, force: bool = False) -> list[Path]:
        """按 manifest 声明顺序拉取全部资产。"""
        return [self.fetch(spec.name, force=force) for spec in self._assets.values()]

    def verify(self, name: str) -> bool:
        """目标在位且（model 且 manifest 提供 sha256 时）校验匹配。"""
        spec = self._require(name)
        dest = self._root / spec.dest
        if not dest.exists():
            return False
        if spec.kind != "model" or not spec.sha256:
            return True
        return _sha256_file(dest) == spec.sha256

    # ------------------------------------------------------------------ internals

    def _require(self, name: str) -> AssetSpec:
        spec = self._assets.get(name)
        if spec is None:
            known = ", ".join(self._assets) or "(manifest 为空)"
            raise JevAssetError(f"未知资产: {name}（manifest 定义: {known}）")
        return spec

    def _in_place(self, spec: AssetSpec, dest: Path) -> bool:
        """dest 在位（文件或目录）即就位——跳过路径不重新校验（srs AC：<5s 秒级跳过，
        大资产全量哈希会超时；篡改检测走 verify()，由 doctor 体检调用）。"""
        return dest.exists()

    def _download_with_retry(self, spec: AssetSpec, part: Path) -> None:
        sources = [spec.url, *spec.mirrors]
        failures: list[str] = []
        for source in sources:
            success = False
            for _attempt in range(_MAX_ATTEMPTS_PER_SOURCE):
                try:
                    self._download(source, part)
                except requests.RequestException as exc:
                    failures.append(f"{source}: {exc}")
                    logger.warning("下载源失败 %s: %s", source, exc)
                    break  # 传输失败 → 换下一源
                if _looks_like_html(part):
                    # 镜像限流/失效常见 200+HTML 错误页（曾致假资产固化）：立即换源，重试同源无意义
                    failures.append(f"{source}: 返回 HTML 页面而非资产内容")
                    logger.warning("下载源 %s 返回 HTML 错误页，换下一源", source)
                    part.unlink(missing_ok=True)
                    break
                if not spec.sha256 or _sha256_file(part) == spec.sha256:
                    success = True
                    break
                failures.append(f"{source}: sha256 校验失败")
                logger.warning("sha256 校验失败，清理残片重试: %s", source)
                part.unlink(missing_ok=True)
            if success:
                return
        manual = "；".join(f"{s} → {self._root / spec.dest}" for s in sources)
        raise JevAssetError(
            f"资产 {spec.name} 全部下载源失败: {'; '.join(failures)}。"
            f"请检查网络/代理，或手动下载以下任一 URL 放置到目标路径后重试: {manual}"
        )

    def _download(self, url: str, part: Path) -> None:
        if self._curl_proxy:
            self._download_via_curl(url, part)
            return
        self._download_via_requests(url, part)

    def _download_via_curl(self, url: str, part: Path) -> None:
        """NTLM 代理通道：curl 子进程（HTTPS CONNECT 隧道 NTLM 的唯一可用实现）。"""
        resume_at = part.stat().st_size if part.exists() else 0
        command = [
            "curl",
            "-sS",
            "--show-error",
            "--location",
            "--fail",
            "--proxy-ntlm",
            "--proxy-user",
            ":",
            "--connect-timeout",
            str(_CURL_CONNECT_TIMEOUT_S),
            *([] if _CURL_MAX_TIME_S == 0 else ["--max-time", str(_CURL_MAX_TIME_S)]),
        ]
        if self._curl_proxy:
            command += ["--proxy", self._curl_proxy]
        if resume_at:
            command += ["--continue-at", "-"]
        command += ["--output", str(part), url]

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == _CURL_RANGE_NOT_RESUMABLE:
            # 服务器不支持 Range：清残片从头重下（与 requests 路径同语义）
            part.unlink(missing_ok=True)
            resume_at = 0
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or f"curl 退出码 {result.returncode}"
            raise requests.RequestException(f"{url}: {detail}")
        logger.debug("curl 下载完成: %s -> %s", url, part)

    def _download_via_requests(self, url: str, part: Path) -> None:
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        response = self._session.get(
            url,
            stream=True,
            headers=headers,
            timeout=_DOWNLOAD_TIMEOUT_S,
            proxies=self._proxies or requests.utils.get_environ_proxies(url),
        )
        if resume_from and response.status_code == 206:
            mode = "ab"
        else:
            # 服务器忽略 Range（200 全量）→ 从头重写
            response.raise_for_status()
            mode = "wb"
        with response, part.open(mode) as f:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                f.write(chunk)

    def _fetch_archive(self, spec: AssetSpec, dest: Path) -> Path:
        zip_part = dest.with_name(dest.name + ".zip.part")
        try:
            self._download_with_retry(spec, zip_part)
            staging = dest.with_name(dest.name + ".extracting")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            with zipfile.ZipFile(zip_part) as archive:
                archive.extractall(staging)
            shutil.rmtree(dest, ignore_errors=True)
            staging.replace(dest)  # 原子目录就位
        finally:
            zip_part.unlink(missing_ok=True)
            shutil.rmtree(dest.with_name(dest.name + ".extracting"), ignore_errors=True)
        return dest

    def _fetch_git(self, spec: AssetSpec, force: bool) -> Path:
        dest = self._root / spec.dest
        if force and dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "--depth", "1", spec.url, str(dest)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise JevAssetError(
                    f"资产 {spec.name} git 克隆失败（{spec.url}）: {result.stderr.strip()}"
                )
        return dest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


_HTML_PREFIXES = (b"<!doctype html", b"<html")


def _looks_like_html(path: Path) -> bool:
    """嗅探文件头识别 200+HTML 错误页（镜像限流/防盗链场景）；html 目标除外。"""
    if path.suffix.lower() in (".html", ".htm"):
        return False
    head = path.read_bytes()[:512].lstrip().lower()
    return head.startswith(_HTML_PREFIXES)
