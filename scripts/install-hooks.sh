#!/usr/bin/env bash
# 安装本仓库的 git hooks(指向版本化的 .githooks/ 目录)
# 每次新 clone 之后运行一次即可。

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .githooks ]; then
    echo "❌ 找不到 .githooks/ 目录"
    exit 1
fi

git config core.hooksPath .githooks
chmod +x .githooks/*

echo "✅ git hooks 已启用 (core.hooksPath=.githooks)"
echo "   安装的 hooks:"
ls -1 .githooks | sed 's/^/   - /'
echo ""
echo "💡 误报时可临时绕过: git commit --no-verify"
