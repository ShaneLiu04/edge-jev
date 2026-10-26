"""jev CLI 主入口与 fetch/serve/mcp/distill/bench/doctor 子命令（T002/T015/T017/T019/T021）。"""

import asyncio
import logging
import tomllib
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

import jev
from jev.cli.doctor import render_checks, run_doctor
from jev.errors import BrainStartupError, JevAssetError, JevError
from jev.fetch.downloader import AssetFetcher
from jev.fetch.manifest import load_manifest
from jev.kernel.kernel import Kernel

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Jev（捷思）— 端侧快慢双系统 AI 运行时",
    no_args_is_help=True,
)


def repo_root() -> Path:
    """仓库根（src/jev/cli/main.py 向上 4 级）；editable 安装下即仓库本体。"""
    return Path(__file__).resolve().parents[3]


def _load_fetcher() -> AssetFetcher:
    root = repo_root()
    specs = load_manifest(root / "configs" / "assets.toml")
    fetch_cfg: dict = {}
    config_path = root / "configs" / "jev.toml"
    if config_path.is_file():
        with config_path.open("rb") as f:
            fetch_cfg = tomllib.load(f).get("fetch", {})
    return AssetFetcher(
        assets=specs,
        repo_root=root,
        ntlm_proxy=fetch_cfg.get("proxy_ntlm", False),
        proxy_url=fetch_cfg.get("proxy_url", ""),
    )


@app.command()
def fetch(
    asset: Annotated[
        str | None,
        typer.Argument(help="资产名（configs/assets.toml 中定义，如 laya-onnx）"),
    ] = None,
    all: Annotated[bool, typer.Option("--all", help="拉取全部资产")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="忽略已就位资产，强制重新下载")
    ] = False,
) -> None:
    """拉取模型资产（sha256 校验 / 断点续传 / 镜像回退 / 已就位秒级跳过）。"""
    if asset is None and not all:
        typer.echo("错误: 请指定资产名或使用 --all", err=True)
        raise typer.Exit(2)

    fetcher = _load_fetcher()
    try:
        if all:
            paths = fetcher.fetch_all(force=force)
        else:
            assert asset is not None
            paths = [fetcher.fetch(asset, force=force)]
    except JevAssetError as exc:
        logger.error("fetch 失败: %s", exc)
        typer.echo(f"错误: {exc}", err=True)
        raise typer.Exit(1) from exc

    for path in paths:
        typer.echo(f"就位: {path}")


@app.command()
def serve(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="配置文件路径（默认 configs/jev.toml）"),
    ] = None,
    host: Annotated[str | None, typer.Option(help="覆盖监听地址")] = None,
    port: Annotated[int | None, typer.Option(help="覆盖监听端口")] = None,
    no_brain: Annotated[
        bool, typer.Option("--no-brain", help="不拉起大脑，仅小脑决策服务")
    ] = False,
) -> None:
    """启动 Jev 服务（/v1/systemone + /v1/chat/completions + /health）。

    按 [cerebrum] 按需拉起 llama-server（就绪探针通过后 brain=true）；
    大脑启动失败不阻断服务（chat 生成路径 503，小脑决策仍可用）。
    """
    config_path = Path(config) if config is not None else repo_root() / "configs" / "jev.toml"
    try:
        kernel = Kernel.from_config(config_path)
    except JevAssetError as exc:
        typer.echo(f"错误: {exc}", err=True)
        typer.echo("提示: 先运行 `jev fetch --all` 拉取模型资产", err=True)
        raise typer.Exit(1) from exc

    server_cfg: dict = {}
    if config_path.is_file():
        try:
            server_cfg = tomllib.loads(config_path.read_text(encoding="utf-8")).get(
                "server", {}
            )
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
            server_cfg = {}
    listen_host = host or str(server_cfg.get("host", "127.0.0.1"))
    listen_port = port or int(server_cfg.get("port", 8300))

    if kernel.brain is not None and not no_brain:
        try:
            kernel.brain.start()
        except BrainStartupError as exc:
            typer.echo(
                f"警告: 大脑启动失败（chat 生成路径将 503，小脑决策仍可用）: {exc}",
                err=True,
            )
            typer.echo("提示: 运行 `jev doctor` 体检环境", err=True)

    from jev.server.app import build_serve_app

    app = build_serve_app(kernel)
    typer.echo(f"Jev 就绪: http://{listen_host}:{listen_port}（health: /health, docs: /docs）")
    try:
        uvicorn.run(app, host=listen_host, port=listen_port, log_level="info")
    finally:
        kernel.close()


@app.command()
def mcp(
    url: Annotated[
        str,
        typer.Option("--url", help="jev serve 服务地址（默认本机 8300）"),
    ] = "http://127.0.0.1:8300",
) -> None:
    """以 MCP server 运行（stdio）：将 jev_decide/jev_judge/jev_rate 暴露为 MCP tools。

    需先运行 `jev serve`；本命令为薄壳代理（不自持模型资产）。
    """
    from jev.mcp_server import create_mcp_server
    from jev.sdk import JevClient

    server = create_mcp_server(JevClient(url))
    asyncio.run(server.run_stdio_async())


@app.command()
def distill(
    calibrate_flag: Annotated[
        bool,
        typer.Option("--calibrate", help="冻结权重重校准（温度缩放+阈值扫描+留出集评估）"),
    ] = False,
    lora_flag: Annotated[
        bool,
        typer.Option("--lora", help="kev-0.8B LoRA 微调（GPU，独立 py3.12 环境，长时）"),
    ] = False,
    kev_repo: Annotated[
        Path | None,
        typer.Option("--kev-repo", help="kev 仓库路径（默认 models/kev/repo）"),
    ] = None,
    min_samples: Annotated[
        int, typer.Option("--min-samples", help="蒸馏最小样本阈值（默认 500）")
    ] = 500,
    ledger_dir: Annotated[
        Path | None,
        typer.Option("--ledger-dir", help="台账目录（默认取配置 [ledger].dir）"),
    ] = None,
    decision_points: Annotated[
        Path | None,
        typer.Option("--decision-points", help="决策点声明（默认配置同目录 decision-points.toml）"),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="配置文件路径（默认 configs/jev.toml）"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="产物目录（默认 data/distill/<时间戳>）"),
    ] = None,
) -> None:
    """蒸馏飞轮（FR12）：台账 → (state, question, gold) 数据集 [+ 校准]。"""
    import time as _time

    from jev.distill import build_dataset, calibrate, export_dataset, save_calibration
    from jev.kernel.decisions.framework import DecisionRegistry
    from jev.kernel.ledger.query import LedgerQuery

    config_path = Path(config) if config is not None else repo_root() / "configs" / "jev.toml"
    if ledger_dir is None:
        ledger_cfg: dict = {}
        if config_path.is_file():
            try:
                ledger_cfg = tomllib.loads(config_path.read_text(encoding="utf-8")).get(
                    "ledger", {}
                )
            except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
                ledger_cfg = {}
        ledger_dir = Path(ledger_cfg.get("dir", repo_root() / "data" / "ledger"))
    points_path = (
        decision_points
        if decision_points is not None
        else config_path.parent / "decision-points.toml"
    )

    registry = DecisionRegistry.from_toml(points_path)
    dataset = build_dataset(LedgerQuery(dir=ledger_dir), min_samples=min_samples)
    out_dir = out or (repo_root() / "data" / "distill" / _time.strftime("%Y%m%d-%H%M%S"))
    export_dataset(dataset, out_dir)
    typer.echo(
        f"数据集就位: {out_dir}（{len(dataset.samples)} 样本 / "
        f"{len(dataset.strata)} 决策点 / "
        f"切分 {len(dataset.train)}/{len(dataset.dev)}/{len(dataset.test)}）"
    )

    if calibrate_flag:
        report = calibrate(dataset, registry)
        save_calibration(report, out_dir / "calibration.json")
        baseline, after = report.baseline_test, report.test
        typer.echo(
            f"校准: 温度 {report.temperature} / 阈值 {report.tau}\n"
            f"留出集 NLL {baseline.nll:.4f}→{after.nll:.4f} / "
            f"ECE {baseline.ece:.4f}→{after.ece:.4f} / "
            f"精度 {baseline.accuracy:.2%}→{after.accuracy:.2%}"
        )
        if report.accepted:
            typer.echo(f"发布: {out_dir / 'calibration.json'}")
        else:
            typer.echo("拒绝发布: 校准指标劣化，保留旧校准文件", err=True)
            raise typer.Exit(1)

    if lora_flag:
        from jev.distill import train_lora

        repo = kev_repo or (repo_root() / "models" / "kev" / "repo")
        typer.echo(f"kev LoRA 训练启动（GPU 长时）: {repo}")
        train_lora(dataset, registry, repo, out_dir / "lora")
        typer.echo(
            f"适配器就位: {out_dir / 'lora' / 'adapter'}\n"
            f"服务接入: kev.serve --run {out_dir / 'lora' / 'adapter'} "
            f"+ [cerebellum] default_backend=\"kev\""
        )


@app.command()
def matrix(
    config: Annotated[
        Path,
        typer.Option("--config", help="实验配置（如 configs/experiments/ar005.toml）"),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", help="只跑前 N 题（0=全部）"),
    ] = 0,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="输出目录（默认 benchmarks/results/<run>）"),
    ] = None,
    write_profile: Annotated[
        bool,
        typer.Option("--write-profile", help="采纳时写入 configs/runtime-profile.toml"),
    ] = False,
) -> None:
    """真实实验矩阵（AR005）：B0/B1/B2/AR005 + 校准 + 选择/回退。"""
    from jev.bench.matrix import run_matrix

    try:
        payload = run_matrix(
            config, out_dir=out, limit=limit or None, write_profile=write_profile
        )
    except (JevAssetError, JevError) as exc:
        typer.echo(f"错误: {exc}", err=True)
        raise typer.Exit(2) from exc
    selection = payload["selection"]
    run_name = payload["run"]["name"]
    typer.echo(
        f"矩阵完成: {run_name}\n"
        f"选择: {selection['decision']}（chosen={selection['chosen_arm']}，"
        f"baseline={selection['baseline_arm']}）\n"
        f"原因: {selection['reason']}"
    )


@app.command()
def bench(
    full: Annotated[
        bool,
        typer.Option("--full", help="全量基准：含直连大脑基线对比与决策点开销剖析（长时）"),
    ] = False,
    set_path: Annotated[
        Path | None,
        typer.Option("--set", help="基准问题集（默认 configs/bench-set.yaml）"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="报告目录（默认 data/bench/<时间戳>）"),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="配置文件路径（默认 configs/jev.toml）"),
    ] = None,
) -> None:
    """评测基准（FR13）：延迟分布/命中率/精度保持/成本/决策点开销 + BS-05 验收门。"""
    import time as _time

    from jev.bench.report import write_reports
    from jev.bench.runner import load_bench_set, run_bench
    from jev.kernel.cerebrum.llama_server import LlamaServerClient

    config_path = Path(config) if config is not None else repo_root() / "configs" / "jev.toml"
    questions = load_bench_set(set_path or repo_root() / "configs" / "bench-set.yaml")

    try:
        kernel = Kernel.from_config(config_path)
    except JevAssetError as exc:
        typer.echo(f"错误: {exc}", err=True)
        typer.echo("提示: 先运行 `jev fetch --all` 拉取模型资产", err=True)
        raise typer.Exit(1) from exc

    # 大脑：级联 full 路径需要（配置了就拉起）；基线对比仅 --full
    brain_client = None
    if kernel.brain is not None:
        try:
            kernel.brain.start()
        except BrainStartupError as exc:
            typer.echo(f"错误: 大脑启动失败: {exc}", err=True)
            kernel.close()
            raise typer.Exit(1) from exc
        brain_client = LlamaServerClient(kernel.brain.base_url)
    elif full:
        typer.echo("错误: --full 需要大脑基线，但配置缺 [cerebrum]", err=True)
        kernel.close()
        raise typer.Exit(1)

    try:
        result = run_bench(
            cerebellum=kernel,
            registry=kernel.registry,
            brain=brain_client,
            questions=questions,
            include_baseline=full,
        )
    finally:
        kernel.close()

    out_dir = out or (repo_root() / "data" / "bench" / _time.strftime("%Y%m%d-%H%M%S"))
    paths = write_reports(result, out_dir)
    gates = result.gates
    brain_p50 = gates["brain_path_p50_ms"]
    brain_p50_text = f"{brain_p50}ms" if brain_p50 is not None else "无样本"
    direct_p50 = gates["direct_p50_ms"]
    direct_p50_text = f"{direct_p50}ms" if direct_p50 is not None else "无样本"
    typer.echo(
        f"基准完成: {out_dir}\n"
        f"报告: {paths['markdown'].name} / {paths['json'].name}\n"
        f"命中率 {gates['hit_rate']:.1%}（门 ≥70%: "
        f"{'PASS' if gates['hit_rate_pass'] else 'FAIL'}）/ "
        f"直答 P50 {direct_p50_text} vs 大脑路径 P50 {brain_p50_text}"
        f"（门 ≤1/3: {'PASS' if gates['latency_pass'] else 'FAIL'}）"
    )
    if full and not gates["pass"]:
        raise typer.Exit(1)


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="配置文件路径（默认 configs/jev.toml）"),
    ] = None,
) -> None:
    """环境体检：Python/GPU/模型资产/llama-server/端口，输出问题清单与修复建议。"""
    config_path = Path(config) if config is not None else repo_root() / "configs" / "jev.toml"
    checks, exit_code = run_doctor(config_path)
    for line in render_checks(checks):
        typer.echo(line)
    raise typer.Exit(exit_code)


@app.command()
def version() -> None:
    """打印 jev 版本。"""
    typer.echo(jev.__version__)


if __name__ == "__main__":
    app()
