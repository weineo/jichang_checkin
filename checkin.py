#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 v5.0 —— Cookie 版

为什么这样写：
  站点签到接口是  POST /user/checkin  （见前端 malio.js 的 function checkin()，
  jQuery ajax、dataType json、无参数、无 CSRF token、成功回调读 data.msg）。
  也就是说：只要带上有效的登录 Cookie，接口就能签到 ——
  完全不需要打开登录页，也就不需要过 GeeTest v4 验证码。

  所以在本地登录一次、把 Cookie 存进仓库 Secret，之后每天由 Actions 直接调接口。
  零验证码、零浏览器、零第三方依赖、零现金成本。

需要的仓库 Secret：
  COOKIE   必填。一个账号一行。可以放多行实现多账号。
           格式（分号分隔即可，顺序无所谓）：
           uid=xxx; email=xxx; key=xxx; expire_in=xxx
  URL      可选。默认 https://ikuuu.top
  SCKEY    可选。Server酱推送（填了就推送签到结果到微信）。
  COOKIE_ONLY_TEST  可选。设成 1 时只校验 Cookie 是否有效，不执行签到。

本地怎么拿到 COOKIE：见同目录 get_cookie.py。
"""

import os
import sys
import re
import time
import json

import requests

DEFAULT_URL = 'https://ikuuu.top'
URL = (os.environ.get('URL', '') or DEFAULT_URL).rstrip('/')
# 优先读 COOKIE；若你不想改工作流文件，也可以直接把 Cookie 放进原有的 CONFIG secret
COOKIE_RAW = os.environ.get('COOKIE', '') or os.environ.get('CONFIG', '')
COOKIE_FROM = 'COOKIE' if os.environ.get('COOKIE', '').strip() else ('CONFIG' if os.environ.get('CONFIG', '').strip() else '')
SCKEY = os.environ.get('SCKEY', '')
ONLY_TEST = os.environ.get('COOKIE_ONLY_TEST', '') in ('1', 'true', 'True', 'yes')

CHECKIN_PATH = '/user/checkin'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icon = {"INFO": "🔹", "OK": "✅", "FAIL": "❌", "WARN": "⚠️", "STEP": "👉"}.get(level, "  ")
    print(f"[{ts}] {icon} {msg}", flush=True)


def parse_cookie_lines(raw):
    """把 Secret 里的原始文本解析成 [(标签, cookie字典), ...]"""
    accounts = []
    for idx, line in enumerate(raw.splitlines()):
        line = line.strip().strip(';')
        if not line or line.startswith('#'):
            continue
        jar = {}
        for part in line.split(';'):
            part = part.strip()
            if not part or '=' not in part:
                continue
            k, v = part.split('=', 1)
            jar[k.strip()] = v.strip()
        if not jar:
            log(f"第 {idx + 1} 行不是合法 Cookie，已跳过", "WARN")
            continue
        accounts.append((idx + 1, jar))
    return accounts


def mask(v):
    if not v:
        return ''
    return v if len(v) <= 6 else f"{v[:3]}…{v[-2:]}"


def mask_equal(s):
    """把 a@b.com 掩成 a***@b.com，避免账号出现在公开的 Actions 日志里"""
    if not s:
        return ''
    if '@' in s:
        name, _, domain = s.partition('@')
        head = name[:1] if name else ''
        return f"{head}***@{domain}"
    return mask(s)


def describe(jar):
    """脱敏描述，避免把 Cookie / 账号打到公开的 Actions 日志里"""
    uid = jar.get('uid', '')
    email = jar.get('email', '')
    tips = [k for k in ('uid', 'key', 'email', 'expire_in') if k in jar]
    who = mask_equal(email) if email else (f"uid={mask(uid)}" if uid else "未知账号")
    exp = jar.get('expire_in', '')
    if exp.isdigit():
        try:
            left = int(exp) - int(time.time())
            exp = f"，剩余约 {left // 86400} 天" if left > 0 else "，⚠️ 已过期"
        except Exception:
            exp = ''
    else:
        exp = ''
    return f"{who}（字段: {','.join(tips)}{exp}）"


def build_session(jar):
    s = requests.Session()
    s.cookies.update(jar)
    s.headers.update({
        'User-Agent': UA,
        'Referer': f'{URL}/user',
        'Origin': URL,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
    })
    return s


def probe_alive(session):
    """访问 /user 判断 Cookie 是否还在登录态（未登录会被重定向到 /auth/login）"""
    try:
        r = session.get(f'{URL}/user', timeout=20, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get('Location', '')
            return ('login' not in loc), f"跳转 {loc}"
        if r.status_code == 200:
            body = r.text
            # 新版前端登录页会带「登录」表单；已登录的用户中心应有 签到 / 流量 之类字样
            if 'id="password"' in body or 'initGeetest4' in body:
                return False, "返回的是登录页"
            return True, "HTTP 200"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"请求异常 {e}"


def do_checkin(session):
    """POST /user/checkin，返回 (是否成功, 结果文本)"""
    try:
        r = session.post(f'{URL}{CHECKIN_PATH}', timeout=25, allow_redirects=False)
    except Exception as e:
        return False, f"请求异常: {e}"

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get('Location', '')
        if 'login' in loc or 'auth' in loc:
            return False, "Cookie 已失效（被重定向到登录页）"
        return False, f"被重定向: {loc}"

    raw = (r.text or '').strip()
    try:
        d = json.loads(raw)
    except ValueError:
        # 不是 JSON：多半是登录页 HTML
        if 'initGeetest4' in raw or 'id="password"' in raw:
            return False, "Cookie 已失效（返回登录页）"
        return False, f"非 JSON 响应（HTTP {r.status_code}）: {raw[:200]}"

    msg = d.get('msg') or d.get('data') or ''
    if d.get('ret') == 1 or d.get('success') is True:
        return True, str(msg) or '签到成功'
    if '已签到' in str(msg) or '已经签到' in str(msg) or '签到成功' in str(msg):
        return True, str(msg)
    if msg:
        return False, str(msg)
    return False, f"未知响应: {raw[:200]}"


def push(title, content):
    if not SCKEY:
        return
    try:
        requests.post(f'https://sctapi.ftqq.com/{SCKEY}.send',
                      data={'title': title, 'desp': content}, timeout=15)
        log("推送已发送", "OK")
    except Exception as e:
        log(f"推送失败: {e}", "WARN")


def main():
    log("=" * 55)
    log("  iKuuu 机场自动签到 v5.0（Cookie 版）")
    log(f"  站点: {URL}")
    log(f"  模式: {'仅校验 Cookie' if ONLY_TEST else '签到'}")
    log("=" * 55)

    if not COOKIE_RAW.strip():
        log("未配置 COOKIE（或 CONFIG）Secret —— 请先用 get_cookie.py 获取并写入", "FAIL")
        sys.exit(1)
    log(f"Cookie 来源: {COOKIE_FROM}")

    accounts = parse_cookie_lines(COOKIE_RAW)
    if not accounts:
        log("COOKIE 内容无法解析", "FAIL")
        sys.exit(1)

    log(f"共 {len(accounts)} 个账号")
    results = []

    for n, (_, jar) in enumerate(accounts, 1):
        label = mask_equal(jar.get('email', '')) or (f"uid={mask(jar.get('uid', ''))}" if jar.get('uid') else f"账号{n}")
        log(f"\n{'-' * 20} 账号 {n} {'-' * 20}")
        log(f"身份: {describe(jar)}")
        session = build_session(jar)

        alive, why = probe_alive(session)
        if not alive:
            log(f"Cookie 不可用: {why}", "FAIL")
            results.append(f"{label}: ❌ Cookie 失效（{why}），请重新运行 get_cookie.py 并更新 Secret")
            continue
        log(f"Cookie 有效（{why}）", "OK")

        if ONLY_TEST:
            results.append(f"{label}: ✅ Cookie 有效")
            continue

        ok, msg = do_checkin(session)
        if ok:
            log(f"签到成功: {msg}", "OK")
            results.append(f"{label}: ✅ {msg}")
        else:
            log(f"签到失败: {msg}", "FAIL")
            results.append(f"{label}: ❌ {msg}")

        time.sleep(3)

    if SCKEY and results:
        push("机场签到", "## 📊 iKuuu 签到结果\n\n" + "\n\n".join(f"- {r}" for r in results))

    log("\n" + "=" * 55)
    for r in results:
        log(r, "OK" if "✅" in r else "FAIL")
    log("=" * 55)

    expired = [r for r in results if "Cookie 失效" in r]
    if expired:
        log("❗ 有 Cookie 已失效，请重新运行 get_cookie.py 并更新仓库 Secret", "FAIL")

    if all("❌" in r for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
