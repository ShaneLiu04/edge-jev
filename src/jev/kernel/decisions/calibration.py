"""校准产物加载（v1/v2）与路由配置（AR002 FR1-3）。

v1（AR001，T019 产物）：{"temperature": {"choice": T, "score": T, "noul": T},
                        "accepted": true}
v2（AR002）：
{
  "schema": 2,
  "accepted": true,
  "global": {"temperature": {"choice": 1.2, ...}},
  "points": {"intent": {"temperature": 1.31, "tau": 0.55, "margin": 0.18}},
  "routing": {"margin_tau": 0.18, "scan": [...], "selected": {...}}
}

加载语义（沿用 AR001 决策）：
- 文件缺失 → 返回空 Calibration（告警降级，不阻断启动）
- accepted != true → 拒绝加载（返回空 + 告警）
- JSON 非法 / 结构非法 → JevAssetError（显式错误，不静默）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jev.errors import JevAssetError, JevProtocolError
from jev.kernel.decisions.probes import PointCalibration

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Calibration:
    """校准集合：全局按题型温度 + 逐点覆盖 + 路由 margin。"""

    temperatures: dict[str, float] = field(default_factory=dict)
    points: dict[str, PointCalibration] = field(default_factory=dict)
    margin_tau: float = 0.0
    accepted: bool = False

    def for_point(self, point_id: str, question_type: str) -> PointCalibration:
        """逐点校准优先；缺失时回落到按题型温度。"""
        if point_id in self.points:
            return self.points[point_id]
        fallback = self.temperatures.get(question_type, 1.0)
        return PointCalibration(temperature=fallback)

    @property
    def is_empty(self) -> bool:
        return not self.temperatures and not self.points and self.margin_tau == 0.0


EMPTY = Calibration()


def load_calibration(path: str | Path | None) -> Calibration:
    """加载校准文件；缺失/拒绝发布 → 空校准（告警），非法 → JevAssetError。"""
    if path is None or str(path).strip() == "":
        return EMPTY
    file = Path(path)
    if not file.is_file():
        logger.warning("校准文件不存在，按未校准运行: %s", file)
        return EMPTY
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise JevAssetError(f"校准文件不合法（{file}）: {exc}") from exc
    if not isinstance(data, dict):
        raise JevAssetError(f"校准文件须为 JSON 对象（{file}）")
    if data.get("accepted") is not True:
        logger.warning("校准未通过留出集评估（accepted!=true），拒绝加载: %s", file)
        return EMPTY

    schema = data.get("schema", 1)
    if schema == 1:
        temperatures = _read_temperatures(data, file)
        return Calibration(temperatures=temperatures, accepted=True)
    if schema == 2:
        global_block = data.get("global") or {}
        temperatures = _read_temperatures(global_block, file)
        points_raw = data.get("points") or {}
        if not isinstance(points_raw, dict):
            raise JevAssetError(f"校准 points 须为对象（{file}）")
        points: dict[str, PointCalibration] = {}
        for point_id, block in points_raw.items():
            if not isinstance(block, dict):
                raise JevAssetError(f"校准点 {point_id} 须为对象（{file}）")
            try:
                calibration = PointCalibration(
                    temperature=float(block.get("temperature", 1.0)),
                    tau=_optional_float(block.get("tau")),
                    margin=_optional_float(block.get("margin")),
                ).validated(point_id)
            except (TypeError, ValueError, JevProtocolError) as exc:
                raise JevAssetError(f"校准点 {point_id} 非法（{file}）: {exc}") from exc
            points[point_id] = calibration
        routing = data.get("routing") or {}
        margin_tau = 0.0
        if isinstance(routing, dict) and routing.get("margin_tau") is not None:
            margin_tau = float(routing["margin_tau"])
            if not 0.0 <= margin_tau <= 1.0:
                raise JevAssetError(f"校准 routing.margin_tau 越界（{file}）: {margin_tau}")
        return Calibration(
            temperatures=temperatures, points=points, margin_tau=margin_tau, accepted=True
        )
    raise JevAssetError(f"未知校准 schema: {schema}（{file}）")


def _read_temperatures(block: dict, file: Path) -> dict[str, float]:
    raw = block.get("temperature") or {}
    if not isinstance(raw, dict):
        raise JevAssetError(f"校准 temperature 须为对象（{file}）")
    temperatures: dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise JevAssetError(f"校准温度非数值（{file}）: {key}={value!r}") from exc
        if number <= 0:
            raise JevAssetError(f"校准温度须为正数（{file}）: {key}={number}")
        temperatures[str(key)] = number
    return temperatures


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = ["Calibration", "EMPTY", "load_calibration"]
