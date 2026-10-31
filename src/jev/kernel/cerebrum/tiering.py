"""AR018 GTB：证据接地的双脑级联。

小模型先答；只有当答案为短抽取式、且能在“问题锚点的局部文档窗口”中找到证据时
才采用；否则升级大模型。验证完全确定性，不调用模型或网络。
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

from jev.errors import JevProtocolError, JevUnavailableError
from jev.kernel.cerebrum.llama_server import LlamaServerClient

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"[A-Za-z]{1,12}[-_]\d{2,}")
_NUMBER_RE = re.compile(r"\d{4,}")
_ENGLISH_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_QUOTED_RE = re.compile(
    r"[\"'“”‘’「」『』《》]([^\"'“”‘’「」『』《》\n]{2,80})[\"'“”‘’「」『』《》]"
)

_ENGLISH_STOPWORDS = frozenset(
    {
        "what",
        "which",
        "where",
        "when",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "does",
        "did",
        "was",
        "were",
        "is",
        "are",
        "please",
        "only",
        "output",
        "answer",
        "question",
        "document",
        "report",
    }
)

_CJK_STOPWORDS = (
    "请问",
    "多少",
    "什么",
    "哪个",
    "哪些",
    "如何",
    "怎么",
    "是否",
    "为什",
    "问题",
    "只输",
    "输出",
    "数字",
)


@dataclasses.dataclass(frozen=True)
class TierConfig:
    """级联策略配置；默认关闭。"""

    enabled: bool = False
    ground_window: int = 600
    max_answer_chars: int = 64
    small_model: Path | None = None
    small_gpu_layers: int = 0
    prewarm_large: bool = True

    def __post_init__(self) -> None:
        if self.ground_window <= 0:
            raise ValueError(f"tier_ground_window 必须 >0: {self.ground_window}")
        if self.max_answer_chars <= 0:
            raise ValueError(
                f"tier_max_answer_chars 必须 >0: {self.max_answer_chars}"
            )
        if self.small_gpu_layers < 0:
            raise ValueError(
                f"small_gpu_layers 必须 >=0: {self.small_gpu_layers}"
            )


@dataclasses.dataclass(frozen=True)
class GroundingResult:
    accepted: bool
    reason: str
    anchor: str | None = None


def _flatten(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def split_question_document(text: str) -> tuple[str, str]:
    """把最后一条 user 内容拆成 (document, question)。

    优先识别显式 `问题：` 标记，其次取最后一个非空行作为问题。
    文档为空或问题与文档相同则返回空文档。
    """
    marker = text.rfind("问题：")
    if marker >= 0:
        document = text[:marker].rstrip()
        question = text[marker + len("问题：") :].strip()
        return (document, question) if document and question else ("", "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return "", ""
    return "\n".join(lines[:-1]), lines[-1]


def extract_anchors(question: str) -> list[str]:
    """从问题提取可定位锚点；不依赖数据集词表。"""
    anchors: list[str] = []
    anchors.extend(_IDENTIFIER_RE.findall(question))
    anchors.extend(_NUMBER_RE.findall(question))
    anchors.extend(match.group(1).strip() for match in _QUOTED_RE.finditer(question))
    for word in _ENGLISH_RE.findall(question):
        if word.lower() not in _ENGLISH_STOPWORDS:
            anchors.append(word)
    # 中文问题：保留引号外的高信息片段（≥2 字，去掉常见疑问词）。
    if not anchors:
        cjk = re.sub(r"[^\u4e00-\u9fff]", " ", question)
        for token in cjk.split():
            candidate = token
            for stop in _CJK_STOPWORDS:
                candidate = candidate.replace(stop, " ")
            for piece in candidate.split():
                if 2 <= len(piece) <= 12:
                    anchors.append(piece)
    unique: list[str] = []
    seen: set[str] = set()
    for anchor in sorted(anchors, key=len, reverse=True):
        key = anchor.casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append(anchor)
    return unique[:12]


def normalize_text(text: str) -> str:
    """去空白/标点/大小写，用于稳健子串比较。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace()
        and not unicodedata.category(char).startswith(("P", "Z", "C"))
    )


def _positions(document: str, anchor: str, limit: int = 32) -> list[int]:
    positions: list[int] = []
    start = 0
    while len(positions) < limit:
        found = document.find(anchor, start)
        if found < 0:
            break
        positions.append(found)
        start = found + max(1, len(anchor))
    return positions


def grounded_verify(
    messages: list[dict[str, Any]],
    answer: str,
    *,
    window: int = 600,
    max_answer_chars: int = 64,
) -> GroundingResult:
    """判断短答案是否被问题锚点邻域的文档证据支持。"""
    raw_answer = answer.strip()
    if not raw_answer:
        return GroundingResult(False, "empty_answer")
    if len(raw_answer) > max_answer_chars:
        return GroundingResult(False, "answer_too_long")

    text = _flatten(messages)
    document, question = split_question_document(text)
    if not document or not question:
        return GroundingResult(False, "no_document_or_question")

    anchors = extract_anchors(question)
    if not anchors:
        return GroundingResult(False, "no_anchor")

    normalized_answer = normalize_text(raw_answer)
    if not normalized_answer:
        return GroundingResult(False, "empty_normalized_answer")

    has_blocks = "\n\n" in document
    for anchor in anchors:
        for position in _positions(document, anchor):
            if has_blocks:
                left = document.rfind("\n\n", 0, position)
                right = document.find("\n\n", position)
                left = 0 if left < 0 else left + 2
                right = len(document) if right < 0 else right
                block = document[left:right]
                offset = position - left
                start = max(0, offset - window)
                end = min(len(block), offset + len(anchor) + window)
                local = block[start:end]
            else:
                start = max(0, position - window)
                end = min(len(document), position + len(anchor) + window)
                local = document[start:end]
            if raw_answer in local or normalized_answer in normalize_text(local):
                return GroundingResult(True, "grounded", anchor)
    return GroundingResult(False, "answer_not_in_anchor_window")


def _content_of(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content.strip() if isinstance(content, str) else ""


class TieredBrain:
    """full 路由的双脑级联：小模型首答 + 接地验证 + 大模型升级。"""

    def __init__(
        self,
        large: LlamaServerClient,
        small: LlamaServerClient,
        config: TierConfig,
    ) -> None:
        self._large = large
        self._small = small
        self._config = config
        self._warmed: set[str] = set()
        self._seen: dict[str, int] = {}
        self._warm_lock = threading.Lock()

    @property
    def config(self) -> TierConfig:
        return self._config

    @staticmethod
    def _prefix_key(messages: list[dict[str, Any]]) -> str:
        text = ""
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(
                message.get("content"), str
            ):
                text = message["content"]
                break
        document, _question = split_question_document(text)
        payload = document or text
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _prewarm_large(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout_s: float | None,
    ) -> None:
        """同一文档第二次出现时，用 1 token 预热大模型 prefix KV。

        这样后续升级请求可复用文档前缀，避免首次升级付出完整 prefill；
        独立文档（每问不同文档）不会触发额外预热。
        """
        if not self._config.prewarm_large:
            return
        key = self._prefix_key(messages)
        with self._warm_lock:
            seen = self._seen.get(key, 0) + 1
            self._seen[key] = seen
            if seen < 2 or key in self._warmed:
                return
        warm_timeout = 30.0 if timeout_s is None else min(30.0, timeout_s)
        try:
            self._large.chat(
                messages,
                max_tokens=1,
                temperature=0.0,
                stop=None,
                timeout_s=warm_timeout,
            )
        except (JevUnavailableError, JevProtocolError) as exc:
            logger.info("大模型前缀预热失败（不影响级联）: %s", exc)
        with self._warm_lock:
            if len(self._warmed) >= 64:
                self._warmed.clear()
            self._warmed.add(key)

    def chat_full(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
        stop: list[str] | None,
        timeout_s: float | None,
    ) -> tuple[dict[str, Any], str, str]:
        """返回 (response, tier, reason)。失败时由大模型兜底。"""
        if not self._config.enabled:
            response = self._large.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
            )
            return response, "large", "disabled"

        self._prewarm_large(messages, timeout_s=timeout_s)
        try:
            response = self._small.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
            )
        except (JevUnavailableError, JevProtocolError) as exc:
            logger.warning("小模型调用失败，升级大模型: %s", exc)
            large_response = self._large.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                timeout_s=timeout_s,
            )
            return large_response, "small-error->large", type(exc).__name__

        verdict = grounded_verify(
            messages,
            _content_of(response),
            window=self._config.ground_window,
            max_answer_chars=self._config.max_answer_chars,
        )
        if verdict.accepted:
            return response, "small", verdict.reason
        logger.info("小模型答案未接地（%s），升级大模型", verdict.reason)
        large_response = self._large.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            timeout_s=timeout_s,
        )
        return large_response, "small->large", verdict.reason


__all__ = [
    "GroundingResult",
    "TierConfig",
    "TieredBrain",
    "extract_anchors",
    "grounded_verify",
    "normalize_text",
    "split_question_document",
]
