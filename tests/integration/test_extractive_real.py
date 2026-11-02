"""AR020 真实 Laya + 无大脑：抽取快答命中时完全不依赖 llama-server。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jev.bench.dataset import grade
from jev.bench.numeric_reasoning import build_numeric_reasoning
from jev.bench.prefixset import build_shared_prefix
from jev.kernel.extract import ExtractiveConfig, GroundedExtractor
from jev.kernel.kernel import Kernel
from jev.kernel.runtime.config import RuntimeConfig
from jev.server.chat import create_chat_router

pytestmark = pytest.mark.assets

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "jev.toml"


def test_extractive_hit_with_real_laya_and_no_brain() -> None:
    if not (REPO / "models" / "laya" / "laya.onnx").is_file():
        pytest.skip("缺少真实 Laya 资产")
    question = build_shared_prefix(1)[0]
    kernel = Kernel.from_config(CONFIG)
    try:
        app = FastAPI()
        app.include_router(
            create_chat_router(
                kernel.cerebellum,
                None,
                kernel.registry,
                engine=kernel.engine,
                route_policy=lambda _pre: "full",
                runtime_config=RuntimeConfig(enabled=True, decision_post=False),
                extractor=GroundedExtractor(
                    ExtractiveConfig(enabled=True, min_document_chars=200)
                ),
            )
        )
        response = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": question.prompt}],
                "max_tokens": 32,
                "temperature": 0.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        assert grade(content, question.gold)
        assert body["usage"]["total_tokens"] == 0
        assert response.headers["x-jev-extract"].startswith("hit:")
        assert kernel.brain is not None
        assert not kernel.brain.is_running()

        strict_app = FastAPI()
        strict_app.include_router(
            create_chat_router(
                kernel.cerebellum,
                None,
                kernel.registry,
                engine=kernel.engine,
                route_policy=lambda _pre: "full",
                runtime_config=RuntimeConfig(enabled=True, decision_post=True),
                extractor=GroundedExtractor(
                    ExtractiveConfig(enabled=True, min_document_chars=200)
                ),
            )
        )
        strict = TestClient(strict_app).post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": question.prompt}],
                "max_tokens": 32,
                "temperature": 0.0,
            },
        )
        assert strict.status_code == 200
        strict_body = strict.json()
        assert grade(strict_body["choices"][0]["message"]["content"], question.gold)
        assert strict_body["usage"]["total_tokens"] == 0
        assert strict.headers["x-jev-extract"].startswith("hit:")
    finally:
        kernel.close()


def test_gqra_hit_with_real_laya_and_no_brain() -> None:
    if not (REPO / "models" / "laya" / "laya.onnx").is_file():
        pytest.skip("缺少真实 Laya 资产")
    question = build_numeric_reasoning(1)[0]
    kernel = Kernel.from_config(CONFIG)
    try:
        app = FastAPI()
        app.include_router(
            create_chat_router(
                kernel.cerebellum,
                None,
                kernel.registry,
                engine=kernel.engine,
                route_policy=lambda _pre: "full",
                runtime_config=RuntimeConfig(enabled=True, decision_post=False),
                extractor=GroundedExtractor(
                    ExtractiveConfig(
                        enabled=True,
                        min_document_chars=200,
                        quantitative_enabled=True,
                    )
                ),
            )
        )
        response = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": question.prompt}],
                "max_tokens": 32,
                "temperature": 0.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert grade(body["choices"][0]["message"]["content"], question.gold)
        assert body["usage"]["total_tokens"] == 0
        assert response.headers["x-jev-extract"] == "hit:numeric-sum"
    finally:
        kernel.close()
