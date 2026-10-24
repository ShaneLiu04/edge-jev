"""资产 manifest 解析（configs/assets.toml → AssetSpec 列表）。

约束 C4：模型权重与二进制不入仓，全部由 jev fetch 按 manifest 管理。
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from jev.errors import JevAssetError

_REQUIRED_FIELDS = ("name", "kind", "dest", "url")
_VALID_KINDS = ("model", "archive", "git")


@dataclass(frozen=True)
class AssetSpec:
    """单个模型资产条目。

    kind: model=单文件就位；archive=zip 解压到 dest 目录；git=浅克隆仓库到 dest 目录。
    sha256: 为空字符串表示该资产暂不校验（下载成功即就位）。
    """

    name: str
    kind: str
    dest: str
    url: str
    mirrors: list[str] = field(default_factory=list)
    sha256: str = ""


def load_manifest(path: Path | str) -> list[AssetSpec]:
    """解析 assets.toml；文件缺失/TOML 非法/字段非法 → JevAssetError（含资产名可定位）。"""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise JevAssetError(f"资产 manifest 文件不存在: {manifest_path}")

    try:
        with manifest_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise JevAssetError(f"资产 manifest 解析失败（{manifest_path}）: {exc}") from exc

    specs: list[AssetSpec] = []
    for raw in data.get("asset", []):
        specs.append(_parse_entry(raw))
    return specs


def _parse_entry(raw: dict) -> AssetSpec:
    if not isinstance(raw, dict):
        raise JevAssetError(f"manifest 条目必须是表结构，实际: {type(raw).__name__}")

    name = raw.get("name", "<未命名资产>")
    for field_name in _REQUIRED_FIELDS:
        if field_name not in raw:
            raise JevAssetError(f"资产 {name} 缺少必选字段 {field_name}")

    kind = raw["kind"]
    if kind not in _VALID_KINDS:
        raise JevAssetError(f"资产 {name} 的 kind 非法: {kind!r}（合法值: {_VALID_KINDS}）")

    mirrors = raw.get("mirrors", [])
    if not isinstance(mirrors, list) or not all(isinstance(m, str) for m in mirrors):
        raise JevAssetError(f"资产 {name} 的 mirrors 必须是字符串列表")

    sha256 = raw.get("sha256", "")
    if not isinstance(sha256, str):
        raise JevAssetError(f"资产 {name} 的 sha256 必须是字符串")

    dest = raw["dest"]
    if not isinstance(dest, str) or dest.startswith(("/", "\\")):
        raise JevAssetError(f"资产 {name} 的 dest 必须是仓库内相对路径")

    return AssetSpec(
        name=str(raw["name"]),
        kind=kind,
        dest=dest,
        url=raw["url"],
        mirrors=list(mirrors),
        sha256=sha256,
    )
