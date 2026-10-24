# Jev CI 脚本（Windows PowerShell）
# 用法：powershell -File scripts/ci.ps1 [-WithAssets]

param(
    [switch]$WithAssets   # 需先 jev fetch --all 就位真实资产
)

$ErrorActionPreference = "Stop"
if ($env:UV_DEFAULT_INDEX -eq $null) {
    $env:UV_DEFAULT_INDEX = "http://mirrors.tools.huawei.com/pypi/simple/"
}

Write-Host "=== [1/3] uv sync ==="
uv sync
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "=== [2/3] ruff check ==="
uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "=== [3/3] pytest ==="
if ($WithAssets) {
    uv run pytest --cov -m "assets"
} else {
    uv run pytest tests/unit --cov
}
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "CI PASS"
