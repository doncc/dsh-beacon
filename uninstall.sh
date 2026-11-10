#!/usr/bin/env bash
# dsh-beacon 卸载脚本：停托盘、删文件、从 profile 里摘掉插件。
set -euo pipefail

PREFIX="${DSH_BEACON_HOME:-$HOME/.local/share/dsh-beacon}"
PROFILE="${1:-web}"
PLUGIN_DIR="$HOME/.dsh/plugins/dsh-beacon"
PROFILE_PKG="$HOME/.dsh/profiles/$PROFILE/package.json"

# 停托盘
if [ -f "$PREFIX/tray.pid" ]; then
  pid="$(cat "$PREFIX/tray.pid" 2>/dev/null || true)"
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  rm -f "$PREFIX/tray.pid"
fi

# 摘插件
if [ -f "$PROFILE_PKG" ] && command -v node >/dev/null; then
  node - "$PROFILE_PKG" <<'NODE'
const fs = require('fs')
const file = process.argv[2]
const doc = JSON.parse(fs.readFileSync(file, 'utf8'))
if (doc.dependencies) delete doc.dependencies['dsh-beacon']
const bundles = doc.dsh?.profile?.bundles
if (Array.isArray(bundles)) doc.dsh.profile.bundles = bundles.filter((b) => b !== 'dsh-beacon')
fs.writeFileSync(file, JSON.stringify(doc, null, 2) + '\n')
NODE
  echo "已从 profile「$PROFILE」摘掉 dsh-beacon"
fi

rm -rf "$PLUGIN_DIR"
echo "已删除 $PLUGIN_DIR"
rm -rf "$PREFIX"
echo "已删除 $PREFIX"
echo "重启 DSH 生效。"
