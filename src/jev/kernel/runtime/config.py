"""运行时配置聚合（AR003 design §4.3.1）。

单一对象贯穿 Kernel → server，全部默认关闭以保持 AR002 行为。
"""

from __future__ import annotations

import dataclasses

from jev.kernel.cache.semantic import CacheConfig
from jev.kernel.prefill.planner import PrefillConfig
from jev.kernel.runtime.bands import BandPolicy
from jev.kernel.runtime.prompt import PromptConfig


@dataclasses.dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool = False
    bands: BandPolicy = dataclasses.field(default_factory=BandPolicy)
    cache: CacheConfig = dataclasses.field(default_factory=CacheConfig)
    prefill: PrefillConfig = dataclasses.field(default_factory=PrefillConfig)
    prompt: PromptConfig = dataclasses.field(default_factory=PromptConfig)
    verifier_max_tokens: int = 16
    risk_control: bool = False
    risk_calibration: str = ""
    risk_alpha: float = 0.10
    allocator_enabled: bool = False
    allocator_budget_ratio: float = 0.6
    allocator_min_prompt_tokens: int = 512
    profile_name: str = ""

    def __post_init__(self) -> None:
        if self.verifier_max_tokens < 1:
            raise ValueError(f"verifier_max_tokens 须 >=1: {self.verifier_max_tokens}")
        if not 0.0 < self.risk_alpha < 1.0:
            raise ValueError(f"risk_alpha 须在 (0,1): {self.risk_alpha}")
        if not 0.0 < self.allocator_budget_ratio <= 1.0:
            raise ValueError(
                f"allocator_budget_ratio 须在 (0,1]: {self.allocator_budget_ratio}"
            )


__all__ = ["RuntimeConfig"]
