"""unit 共享 fixture：微型「真实契约」Laya bundle。

约束 C1（禁 mock 后端）：单测不依赖 1.6GB 真实权重，但走**真实契约**——
微型 ONNX 图与真实 laya.onnx 同名同型五输入（input_ids/attention_mask int64、
marker_pos int64、marker_mask bool、qtype int64 → logits [B,K] + act_probs [B,2]），
tokenizer 用真 tokenizers 库 + 微型 WordPiece 词表（真库微型资产）。

toy 语义：logits[b,k] = marker_pos[b,k]（mask 位），被 mask 的槽位 -1e4——
后出现的选项 marker 位置更大 → logit 更大 → argmax 恒为最后一个选项，
答案确定性可断言；act_probs 恒 [0.9, 0.1]。
"""

import json

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers

from jev.kernel.cerebellum.laya_onnx import LayaOnnxBackend
from jev.kernel.cerebellum.laya_sequence import MASK_TOKEN

TINY_VOCAB = {
    "[PAD]": 0,
    "[UNK]": 1,
    "[CLS]": 2,
    "[SEP]": 3,
    MASK_TOKEN: 4,
    "hello": 5,
    "world": 6,
    "jev": 7,
    "deep": 8,
    "fast": 9,
    "off": 10,
    "low": 11,
    "mid": 12,
    "high": 13,
}

TINY_CONFIG = {
    "max_len": 64,
    "head_max_len": 24,
    "temperature": [1.0, 1.0, 1.0],
    "temperature_by_options": {},
}


def write_tiny_contract_model(path) -> None:
    """微型真实契约 ONNX：logits=Where(marker_mask, marker_pos, -1e4)；act=[0.9,0.1]。"""
    neg_const = helper.make_node(
        "Constant",
        [],
        ["neg_const"],
        value=numpy_helper.from_array(np.array(-1e4, dtype=np.float32), "neg"),
    )
    cast_pos = helper.make_node("Cast", ["marker_pos"], ["pos_f"], to=TensorProto.FLOAT)
    logits_node = helper.make_node("Where", ["marker_mask", "pos_f", "neg_const"], ["logits"])
    shape_q = helper.make_node("Shape", ["qtype"], ["qshape"])
    two_const = helper.make_node(
        "Constant",
        [],
        ["two_const"],
        value=numpy_helper.from_array(np.array([2], dtype=np.int64), "two"),
    )
    shape2 = helper.make_node("Concat", ["qshape", "two_const"], ["shape2"], axis=0)
    act_const = helper.make_node(
        "Constant",
        [],
        ["act_const"],
        value=numpy_helper.from_array(np.array([0.9, 0.1], dtype=np.float32), "act"),
    )
    act_node = helper.make_node("Expand", ["act_const", "shape2"], ["act_probs"])
    graph = helper.make_graph(
        [neg_const, cast_pos, logits_node, shape_q, two_const, shape2, act_const, act_node],
        "tiny_laya_contract",
        inputs=[
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "seq"]),
            helper.make_tensor_value_info("marker_pos", TensorProto.INT64, ["batch", "options"]),
            helper.make_tensor_value_info("marker_mask", TensorProto.BOOL, ["batch", "options"]),
            helper.make_tensor_value_info("qtype", TensorProto.INT64, ["batch"]),
        ],
        outputs=[
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", "options"]),
            helper.make_tensor_value_info("act_probs", TensorProto.FLOAT, ["batch", 2]),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 8
    onnx.save(model, str(path))


def write_tiny_tokenizer(path) -> None:
    """真 tokenizers 库微型 WordPiece 分词器（含 4 特殊 token + mask token 文本）。"""
    tokenizer = Tokenizer(models.WordPiece(vocab=TINY_VOCAB, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(path))


def write_config(path, config: dict | None = None) -> None:
    path.write_text(
        json.dumps(config if config is not None else TINY_CONFIG), encoding="utf-8"
    )


@pytest.fixture()
def model_path(tmp_path):
    path = tmp_path / "tiny_laya.onnx"
    write_tiny_contract_model(path)
    return path


@pytest.fixture()
def tokenizer_path(tmp_path):
    path = tmp_path / "tokenizer.json"
    write_tiny_tokenizer(path)
    return path


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "laya_config.json"
    write_config(path)
    return path


@pytest.fixture()
def make_backend(model_path, tokenizer_path, config_path):
    """LayaOnnxBackend 工厂（真实契约微型 bundle，kwargs 透传构造器）。"""

    def _make(**kwargs):
        return LayaOnnxBackend(
            model_path=model_path,
            tokenizer_path=tokenizer_path,
            config_path=config_path,
            **kwargs,
        )

    return _make
