/**
 * DSH 顶栏状态灯 · Host 半边。
 *
 * 两件事：
 *  1) 设置开关：$DSH_HOME/beacon-settings.json 里的 enabled，托盘进程每 1.5 秒读它决定显示/隐藏。
 *  2) 跳转焦点：托盘点击时写 $DSH_HOME/beacon-focus.json，浏览器里的客户端插件轮询取走再清空。
 *     不走 URL 深链，因为带 token 的地址会被服务端 303 到 ./，query 参数会被丢掉。
 *
 * 三色状态本身不经过这里：托盘直接读会话投影与会话日志。
 *
 * @module dsh-beacon
 */
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export const name = 'dsh-beacon'
export const inject = ['loader']

const BASE = '/dsh-beacon'
const DEFAULTS = { enabled: true }

function dshHome() {
  return process.env.DSH_HOME || join(homedir(), '.dsh')
}

const settingsFile = () => join(dshHome(), 'beacon-settings.json')
const focusFile = () => join(dshHome(), 'beacon-focus.json')

async function readSettings() {
  try {
    const doc = JSON.parse(await readFile(settingsFile(), 'utf8'))
    return { enabled: doc?.enabled !== false }
  } catch {
    // 文件不存在或坏了都按默认开启，保证灯至少能亮
    return { ...DEFAULTS }
  }
}

async function writeJson(file, payload) {
  await mkdir(dirname(file), { recursive: true })
  await writeFile(file, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
}

async function writeSettings(patch) {
  const next = { ...(await readSettings()), ...patch }
  await writeJson(settingsFile(), next)
  return next
}

async function readFocus() {
  try {
    const doc = JSON.parse(await readFile(focusFile(), 'utf8'))
    return doc && typeof doc === 'object' ? doc : {}
  } catch {
    return {}
  }
}

function sendJson(res, status, payload) {
  res.statusCode = status
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(payload))
}

function trusted(ctx, req, res) {
  try {
    const rejection = ctx.connection?.requestRejection?.(req)
    if (rejection !== undefined) {
      res.statusCode = rejection
      res.end()
      return false
    }
  } catch {
    // 没有 connection 服务时放行
  }
  res.setHeader('cache-control', 'no-store')
  return true
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > 8 * 1024) {
        reject(new Error('请求体过大'))
        req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

async function handleSettings(ctx, req, res) {
  if (!trusted(ctx, req, res)) return
  try {
    if (req.method === 'GET') {
      sendJson(res, 200, await readSettings())
      return
    }
    if (req.method === 'POST') {
      const raw = await readBody(req)
      const body = raw === '' ? {} : JSON.parse(raw)
      if (typeof body?.enabled !== 'boolean') {
        sendJson(res, 400, { error: 'enabled 必须是布尔值' })
        return
      }
      sendJson(res, 200, await writeSettings({ enabled: body.enabled }))
      return
    }
    res.statusCode = 405
    res.setHeader('allow', 'GET, POST')
    res.end()
  } catch (error) {
    sendJson(res, 500, { error: String(error?.message ?? error) })
  }
}

async function handleFocus(ctx, req, res) {
  if (!trusted(ctx, req, res)) return
  try {
    if (req.method === 'GET') {
      sendJson(res, 200, await readFocus())
      return
    }
    sendJson(res, 405, { error: '只支持 GET' })
  } catch (error) {
    sendJson(res, 500, { error: String(error?.message ?? error) })
  }
}

async function handleFocusClear(ctx, req, res) {
  if (!trusted(ctx, req, res)) return
  try {
    if (req.method !== 'POST') {
      sendJson(res, 405, { error: '只支持 POST' })
      return
    }
    await rm(focusFile(), { force: true })
    sendJson(res, 200, { ok: true })
  } catch (error) {
    sendJson(res, 500, { error: String(error?.message ?? error) })
  }
}

export function apply(ctx) {
  ctx.inject(['webServer', 'connection'], (inner) => {
    const routes = [
      [`${BASE}/api/settings`, handleSettings],
      [`${BASE}/api/focus`, handleFocus],
      [`${BASE}/api/focus/clear`, handleFocusClear],
    ]
    for (const [path, handler] of routes) {
      inner.effect(() =>
        inner.webServer.register({
          kind: 'exact',
          path,
          handler: (req, res) => handler(inner, req, res),
        }),
      )
    }
  })
}
