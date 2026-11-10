# dsh-beacon

English | [简体中文](README.md)

> One dot in the top bar that tells you whether DSH is waiting for you.

<p align="center">
  <img src="docs/images/topbar.png" alt="The three states in the GNOME top bar" width="480">
</p>

## What it is

DeepSeek Harness (DSH) runs locally, and one workspace can have several sessions open at the same time. After you switch to another window there is no quick way to tell whether a session stopped at "waiting for your answer" or "waiting for a permission approval".

dsh-beacon keeps a small dot in the Linux system tray and encodes the aggregate state of every local DSH session in its color. Click it to jump back to the oldest conversation waiting for you. It only reads DSH's local state files and never injects anything into the DSH process.

## The three states

| Color | State | Meaning |
|---|---|---|
| 🟢 Green | All clear | No session is running and nothing is waiting for you |
| 🟡 Yellow | Waiting for you | At least one unanswered question or pending permission request; the icon blinks |
| ⚪ Grey | Running | Sessions are running, but none of them is blocked on you |

Priority: **yellow > grey > green**.

## Interaction

| Action | Effect |
|---|---|
| Middle click | Jump straight to the oldest conversation waiting for you |
| Left / right click | Menu with a status summary, the pending list (each entry jumps), open DSH, refresh, stop DSH, close the light |

Why not a single left click: in GNOME's tray protocol a left click always opens the menu, and `Activate` is not implemented by libayatana. Middle click goes through `SecondaryActivate` — the only gesture that can jump directly.

## Install

Two paths:

- **DSH side only** (settings toggle + click-to-jump, no tray): this is the command the plugin market will give you once listed. The Settings section appears afterwards, but no dot shows up in the system tray.

  ```bash
  dsh plugin --profile web add github:doncc/dsh-beacon
  ```

- **The dot itself**: continue with steps 1 and 2 and run `./install.sh`; `--with-plugin` in step 4 installs the DSH plugin above along with the tray. The two plugin install paths are alternatives, not both.

### 1. Dependencies

Debian / Ubuntu:

```bash
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 zstd x11-utils
```

| Dependency | Used for |
|---|---|
| `python3-gi` + `gir1.2-ayatanaappindicator3-0.1` | GTK3 tray icon |
| `zstd` | Decompress session logs to pair approvals and questions |
| `x11-utils` (`xwininfo`) | Find an already open DSH browser window and raise it |

GNOME needs the AppIndicator extension (enabled by default on Ubuntu). KDE, XFCE and other desktops implementing StatusNotifierItem work as well.

### 2. Install the tray

```bash
git clone https://github.com/doncc/dsh-beacon.git
cd dsh-beacon
./install.sh --dsh-root ~/opt/DSH
```

- Installs to `~/.local/share/dsh-beacon` by default; change it with `--prefix`.
- `--dsh-root` points at the **DSH installation directory**. The tray only reads `dsh.log` from it (to get the tokenized URL) and calls `stop-dsh.sh`; it never modifies DSH.
- Start DSH first, then run the tray once by hand to see it:

```bash
DSH_ROOT=~/opt/DSH ~/.local/share/dsh-beacon/tray.py
```

### 3. Start it together with DSH

Add a line to DSH's start script (the example is `~/opt/DSH/start-dsh.sh`), after the DSH port is up:

```bash
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then :; else
  pgrep -f "$HOME/.local/share/dsh-beacon/tray.py" >/dev/null || \
    nohup "$HOME/.local/share/dsh-beacon/tray.py" >/dev/null 2>&1 &
fi
```

And to the stop script:

```bash
[ -f "$HOME/.local/share/dsh-beacon/tray.pid" ] && \
  kill "$(cat "$HOME/.local/share/dsh-beacon/tray.pid")" 2>/dev/null || true
```

A systemd user service works too:

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

The tray exits on its own when nothing listens on port 3080, so the start order does not matter much: once DSH is up, run `systemctl --user restart dsh-beacon`.

### 4. Install the DSH plugin (optional)

> Skip this if you already installed it with the market command above.

The tray already lights up and jumps on its own. The plugin adds two things:

- **A switch in Settings**: DSH Settings → "Status light". Unchecking it hides the tray icon.
- **In-page navigation**: the tray writes a focus file, the page polls it and switches to that session.

```bash
./install.sh --with-plugin           # installs into the web profile
./install.sh --with-plugin --profile tui
```

The script copies the plugin to `~/.dsh/plugins/dsh-beacon`, registers the dependency with `dsh plugin --profile <name> add link:...`, and appends it to `dsh.profile.bundles`. Your previous `package.json` is backed up as `package.json.bak-dsh-beacon-<timestamp>`.

Restart DSH afterwards and a "Status light" section shows up in Settings.

## How the state is computed

Read-only, about 26 ms per scan:

- `~/.dsh/storages/session_projcache/sessions/*.json`
  - a non-empty `userQuestions.questions.active` means a question is waiting for an answer;
  - `turnBoundary.lastStepBoundary.kind == "start"` means the step has not finished yet, so the session is running.
- `~/.dsh/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd`
  - pair `approval/asked` with `approval/decided`; the difference is the set of undecided permission approvals;
  - pair `tool/call(name=ask_user_question)` with `tool/result(toolCallId)`; the difference is the set of unanswered questions (blocking-mode questions never appear in `userQuestions.active`, they only exist in the log).

Logs are decompressed only for sessions that are running or waiting on a question, so the 1.5 s tick does not re-read megabytes of log every time.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DSH_ROOT` | `~/opt/DSH` | DSH install directory: read `dsh.log`, call `stop-dsh.sh` |
| `DSH_BEACON_HOME` | the script's directory | Where icons and `tray.pid` live |
| `DSH_HOME` | `~/.dsh` | DSH config directory |
| `DSH_PORT` | `3080` | DSH web port |

Files:

| Path | Purpose |
|---|---|
| `~/.dsh/beacon-settings.json` | Switch written by the plugin, `{"enabled": bool}` |
| `~/.dsh/beacon-focus.json` | Jump target written by the tray, deleted by the page after polling |

## Known limitations

1. **Raising the window relies on X11.** Jumping to a session first raises the browser window through `_NET_ACTIVE_WINDOW`, which fails on Wayland. The focus file still works: the page jumps as soon as you switch back to the browser.
2. **Icons must be PNG, and the file name must change per state.** An absolute SVG path is not shown in the GNOME top bar, and the Shell's icon cache is keyed by icon name, so swapping the image under the same name does not refresh it.
3. **No URL deep links for sessions.** A URL carrying a token is 303-redirected to `./` and query parameters are dropped, hence the focus file plus front-end polling.
4. Icon size and blink rhythm are hard-coded (1.5 s tick, 350 ms half period).

## Tests

```bash
python3 tests/test_state.py   # state machine: colors and ordering, offline, never touches the real ~/.dsh
python3 tests/test_blink.py   # blink phase of the yellow icon
```

## Uninstall

```bash
./uninstall.sh          # removes the plugin, stops the tray, deletes the install directory
```

## License

[MIT](LICENSE)
