"""大脑后端：llama-server 子进程生命周期管理与 OpenAI 兼容客户端。

LlamaServerManager 负责"拉起子进程 → 轮询 /health → 崩溃自动重启（指数退避）
→ 优雅退出无残留"；连续启动失败超 restart_max 触发大脑侧熔断（设计状态图 E，
抛 BrainStartupError，由上层级联引擎降级）。LlamaServerClient 对接
llama-server 的 OpenAI 兼容 /v1/chat/completions。真实 llama-server 推理
验证属 assets 集成测试。
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx

from jev.errors import BrainStartupError, JevProtocolError, JevTimeoutError, JevUnavailableError

logger = logging.getLogger(__name__)

_PROBE_INTERVAL_S = 0.5
_STDERR_TAIL_CHARS = 400


class LlamaServerManager:
    """llama-server 子进程管理器：start/ensure_running/stop 与上下文管理器。"""

    def __init__(
        self,
        llama_server_bin: Path,
        gguf_path: Path,
        host: str = "127.0.0.1",
        port: int = 8388,
        ctx_size: int = 4096,
        startup_probe: str = "/health",
        probe_timeout_s: float = 30.0,
        restart_max: int = 3,
        restart_backoff_s: Sequence[float] = (2.0, 4.0, 8.0),
        extra_args: Sequence[str] | None = None,
    ) -> None:
        self._bin = Path(llama_server_bin)
        self._gguf = Path(gguf_path)
        self._host = host
        self._port = port
        self._ctx_size = ctx_size
        self._startup_probe = startup_probe
        self._probe_timeout_s = probe_timeout_s
        self._restart_max = restart_max
        # tuple 固化，防外部序列被篡改；测试注入小退避加速重启链
        self._backoff = tuple(restart_backoff_s)
        self._extra_args = tuple(extra_args or ())
        self._proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def pid(self) -> int:
        if self._proc is None:
            raise JevProtocolError("llama-server 尚未启动，无 pid")
        return self._proc.pid

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """拉起子进程并阻塞至健康探测通过；已在运行则拒绝。"""
        if self.is_running():
            raise JevProtocolError(
                f"llama-server 已在运行（pid={self._proc.pid}），不可重复 start()"
            )
        self._close_dead_pipes()
        self._spawn_and_probe()

    def ensure_running(self) -> None:
        """幂等保活：进程已活着直接返回；退出则按指数退避自动重启。

        连续失败超 restart_max 次 → BrainStartupError（大脑侧熔断入口）。
        """
        if self.is_running():
            return
        self._close_dead_pipes()
        attempts = 0
        while True:
            attempts += 1
            try:
                self._spawn_and_probe()
                return
            except BrainStartupError as exc:
                if attempts >= self._restart_max:
                    raise BrainStartupError(
                        f"大脑连续 {attempts} 次启动失败（大脑侧熔断，应降级小脑直答）: {exc}"
                    ) from exc
                delay = self._backoff[min(attempts - 1, len(self._backoff) - 1)]
                logger.warning(
                    "llama-server 第 %d/%d 次启动失败，%.2fs 后重试: %s",
                    attempts,
                    self._restart_max,
                    delay,
                    exc,
                )
                time.sleep(delay)

    def stop(self, timeout_s: float = 10.0) -> None:
        """退出子进程；幂等，未启动也安全。

        Windows 下 terminate() 本就是 TerminateProcess（无优雅语义可失），
        故直接用 taskkill /T 整树终结——防 trampoline/.bat 等包装层遗留孤儿
        进程继续占用端口。
        """
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            logger.info("停止 llama-server pid=%s", proc.pid)
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                logger.warning("llama-server 退出超时，强制 kill pid=%s", proc.pid)
                proc.kill()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    logger.error("llama-server pid=%s 强杀后仍未退出", proc.pid)
        self._close_pipes(proc)
        self._proc = None

    def __enter__(self) -> "LlamaServerManager":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # 内部：spawn / 探测 / 诊断
    # ------------------------------------------------------------------

    def _build_cmd(self) -> list[str]:
        return [
            str(self._bin),
            "-m",
            str(self._gguf),
            "--host",
            self._host,
            "--port",
            str(self._port),
            "-c",
            str(self._ctx_size),
            *self._extra_args,
        ]

    def _spawn_and_probe(self) -> None:
        """拉起一次子进程并探测健康；成功则接管 self._proc，失败抛 BrainStartupError。"""
        cmd = self._build_cmd()
        # 新进程组隔离 Ctrl+C 信号传播，宿主退出语义由 stop() 显式管理
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        logger.info("拉起 llama-server: %s", cmd)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                env=self._spawn_env(),
            )
        except OSError as exc:
            raise BrainStartupError(f"llama-server 可执行文件无法启动（{cmd[0]}）: {exc}") from exc

        deadline = time.monotonic() + self._probe_timeout_s
        while time.monotonic() < deadline:
            # 子进程先于探测窗口退出 → 立即转入诊断，不空等超时
            if proc.poll() is not None:
                break
            if self._probe_health():
                self._proc = proc
                logger.info("llama-server 就绪 pid=%s url=%s", proc.pid, self.base_url)
                return
            time.sleep(_PROBE_INTERVAL_S)
        raise self._startup_failure(proc)

    def _spawn_env(self) -> dict[str, str]:
        """子进程环境：llama-server 预编译包不自带 cudart（bin/ 与 cudart/ 分离
        布局），将同级 cudart/ 目录前置注入 PATH（T015 归纳自集成测试经验）。"""
        env = os.environ.copy()
        cudart_dir = self._bin.parent.parent / "cudart"
        if cudart_dir.is_dir():
            env["PATH"] = str(cudart_dir) + os.pathsep + env.get("PATH", "")
        return env

    def _probe_health(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.base_url}{self._startup_probe}", timeout=1.0
            ) as resp:
                return 200 <= resp.status < 300
        except (OSError, urllib.error.URLError):
            return False

    def _startup_failure(self, proc: subprocess.Popen) -> BrainStartupError:
        """构造携带诊断信息的启动失败：退出码 + stderr 尾部；清理残留进程。"""
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
            self._close_pipes(proc)
            return BrainStartupError(
                f"llama-server 健康探测超时（>{self._probe_timeout_s}s）: "
                f"{self.base_url}{self._startup_probe} 未就绪，已清理进程 pid={proc.pid}"
            )
        try:
            _stdout, stderr = proc.communicate(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            stderr = None
        self._close_pipes(proc)
        tail = (stderr or b"").decode("utf-8", "replace").strip()[-_STDERR_TAIL_CHARS:]
        message = f"llama-server 启动失败 exit={proc.returncode}"
        if tail:
            message = f"{message} stderr 尾部: {tail}"
        return BrainStartupError(message)

    @staticmethod
    def _close_pipes(proc: subprocess.Popen) -> None:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _close_dead_pipes(self) -> None:
        """已被外部杀死/前次失败遗留的 Popen 关闭管道，避免句柄泄漏。"""
        old = self._proc
        if old is not None and old.poll() is not None:
            self._close_pipes(old)
            self._proc = None


class LlamaServerClient:
    """llama-server 的 OpenAI 兼容客户端（/v1/chat/completions + /health）。"""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 120.0,
        *,
        cache_prompt: bool | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s)
        self._cache_prompt = cache_prompt

    @property
    def base_url(self) -> str:
        return self._base_url

    def health(self) -> bool:
        """2xx → True；连接失�?→ 非 2xx → False（不抛，供探活轮询）。"""
        try:
            response = self._client.get(f"{self._base_url}/health")
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300

    def close(self) -> None:
        """释放 HTTP 连接池（幂等）。"""
        self._client.close()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/chat/completions，返回含 choices[0].message.content 与 usage 的 dict。

        timeout_s 为单次调用超时覆盖（AR002 FR2-4：由级联剩余 deadline 推导，
        避免客户端固定超时与服务端 deadline 竞态）。
        """
        payload: dict[str, Any] = {
            "model": "local",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop is not None:
            payload["stop"] = stop
        if self._cache_prompt is not None:
            payload["cache_prompt"] = self._cache_prompt
        if extra_body:
            payload.update(extra_body)
        request_kwargs: dict[str, Any] = {"json": payload}
        if timeout_s is not None:
            request_kwargs["timeout"] = max(0.001, timeout_s)
        try:
            response = self._client.post(
                f"{self._base_url}/v1/chat/completions", **request_kwargs
            )
        except httpx.TimeoutException as exc:
            raise JevTimeoutError(f"大脑服务超时（{self._base_url}）: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JevUnavailableError(f"大脑服务不可达（{self._base_url}）: {exc}") from exc

        if not 200 <= response.status_code < 300:
            raise JevProtocolError(
                f"大脑服务错误（{self._base_url}）: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise JevProtocolError(
                f"大脑响应不是合法 JSON: {response.text[:200]}"
            ) from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        message = (
            choices[0].get("message")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(message, dict) or "content" not in message or not isinstance(usage, dict):
            raise JevProtocolError(
                f"大脑响应缺少必需字段（choices[0].message.content / usage）: {str(data)[:200]}"
            )
        return data

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """POST /v1/chat/completions（stream=true）→ 逐 chunk 产出 OpenAI 流式 dict。

        消费方中途停止迭代即关闭上游连接（MID 早停断流口径）。
        timeout_s 语义同 chat（AR002 FR2-4）。
        """
        payload: dict[str, Any] = {
            "model": "local",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if stop is not None:
            payload["stop"] = stop
        if self._cache_prompt is not None:
            payload["cache_prompt"] = self._cache_prompt
        if extra_body:
            payload.update(extra_body)
        stream_kwargs: dict[str, Any] = {"json": payload}
        if timeout_s is not None:
            stream_kwargs["timeout"] = max(0.001, timeout_s)
        try:
            with self._client.stream(
                "POST", f"{self._base_url}/v1/chat/completions", **stream_kwargs
            ) as response:
                if not 200 <= response.status_code < 300:
                    body = response.read().decode("utf-8", "replace")
                    raise JevProtocolError(
                        f"大脑服务错误（{self._base_url}）: "
                        f"HTTP {response.status_code} {body[:200]}"
                    )
                for line in response.iter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except ValueError as exc:
                        raise JevProtocolError(
                            f"大脑流式响应不是合法 JSON: {data[:200]}"
                        ) from exc
        except httpx.TimeoutException as exc:
            raise JevTimeoutError(
                f"大脑服务超时（{self._base_url}）: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise JevUnavailableError(
                f"大脑服务不可达（{self._base_url}）: {exc}"
            ) from exc


def slot_server_args(
    *, parallel: int = 1, slot_prompt_similarity: float | None = None
) -> list[str]:
    """主大脑实例的槽复用启动参数（AR003 FR2-2）。

    parallel >= 1 -> ``--parallel N``；
    slot_prompt_similarity in (0,1] -> ``--slot-prompt-similarity S``（0/None 关闭）。
    """
    if isinstance(parallel, bool) or not isinstance(parallel, int) or parallel < 1:
        raise ValueError(f"parallel 须为 >=1 的整数: {parallel!r}")
    args = ["--parallel", str(parallel)]
    if slot_prompt_similarity is not None:
        value = float(slot_prompt_similarity)
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"slot_prompt_similarity 须在 [0,1]: {slot_prompt_similarity!r}"
            )
        if value > 0.0:
            args.extend(["--slot-prompt-similarity", str(value)])
    return args
