"""运行时 profile（AR005 FR5-4）：实验选择器导出的配置覆盖层。

profile 是 `jev.toml` 的**子集覆盖**：读取后按节合并到主配置，未出现的键保持原值。
默认路径为空 = 不启用；加载失败按 `JevProtocolError` 处理（显式启用时 fail-fast）。
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from jev.errors import JevProtocolError

ALLOWED_SECTIONS = frozenset(
    {"portfolio", "runtime", "cache", "prefill", "cascade", "cerebrum"}
)


@dataclasses.dataclass(frozen=True)
class RuntimeProfile:
    name: str
    overlay: dict

    def get(self, section: str, key: str, default=None):
        return self.overlay.get(section, {}).get(key, default)


def load_profile(path: Path | str) -> RuntimeProfile:
    file = Path(path)
    if not file.is_file():
        raise JevProtocolError(f"profile 文件不存在: {file}")
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise JevProtocolError(f"profile 不是合法 TOML: {file}: {exc}") from exc
    unknown = set(data) - ALLOWED_SECTIONS
    if unknown:
        raise JevProtocolError(f"profile 含未知节: {sorted(unknown)}")
    name = str((data.get("portfolio") or {}).get("name", "custom"))
    return RuntimeProfile(name=name, overlay=data)


def merge_profile(config: dict, profile: RuntimeProfile) -> dict:
    """浅节合并：profile 中出现的键覆盖主配置，未出现保持。"""
    merged = {
        section: dict(values)
        for section, values in config.items()
        if isinstance(values, dict)
    }
    for section, values in profile.overlay.items():
        if not isinstance(values, dict):
            raise JevProtocolError(f"profile 节 {section} 必须是表")
        merged.setdefault(section, {}).update(values)
    return merged


def to_toml(name: str, overlay: dict) -> str:
    """导出简单两层的 TOML（仅标量/字符串/布尔）。"""
    lines = [
        "# 由 jev matrix 生成；人工可编辑，未知节会被拒绝。",
        "",
        "[portfolio]",
        f'name = "{name}"',
        "",
    ]
    for section, values in overlay.items():
        if section == "portfolio":
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (int, float)):
                rendered = str(value)
            elif isinstance(value, str):
                rendered = f'"{value}"'
            else:
                raise JevProtocolError(f"profile 值不可序列化: {section}.{key}")
            lines.append(f"{key} = {rendered}")
        lines.append("")
    return "\n".join(lines)


__all__ = ["ALLOWED_SECTIONS", "RuntimeProfile", "load_profile", "merge_profile", "to_toml"]
