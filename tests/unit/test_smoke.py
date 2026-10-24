"""工程脚手架冒烟测试（T001）。

验证内容：
- jev 包可导入且暴露版本号
- 内核/薄壳各子包存在（目录骨架与 design.md §4.4 一致）
- 配置文件（jev.toml / decision-points.toml / assets.toml）存在
"""

import tomllib


def test_package_importable_with_version():
    import jev

    assert isinstance(jev.__version__, str)
    assert jev.__version__.count(".") == 2


def test_kernel_and_shell_subpackages_exist():
    import jev.bench
    import jev.cli
    import jev.distill
    import jev.fetch
    import jev.kernel
    import jev.mcp_server
    import jev.sdk
    import jev.server

    assert jev.kernel is not None


def test_config_files_exist_and_parse(configs_dir):
    for name in ("jev.toml", "decision-points.toml", "assets.toml"):
        path = configs_dir / name
        assert path.is_file(), f"missing config: {path}"
        with path.open("rb") as f:
            data = tomllib.load(f)
        assert isinstance(data, dict)


def test_decision_points_declares_18_points(configs_dir):
    with (configs_dir / "decision-points.toml").open("rb") as f:
        data = tomllib.load(f)
    points = data["point"]
    assert len(points) == 18
    stages = {}
    for p in points:
        assert p["stage"] in ("PRE", "MID", "POST")
        assert p["mode"] in ("active", "shadow", "off")
        stages[p["stage"]] = stages.get(p["stage"], 0) + 1
    assert stages == {"PRE": 9, "MID": 4, "POST": 5}
