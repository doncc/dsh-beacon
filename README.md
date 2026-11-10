# dsh-beacon · DSH 顶栏状态灯

[English](README.en.md) | 简体中文

> 顶栏一个点，告诉你 DSH 有没有在等你。

<p align="center">
  <img src="docs/images/topbar.png" alt="GNOME 顶栏上的三种状态灯" width="480">
</p>

## 这是什么

DeepSeek Harness（DSH） 在本地跑，一个工作区里可能同时开着好几个会话。切到别的窗口之后，你没法一眼看出：某个会话是不是停在「等你回答」或者「等你批准权限」上。

`dsh-beacon` 在 Linux 系统托盘常驻一个小圆点，用三种颜色表示本机全部 DSH 会话的聚合状态；点击它就能跳回最早那条在等你的对话。它只读 DSH 的本地状态文件，不往 DSH 进程里注入任何东西。

## 三种颜色

| 颜色 | 状态 | 含义 |
|---|---|---|
| 🟢 绿 | 全部完成 | 没有会话在跑，也没有等你确认的事 |
| 🟡 黄 | 等你确认 | 至少有一条待回答的提问，或待批准的权限请求；图标会闪烁 |
| ⚪ 灰 | 正在执行 | 有会话在跑，但没有卡在等你 |

优先级是 **黄 > 灰 > 绿**。

## 交互

| 操作 | 效果 |
|---|---|
| 中键单击 | 直接跳到「最早待确认」的那个会话 |
| 左键 / 右键 | 弹出菜单：状态摘要、待确认列表（每条可点跳转）、打开 DSH 网页、刷新状态、停止 DSH、仅关闭状态灯 |

为什么不能单击直达：GNOME 的托盘协议里左键单击必然打开菜单，`Activate` 方法 libayatana 没有实现；中键走 `SecondaryActivate`，是唯一能做到「一键直达」的手势。

## 安装

### 1. 依赖

Debian / Ubuntu：

```bash
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 zstd x11-utils
```

| 依赖 | 用途 |
|---|---|
| `python3-gi` + `gir1.2-ayatanaappindicator3-0.1` | GTK3 托盘图标 |
| `zstd` | 解压会话日志，配对审批与提问 |
| `x11-utils`（`xwininfo`） | 找到已经打开的 DSH 浏览器窗口并置前 |

GNOME 需要 AppIndicator 扩展（Ubuntu 默认启用）。KDE、XFCE 等支持 StatusNotifierItem 的桌面也能显示。

### 2. 装托盘本体

```bash
git clone https://github.com/doncc/dsh-beacon.git
cd dsh-beacon
./install.sh --dsh-root ~/opt/DSH
```

- 默认装到 `~/.local/share/dsh-beacon`，用 `--prefix` 改。
- `--dsh-root` 指向 **DSH 本体的安装目录**：状态灯只从这里读 `dsh.log`（拿带 token 的地址）和调用 `stop-dsh.sh`，不会改它。
- 先启动 DSH，再手动跑一次看效果：

```bash
DSH_ROOT=~/opt/DSH ~/.local/share/dsh-beacon/tray.py
```

### 3. 让它跟着 DSH 一起启动

在 DSH 的启动脚本里加一行（示例是 `~/opt/DSH/start-dsh.sh`）：

```bash
# DSH 端口起来之后
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then :; else
  pgrep -f "$HOME/.local/share/dsh-beacon/tray.py" >/dev/null || \
    nohup "$HOME/.local/share/dsh-beacon/tray.py" >/dev/null 2>&1 &
fi
```

停止脚本里对应加：

```bash
[ -f "$HOME/.local/share/dsh-beacon/tray.pid" ] && \
  kill "$(cat "$HOME/.local/share/dsh-beacon/tray.pid")" 2>/dev/null || true
```

也可以写成 systemd 用户服务：

```ini
# ~/.config/systemd/user/dsh-beacon.service
[Unit]
Description=dsh-beacon status light for DeepSeek Harness
After=graphical-session.target

[Service]
ExecStart=%h/.local/share/dsh-beacon/tray.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

托盘进程发现 3080 端口没开时会自己退出，所以和 DSH 的启动顺序不敏感：DSH 起来后再 `systemctl --user restart dsh-beacon` 即可。

### 4. 装 DSH 插件（可选）

托盘本体已经能亮、能跳转。插件多给你两样东西：

- **设置页开关**：DSH 设置 →「状态灯」，勾掉后托盘把图标隐藏；
- **前端跳转**：托盘写焦点文件，页面轮询取走并切到对应会话。

```bash
./install.sh --with-plugin           # 默认装进 web profile
./install.sh --with-plugin --profile tui
```

脚本会把插件放到 `~/.dsh/plugins/dsh-beacon`，用 `dsh plugin --profile <名字> add link:...` 注册依赖，再把它补进 `dsh.profile.bundles`。原来的 `package.json` 会备份成 `package.json.bak-dsh-beacon-<时间戳>`。

装完重启 DSH，设置页会出现「状态灯」分区。

## 状态是怎么算出来的

全部只读，单次聚合约 26 ms：

- `~/.dsh/storages/session_projcache/sessions/*.json`
  - `userQuestions.questions.active` 非空 → 有待答提问；
  - `turnBoundary.lastStepBoundary.kind == "start"` → 这一步还没结束，在跑。
- `~/.dsh/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd`
  - `approval/asked` 与 `approval/decided` 配对，差集 = 还没决定的权限审批；
  - `tool/call(name=ask_user_question)` 与 `tool/result(toolCallId)` 配对，差集 = 还没被回答的提问（blocking 模式的提问不会写进 `userQuestions.active`，只能从日志里看）。

只有「在跑」或「有待答提问」的会话才解压日志，避免每 1.5 秒把几 MB 的日志全读一遍。

## 配置

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `DSH_ROOT` | `~/opt/DSH` | DSH 安装目录：读 `dsh.log`、调 `stop-dsh.sh` |
| `DSH_BEACON_HOME` | 脚本所在目录 | 图标和 `tray.pid` 放在哪 |
| `DSH_HOME` | `~/.dsh` | DSH 配置目录 |
| `DSH_PORT` | `3080` | DSH Web 端口 |

文件：

| 路径 | 作用 |
|---|---|
| `~/.dsh/beacon-settings.json` | 插件写的开关，`{"enabled": bool}` |
| `~/.dsh/beacon-focus.json` | 托盘写的跳转目标，前端轮询后删除 |

## 已知限制

1. **窗口置前依赖 X11**。「跳到会话」会先用 `_NET_ACTIVE_WINDOW` 把浏览器窗口提到前台，Wayland 会话下这一步会失败；焦点文件仍然有效，切回浏览器后页面会自己跳。
2. **图标必须用 PNG，且每次换状态要换文件名**。SVG 绝对路径在 GNOME 顶栏不显示；Shell 的 iconCache 按图标名缓存，同名换图不刷新。
3. **不能靠 URL 深链跳会话**。带 token 的地址会被服务端 303 到 `./`，query 参数会丢，所以改用焦点文件 + 前端轮询。
4. 托盘图标大小、闪烁节奏目前写死（1.5 s 轮询、350 ms 半周期）。

## 测试

```bash
python3 tests/test_state.py   # 状态机：三色判定与排序，离线跑，不碰真实 ~/.dsh
python3 tests/test_blink.py   # 黄灯闪烁相位
```

## 卸载

```bash
./uninstall.sh          # 摘掉插件、停托盘、删安装目录
```

## 许可证

[MIT](LICENSE)
