#!/usr/bin/env bash
# dsh-beacon 安装脚本：把托盘程序装到独立目录，可选装 DSH 插件。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${DSH_BEACON_HOME:-$HOME/.local/share/dsh-beacon}"
DSH_ROOT="${DSH_ROOT:-$HOME/opt/DSH}"
WITH_PLUGIN=0
PROFILE=web

die() { echo "错误：$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法：./install.sh [选项]

  --prefix <目录>     状态灯安装目录，默认 ~/.local/share/dsh-beacon
  --dsh-root <目录>   DSH 安装目录，默认 ~/opt/DSH（只用它读 dsh.log、调 stop-dsh.sh）
  --with-plugin       同时安装 DSH 插件（设置页开关 + 点击跳转），需要 dsh 命令
  --profile <名字>    插件装进哪个 profile，默认 web
  -h, --help          显示本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)     PREFIX="$2"; shift 2 ;;
    --dsh-root)   DSH_ROOT="$2"; shift 2 ;;
    --with-plugin) WITH_PLUGIN=1; shift ;;
    --profile)    PROFILE="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "未知参数 $1（用 --help 看用法）" ;;
  esac
done

# ---------- 依赖检查 ----------
missing=()
command -v python3 >/dev/null || missing+=("python3")
python3 -c 'import gi' 2>/dev/null || missing+=("python3-gi")
python3 -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null \
  || missing+=("gir1.2-ayatanaappindicator3-0.1")
command -v zstd     >/dev/null || missing+=("zstd")
command -v xwininfo >/dev/null || missing+=("x11-utils")

if [ ${#missing[@]} -gt 0 ]; then
  echo "缺少依赖：${missing[*]}" >&2
  echo "Debian/Ubuntu 上可以这样装：" >&2
  echo "  sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 zstd x11-utils" >&2
  exit 1
fi

# ---------- 托盘本体 ----------
mkdir -p "$PREFIX/icons"
install -m 755 "$SRC/tray.py"      "$PREFIX/tray.py"
install -m 644 "$SRC/dsh_state.py" "$PREFIX/dsh_state.py"
cp "$SRC"/icons/dot-*.png "$PREFIX/icons/"

echo "托盘已装到 $PREFIX"
echo "  DSH 安装目录：$DSH_ROOT"
if [ ! -f "$DSH_ROOT/dsh.log" ]; then
  echo "  提示：$DSH_ROOT/dsh.log 不存在，点击图标只会打开首页，拿不到 token。"
  echo "        用 DSH_ROOT=... 指向真正的 DSH 安装目录即可。"
fi

# ---------- DSH 插件（可选） ----------
if [ "$WITH_PLUGIN" -eq 1 ]; then
  PLUGIN_DIR="$HOME/.dsh/plugins/dsh-beacon"
  PROFILE_DIR="$HOME/.dsh/profiles/$PROFILE"
  [ -f "$PROFILE_DIR/package.json" ] || die "找不到 $PROFILE_DIR/package.json，先把 DSH 跑起来一次再装插件"

  mkdir -p "$PLUGIN_DIR/lib"
  cp "$SRC/plugin/package.json"      "$PLUGIN_DIR/"
  cp "$SRC/plugin/cordis.patch.yml"  "$PLUGIN_DIR/"
  cp "$SRC/plugin/lib/"*.js          "$PLUGIN_DIR/lib/"

  backup="$PROFILE_DIR/package.json.bak-dsh-beacon-$(date +%Y%m%d-%H%M%S)"
  cp "$PROFILE_DIR/package.json" "$backup"

  command -v dsh >/dev/null || die "找不到 dsh 命令，插件文件已放好，但需要在有 node/nvm 的环境里手动加依赖"
  dsh plugin --profile "$PROFILE" add "link:$PLUGIN_DIR"

  # dsh plugin add 只加依赖，bundles 列表得自己补
  node - "$PROFILE_DIR/package.json" <<'NODE'
const fs = require('fs')
const file = process.argv[2]
const doc = JSON.parse(fs.readFileSync(file, 'utf8'))
doc.dsh ??= {}
doc.dsh.profile ??= {}
const bundles = doc.dsh.profile.bundles ??= []
if (!bundles.includes('dsh-beacon')) bundles.push('dsh-beacon')
fs.writeFileSync(file, JSON.stringify(doc, null, 2) + '\n')
NODE

  echo "插件已装进 profile「$PROFILE」，原 package.json 备份在 $backup"
  echo "重启 DSH 后，设置页会出现「状态灯」分区。"
fi

cat <<EOF

下一步：
  1. 启动 DSH，然后手动跑一次看效果：
       DISPLAY=\$DISPLAY DSH_ROOT="$DSH_ROOT" "$PREFIX/tray.py"
  2. 让它跟着 DSH 一起启动，在 DSH 的 start 脚本里加：
       [ -f "$PREFIX/tray.py" ] && nohup "$PREFIX/tray.py" >/dev/null 2>&1 &
  3. 停止：kill "\$(cat "$PREFIX/tray.pid")"
EOF
