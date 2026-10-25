"""AR003 T006：前缀指纹（FR2-1，纯逻辑）。"""

import pytest

from jev.kernel.cascade.prefix import prefix_fingerprint


def _msgs(*pairs):
    return [{"role": role, "content": content} for role, content in pairs]


class TestPrefixFingerprint:
    def test_returns_64_hex(self):
        value = prefix_fingerprint(_msgs(("user", "a"), ("assistant", "b"), ("user", "c")))
        assert len(value) == 64
        assert all(char in "0123456789abcdef" for char in value)

    def test_deterministic(self):
        messages = _msgs(("user", "a"), ("user", "b"))
        assert prefix_fingerprint(messages) == prefix_fingerprint(messages)

    def test_last_user_message_does_not_change_fingerprint(self):
        first = _msgs(("user", "历史"), ("assistant", "答"), ("user", "问题A"))
        second = _msgs(("user", "历史"), ("assistant", "答"), ("user", "问题B"))
        assert prefix_fingerprint(first) == prefix_fingerprint(second)

    def test_history_change_changes_fingerprint(self):
        first = _msgs(("user", "历史1"), ("user", "问题"))
        second = _msgs(("user", "历史2"), ("user", "问题"))
        assert prefix_fingerprint(first) != prefix_fingerprint(second)

    def test_order_matters(self):
        first = _msgs(("user", "a"), ("assistant", "b"), ("user", "q"))
        second = _msgs(("assistant", "b"), ("user", "a"), ("user", "q"))
        assert prefix_fingerprint(first) != prefix_fingerprint(second)

    def test_role_matters(self):
        first = _msgs(("user", "a"), ("assistant", "b"), ("user", "q"))
        second = _msgs(("assistant", "a"), ("user", "b"), ("user", "q"))
        assert prefix_fingerprint(first) != prefix_fingerprint(second)

    def test_truncation_ignores_far_tail_of_history(self):
        long_a = _msgs(("user", "x" * 100 + "TAIL_A"), ("user", "q"))
        long_b = _msgs(("user", "x" * 100 + "TAIL_B"), ("user", "q"))
        assert prefix_fingerprint(long_a, content_truncate=100) == prefix_fingerprint(
            long_b, content_truncate=100
        )
        assert prefix_fingerprint(long_a, content_truncate=512) != prefix_fingerprint(
            long_b, content_truncate=512
        )

    def test_single_message_has_empty_history(self):
        one = prefix_fingerprint(_msgs(("user", "唯一")))
        two = prefix_fingerprint([])
        assert one == two  # 历史为空 → 同一常量指纹

    def test_negative_truncate_rejected(self):
        with pytest.raises(ValueError):
            prefix_fingerprint(_msgs(("user", "a")), content_truncate=-1)
