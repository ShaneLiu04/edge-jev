"""Laya ONNX 小脑后端（onnxruntime 进程内，默认后端；真实 bundle 契约）。

真实 laya.onnx（receptron/laya-onnx，ModernBERT-large + decision head）契约：
输入 input_ids/attention_mask int64 [B,L]、marker_pos int64 [B,K]、
marker_mask bool [B,K]、qtype int64 [B]（choice=0/score=1/noul=2）；
输出 logits [B,K]（未校准，mask 槽位 -1e4）、act_probs [B,2]。

system_one 单次前向多问：每问构造一行序列（laya_sequence.build_sequence，
布局 [CLS] head [SEP] ⟨mask⟩选项… [SEP] state [SEP]），批内右填充、K 取
max(2, 最宽选项集)；logits[:k]/温度 softmax 出分布——温度按 (qtype, 选项数分桶)
查 laya_config，clamp [0.5, 5]，calibration 参数覆盖 qtype 基准。
答案形状对齐 TypeSafe：score=E[level] 浮点 + 索引 legend/probabilities；
noul=p[1]（true 概率）。

M1 简化说明（有意偏离参照、留待后续）：state 恒按字符串语义截断保前部
（参照对会话数组 state 保最新端）；act_probs 暂不外露（非 18 决策点域）。
"""

import json
import logging
import math
from pathlib import Path

import numpy as np
import onnxruntime
from tokenizers import Tokenizer

from jev.errors import JevAssetError, JevOverflowError, JevProtocolError
from jev.kernel.cerebellum.base import (
    Answer,
    CerebellumBackend,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
    choice_confidence,
    noul_confidence,
    score_confidence,
)
from jev.kernel.cerebellum.laya_sequence import (
    MASK_TOKEN,
    BuiltSequence,
    SpecialIds,
    build_sequence,
    qtype_index,
    render_options,
    temperature_for,
)

logger = logging.getLogger(__name__)

_REQUIRED_INPUTS = ("input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype")


class LayaOnnxBackend(CerebellumBackend):
    def __init__(
        self,
        model_path: Path | str,
        tokenizer_path: Path | str,
        config_path: Path | str,
        max_state_tokens: int | None = None,
        truncation: str = "truncate",
        calibration: dict[str, float] | None = None,
        providers: list[str] | None = None,
    ) -> None:
        self._model_path = Path(model_path)
        if not self._model_path.is_file():
            raise JevAssetError(f"小脑模型文件不存在: {self._model_path}")
        self._tokenizer_path = Path(tokenizer_path)
        if not self._tokenizer_path.is_file():
            raise JevAssetError(f"tokenizer 文件不存在: {self._tokenizer_path}")
        self._config_path = Path(config_path)
        if not self._config_path.is_file():
            raise JevAssetError(f"laya 配置文件不存在: {self._config_path}")

        self._tokenizer = Tokenizer.from_file(str(self._tokenizer_path))
        self._config: dict = json.loads(self._config_path.read_text(encoding="utf-8"))
        self._max_len = int(self._config.get("max_len", 512))
        self._head_max_len = int(self._config.get("head_max_len", 192))
        self._max_state_tokens = (
            int(max_state_tokens) if max_state_tokens is not None else self._max_len
        )
        if truncation not in ("truncate", "refuse"):
            raise JevProtocolError(f"truncation 非法: {truncation!r}")
        self._truncation = truncation
        self._calibration = dict(calibration or {})
        for qtype_name, value in self._calibration.items():
            if not _is_finite_positive(value):
                raise JevProtocolError(
                    f"校准温度须为正数，实际: {qtype_name}={value!r}"
                )

        self._session = onnxruntime.InferenceSession(
            str(self._model_path),
            providers=providers or ["CPUExecutionProvider"],
        )
        input_names = [item.name for item in self._session.get_inputs()]
        if tuple(input_names) != _REQUIRED_INPUTS:
            raise JevProtocolError(
                f"小脑模型输入契约不符（期望 {_REQUIRED_INPUTS}，实际: {input_names}）"
            )
        self._ids = self._load_special_ids()

    def name(self) -> str:
        return "laya_onnx"

    def warmup(self) -> None:
        """一次真实前向（单问 noul），暴露加载期问题。"""
        self.system_one(
            "warmup",
            {"warmup": NoulQuestion(instructions="warmup probe")},
        )

    # ------------------------------------------------------------------
    # system_one：单次前向多问
    # ------------------------------------------------------------------

    def system_one(self, state: str, questions: dict[str, Question]) -> SystemOneResponse:
        if not questions:
            raise JevProtocolError("questions 至少包含 1 个问题")

        # state 预算（token 数）：超限 refuse/truncate（截断保前部，字符串语义）
        state_ids = self._encode(state)
        truncated = False
        if len(state_ids) > self._max_state_tokens:
            if self._truncation == "refuse":
                # 专用子类：端点据此映射 HTTP 422（其余协议错为 400）
                raise JevOverflowError(
                    f"state 超长（{len(state_ids)} > {self._max_state_tokens} tokens），"
                    "truncation=refuse 拒绝处理"
                )
            state_ids = state_ids[: self._max_state_tokens]
            truncated = True

        built: dict[str, BuiltSequence] = {}
        for question_id, question in questions.items():
            options = render_options(question)
            sequence = build_sequence(
                self._encode,
                self._ids,
                state,
                question,
                self._max_len,
                self._head_max_len,
                state_ids=state_ids,
            )
            if len(sequence.markers) != len(options):
                raise JevProtocolError(
                    f"questions.{question_id}: 选项无法在 max_len={self._max_len} 内"
                    f"完整布位（{len(sequence.markers)}/{len(options)} 个 marker）"
                )
            built[question_id] = sequence
            truncated = truncated or sequence.state_truncated

        logits = self._run_batch(list(built.values()))
        answers = {}
        for row, question_id in enumerate(built):
            sequence = built[question_id]
            answers[question_id] = self._answer(
                questions[question_id], logits[row], len(sequence.markers)
            )
        return SystemOneResponse(
            answers=answers,
            input_tokens=sum(len(sequence.ids) for sequence in built.values()),
            truncated=truncated,
        )

    # ------------------------------------------------------------------ internals

    def _encode(self, text: str) -> list[int]:
        return self._tokenizer.encode(text, add_special_tokens=False).ids

    def _load_special_ids(self) -> SpecialIds:
        def _id(token: str) -> int:
            value = self._tokenizer.token_to_id(token)
            if value is None:
                raise JevAssetError(f"tokenizer 缺少特殊 token: {token!r}")
            return value

        return SpecialIds(
            cls=_id("[CLS]"),
            sep=_id("[SEP]"),
            mask=_id(MASK_TOKEN),
            pad=_id("[PAD]"),
            mask_token=MASK_TOKEN,
        )

    def _run_batch(self, sequences: list[BuiltSequence]) -> np.ndarray:
        """批内右填充；K=max(2, 最宽选项集)（act 头需 ≥2 槽，单选项问 mask 补位）。"""
        batch = len(sequences)
        length = max(len(sequence.ids) for sequence in sequences)
        width = max(2, max(len(sequence.markers) for sequence in sequences))

        input_ids = np.full((batch, length), self._ids.pad, dtype=np.int64)
        attention = np.zeros((batch, length), dtype=np.int64)
        marker_pos = np.zeros((batch, width), dtype=np.int64)
        marker_mask = np.zeros((batch, width), dtype=bool)
        qtype = np.zeros(batch, dtype=np.int64)
        for row, sequence in enumerate(sequences):
            ids = sequence.ids
            input_ids[row, : len(ids)] = ids
            attention[row, : len(ids)] = 1
            marker_pos[row, : len(sequence.markers)] = sequence.markers
            marker_mask[row, : len(sequence.markers)] = True
        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention,
            "marker_pos": marker_pos,
            "marker_mask": marker_mask,
            "qtype": qtype,
        }
        logits = self._session.run(["logits"], feeds)[0]
        return np.asarray(logits, dtype=np.float64)

    def _answer(self, question: Question, row: np.ndarray, k: int) -> Answer:
        options = render_options(question)
        if k != len(options):
            raise JevProtocolError(f"marker 数与选项数不符: {k} != {len(options)}")
        question_type = _type_name(question)
        temperature = self._temperature(question_type, k)
        probs = _softmax(np.asarray(row[:k], dtype=np.float64) / temperature)

        if isinstance(question, ChoiceQuestion):
            labels = list(question.choices)
            probabilities = {
                label: float(p) for label, p in zip(labels, probs, strict=True)
            }
            best = max(probabilities, key=probabilities.__getitem__)
            return ChoiceAnswer(
                choice=best,
                confidence=choice_confidence(probabilities),
                probabilities=probabilities,
            )
        if isinstance(question, NoulQuestion):
            p_true = float(probs[1])  # noul 选项序恒 [false, true]
            return NoulAnswer(noul=p_true, confidence=noul_confidence(p_true))
        if isinstance(question, ScoreQuestion):
            legend = {str(index): level for index, level in enumerate(question.criteria)}
            probabilities = {
                str(index): float(p) for index, p in enumerate(probs)
            }
            expected_level = sum(index * p for index, p in enumerate(probs))
            return ScoreAnswer(
                score=float(expected_level),
                confidence=score_confidence(probabilities),
                legend=legend,
                probabilities=probabilities,
            )
        raise JevProtocolError(f"未知问题类型: {type(question).__name__}")

    def _temperature(self, question_type: str, k: int) -> float:
        """calibration 参数覆盖 qtype 基准温度（分桶表仍优先）；构造期已校验。"""
        config = dict(self._config)
        override = self._calibration.get(question_type)
        if override is not None:
            base = list(config.get("temperature") or [])
            index = qtype_index(question_type)
            base = list(base) + [1.0] * (index + 1 - len(base))
            base[index] = override
            config["temperature"] = base
        return temperature_for(config, question_type, k)


def _type_name(question: Question) -> str:
    if isinstance(question, ChoiceQuestion):
        return "choice"
    if isinstance(question, ScoreQuestion):
        return "score"
    if isinstance(question, NoulQuestion):
        return "noul"
    raise JevProtocolError(f"未知问题类型: {type(question).__name__}")


def _is_finite_positive(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()
