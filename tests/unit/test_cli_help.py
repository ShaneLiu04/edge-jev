"""FR9-2（review 补齐）：CLI 子命令 --help 全量可用（serve/distill/doctor）。"""

from typer.testing import CliRunner

from jev.cli.main import app

runner = CliRunner()


def test_serve_help_exits_zero():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--no-brain" in result.output


def test_distill_help_exits_zero():
    result = runner.invoke(app, ["distill", "--help"])
    assert result.exit_code == 0
    for option in ("--calibrate", "--ledger-dir", "--out"):
        assert option in result.output


def test_doctor_help_exits_zero():
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "体检" in result.output or "doctor" in result.output
