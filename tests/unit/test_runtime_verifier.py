"""AR003 T004：大脑验证器（FR1-3/FR1-4）。

纯逻辑测试：ChatFn 为确定性桩（非后端 mock），只验证解析/异常/计量契约。
真实 llama-server 集成用例在 tests/integration。
"""

import pytest

from jev.errors import JevTimeoutError, JevUnavailableError
from jev.kernel.runtime.verifier import BrainVerifier, VerifyResult, parse_verdict


class FakeChat:
    def __init__(self, content: str, *, usage_tokens: int = 7, raises: Exception | None = None):
        self.content = content
        self.usage_tokens = usage_tokens
        self.raises = raises
        self.calls: list[dict] = []

    def chat(self, messages, *, max_tokens=16, temperature=0.0, stop=None,
             timeout_s=None, extra_body=None):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout_s": timeout_s,
                "extra_body": extra_body,
            }
        )
        if self.raises is not None:
            raise self.raises
        return {
            "choices": [{"message": {"role": "assistant", "content": self.content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": self.usage_tokens,
                      "total_tokens": 3 + self.usage_tokens},
        }


class TestParseVerdict:
    def test_accept(self):
        assert parse_verdict("ACCEPT\n答案完整")[0] == "accept"

    def test_accept_lowercase_and_lead_space(self):
        assert parse_verdict("  accept because ok")[0] == "accept"

    def test_reject(self):
        verdict, reason = parse_verdict("REJECT\n缺少关键信息")
        assert verdict == "reject"
        assert "缺少关键信息" in reason

    def test_invalid_text_rejects(self):
        verdict, reason = parse_verdict("看起来还行")
        assert verdict == "reject"
        assert "无法解析" in reason

    def test_empty_rejects(self):
        assert parse_verdict("")[0] == "reject"

    def test_accept_disguised_in_sentence_rejects(self):
        # 必须是首行首个词，避免 "do not ACCEPT" 之类误判
        assert parse_verdict("I cannot ACCEPT this")[0] == "reject"


class TestBrainVerifier:
    def test_accept_result(self):
        fake = FakeChat("ACCEPT\n完整且切题")
        result = BrainVerifier(fake).verify("问题", "草稿", {"intent": "complex"})
        assert isinstance(result, VerifyResult)
        assert result.verdict == "accept"
        assert result.unavailable is False
        assert result.tokens == 7

    def test_single_call_with_deterministic_sampling(self):
        fake = FakeChat("REJECT\n不完整")
        BrainVerifier(fake).verify("问题", "草稿", {"intent": "complex"})
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["temperature"] == 0.0
        assert call["max_tokens"] == 16

    def test_prompt_contains_request_draft_and_applied(self):
        fake = FakeChat("ACCEPT\nok")
        BrainVerifier(fake).verify("用户请求X", "草稿Y", {"intent": "decision", "safety": "safe"})
        content = fake.calls[0]["messages"][-1]["content"]
        assert "用户请求X" in content
        assert "草稿Y" in content
        assert "intent=decision" in content
        assert "safety=safe" in content

    def test_timeout_maps_to_reject_unavailable(self):
        fake = FakeChat("", raises=JevTimeoutError("timed out"))
        result = BrainVerifier(fake).verify("问题", "草稿", {})
        assert result.verdict == "reject"
        assert result.unavailable is True
        assert result.tokens == 0

    def test_unavailable_maps_to_reject(self):
        fake = FakeChat("", raises=JevUnavailableError("boom"))
        result = BrainVerifier(fake).verify("问题", "草稿", {})
        assert result.verdict == "reject"
        assert result.unavailable is True

    def test_timeout_override_passed(self):
        fake = FakeChat("ACCEPT\nok")
        BrainVerifier(fake).verify("问题", "草稿", {}, timeout_s=0.25)
        assert fake.calls[0]["timeout_s"] == pytest.approx(0.25)

    def test_applied_none_values_rendered(self):
        fake = FakeChat("ACCEPT\nok")
        BrainVerifier(fake).verify("q", "d", {"intent": None, "complexity": 0.5})
        content = fake.calls[0]["messages"][-1]["content"]
        assert "intent=None" in content
        assert "complexity=0.5" in content

    def test_applied_truncated_for_budget(self):
        fake = FakeChat("ACCEPT\nok")
        applied = {f"point_{i}": "v" for i in range(20)}
        BrainVerifier(fake).verify("q", "d", applied)
        content = fake.calls[0]["messages"][-1]["content"]
        assert "point_0=v" in content
        # 最多 8 项，避免验证 prompt 膨胀
        assert content.count("point_") <= 8
