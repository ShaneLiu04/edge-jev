"""T011 IT-1：真实 Laya bundle 端到端——/v1/systemone 三型正确性 + 短 state P50 预算。

AR001 返工修订后预算（design.md §4.5）：CPU EP P50≤600ms / P95≤1.5s。
语义锚点来自真实 bundle 烟测：非体育 state 的 is_sports 概率≈0.004（no 压倒）。
"""

import time

import pytest
from fastapi.testclient import TestClient

from jev.kernel.cerebellum.base import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
)
from jev.kernel.cerebellum.wire import encode_request
from jev.server.app import build_systemone_app

pytestmark = pytest.mark.assets

STATE = "用户希望在内网环境离线部署一个本地推理服务"
NON_SPORTS_STATE = "The user is asking about deploying a local inference service offline."

SHORT_STATES = [
    "用户想查询明天的天气",
    "帮我总结这段话的要点",
    "这个功能什么时候上线",
    "翻译这句话成英文",
    "重启服务需要什么权限",
]


def _mixed_questions() -> dict:
    return {
        "q_intent": ChoiceQuestion(
            choices={"decision": None, "generation": None},
            instructions="Is this request a structured decision task?",
        ),
        "q_reasoning": NoulQuestion(instructions="Does this need multi-step reasoning?"),
        "q_complexity": ScoreQuestion(
            criteria=["low", "medium", "high"], instructions="How complex is this request?"
        ),
    }


class TestSystemoneRealChain:
    def test_three_question_types_valid(self, real_kernel):
        client = TestClient(build_systemone_app(real_kernel))
        response = client.post(
            "/v1/systemone", json=encode_request("local", STATE, _mixed_questions())
        )
        assert response.status_code == 200
        body = response.json()

        choice = body["answers"]["q_intent"]
        assert choice["type"] == "choice"
        assert choice["choice"] in {"decision", "generation"}
        assert abs(sum(choice["probabilities"].values()) - 1.0) < 0.05

        noul = body["answers"]["q_reasoning"]
        assert noul["type"] == "noul"
        assert 0.0 <= noul["noul"] <= 1.0

        score = body["answers"]["q_complexity"]
        assert score["type"] == "score"
        assert 0.0 <= score["score"] <= 2.0
        assert set(score["legend"].values()) == {"low", "medium", "high"}
        assert abs(sum(score["probabilities"].values()) - 1.0) < 0.05

        assert body["usage"]["input_tokens"] > 0
        assert body["model"] == "laya_onnx"

    def test_non_sports_state_answers_no(self, real_kernel):
        """真实语义锚点（烟测实测）：非体育 state → is_sports 概率压倒性低。"""
        questions = {
            "is_sports": ChoiceQuestion(
                choices={"no": None, "yes": None},
                instructions="Is this question about sports?",
            )
        }
        client = TestClient(build_systemone_app(real_kernel))
        response = client.post(
            "/v1/systemone", json=encode_request("local", NON_SPORTS_STATE, questions)
        )
        assert response.status_code == 200
        probabilities = response.json()["answers"]["is_sports"]["probabilities"]
        assert probabilities["no"] > probabilities["yes"]

    def test_truncated_state_flagged(self, real_kernel):
        """超长 state 截断后响应携带 truncated=true（TypeSafe 契约）。"""
        long_state = " ".join(["deployment"] * 2000)
        questions = {
            "q": NoulQuestion(instructions="Does this need extra context?")
        }
        client = TestClient(build_systemone_app(real_kernel))
        response = client.post(
            "/v1/systemone", json=encode_request("local", long_state, questions)
        )
        assert response.status_code == 200
        assert response.json()["truncated"] is True


class TestShortStateP50Budget:
    def test_p50_under_600ms(self, real_kernel):
        """短 state 基准集：P50≤600ms、P95≤1.5s（AR001 返工修订预算，design §4.5）。"""
        questions = {
            "q_intent": ChoiceQuestion(
                choices={"decision": None, "generation": None},
                instructions="Is this request a structured decision task?",
            )
        }
        # 预热（首次前向含惰性初始化，不计入统计）
        real_kernel.warmup()
        for state in SHORT_STATES:
            real_kernel.system_one(state, questions)

        samples = []
        for state in SHORT_STATES * 3:
                start = time.perf_counter()
                real_kernel.system_one(state, questions)
                samples.append((time.perf_counter() - start) * 1000)
        samples.sort()
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        print(f"\nreal laya CPU: n={len(samples)} p50={p50:.0f}ms p95={p95:.0f}ms")
        assert p50 <= 600, f"P50 {p50:.0f}ms 超预算 600ms"
        assert p95 <= 1500, f"P95 {p95:.0f}ms 超预算 1.5s"
