/**
 * DSH 顶栏状态灯 · Client 半边。
 *
 * 两件事：
 *  1) 深链：托盘点击后写 ~/.dsh/beacon-focus.json，这里轮询 /dsh-beacon/api/focus，
 *     拿到会话 id 就调 ctx.uiWorkspace.openSession() 切过去，然后清掉焦点。
 *     （不走 URL：带 token 的地址会被服务端 303 到 ./，query 参数会丢。）
 *  2) 设置页开关：关掉后托盘进程把图标隐藏。
 */
window.__ModuleLoader__.load({
  id: 'dsh-beacon',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    const API = '/dsh-beacon/api'
    const FOCUS_TTL_MS = 60_000
    const POLL_MS = 1500

    const CSS = `
.dt-page{box-sizing:border-box;width:100%;max-width:960px;display:flex;flex-direction:column;gap:12px;color:var(--dsw-alias-label-primary)}
.dt-row{display:flex;align-items:flex-start;gap:10px;border:.5px solid var(--dsw-alias-settings-card-stroke);background:var(--dsw-alias-settings-card-fill);border-radius:var(--dsw-radius-xl);padding:12px}
.dt-main{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:3px}
.dt-title{font-size:14px;font-weight:500;line-height:22px}
.dt-desc{font-size:12px;line-height:18px;color:var(--dsw-alias-label-secondary)}
.dt-switch{flex:none;display:inline-flex;align-items:center;gap:8px;font-size:13px;line-height:20px;cursor:pointer;user-select:none}
.dt-switch input{width:16px;height:16px;cursor:pointer}
.dt-legend{display:flex;flex-direction:column;gap:6px;font-size:12px;line-height:18px;color:var(--dsw-alias-label-secondary)}
.dt-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle;border:1px solid rgba(0,0,0,.35)}
`

    function useSettings() {
      const [enabled, setEnabled] = React.useState(undefined)
      const [error, setError] = React.useState(undefined)

      React.useEffect(() => {
        let alive = true
        fetch(`${API}/settings`, { credentials: 'same-origin' })
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
          .then((doc) => {
            if (alive) setEnabled(doc?.enabled !== false)
          })
          .catch((e) => {
            if (alive) setError(String(e?.message ?? e))
          })
        return () => {
          alive = false
        }
      }, [])

      const toggle = (next) => {
        setEnabled(next)
        setError(undefined)
        fetch(`${API}/settings`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ enabled: next }),
        }).catch((e) => setError(String(e?.message ?? e)))
      }

      return { enabled, error, toggle }
    }

    function TraySettings() {
      const { enabled, error, toggle } = useSettings()
      return h(
        'div',
        { className: 'dt-page' },
        h(
          'div',
          { className: 'dt-row' },
          h(
            'div',
            { className: 'dt-main' },
            h('div', { className: 'dt-title' }, '顶栏状态灯'),
            h(
              'div',
              { className: 'dt-desc' },
              '在 GNOME 顶栏显示一个圆点：绿=全部完成，黄=有对话等你确认，灰=正在执行。中键点击直接跳到最早待确认的对话。',
            ),
          ),
          h(
            'label',
            { className: 'dt-switch' },
            h('input', {
              type: 'checkbox',
              checked: enabled === true,
              disabled: enabled === undefined,
              onChange: (event) => toggle(event.target.checked),
            }),
            enabled === true ? '已开启' : enabled === false ? '已关闭' : '读取中',
          ),
        ),
        error !== undefined ? h('div', { className: 'dt-desc' }, `设置读写失败：${error}`) : null,
        h(
          'div',
          { className: 'dt-legend' },
          h('span', null, h('i', { className: 'dt-dot', style: { background: '#2ec27e' } }), '绿：所有对话都空闲'),
          h('span', null, h('i', { className: 'dt-dot', style: { background: '#e5a50a' } }), '黄：有待答提问或待批准的权限'),
          h('span', null, h('i', { className: 'dt-dot', style: { background: '#9a9996' } }), '灰：正在执行'),
        ),
      )
    }

    /** 轮询焦点文件，把「点图标要跳的会话」落到前端状态上。 */
    function followFocus(ctx) {
      const clear = () =>
        fetch(`${API}/focus/clear`, { method: 'POST', credentials: 'same-origin' }).catch(() => {})
      const tick = () => {
        fetch(`${API}/focus`, { credentials: 'same-origin' })
          .then((r) => (r.ok ? r.json() : null))
          .then((doc) => {
            const id = doc?.sessionId
            if (typeof id !== 'string' || id === '') return
            if (Date.now() - Number(doc?.at ?? 0) > FOCUS_TTL_MS) {
              clear()
              return
            }
            try {
              ctx.uiWorkspace.openSession(id)
            } catch {
              // 会话可能已被删除或属于别的工作区，静默放弃
            }
            clear()
          })
          .catch(() => {})
      }
      setInterval(tick, POLL_MS)
      // 后台标签的定时器会被浏览器节流到分钟级；窗口一回到前台就立刻查一次
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) tick()
      })
      tick()
    }

    return {
      inject: ['slots', 'uiWorkspace'],
      apply(ctx) {
        const style = document.createElement('style')
        style.dataset.plugin = 'dsh-beacon'
        style.textContent = CSS
        document.head.appendChild(style)

        followFocus(ctx)

        ctx.slots.inject('settings.section', () =>
          ctx.slots.register(
            {
              name: 'settings.section',
              id: 'dsh-beacon',
              order: 27,
              label: () => '状态灯',
            },
            TraySettings,
          ),
        )
      },
    }
  },
})
