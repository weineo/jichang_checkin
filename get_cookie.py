#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 登录 Cookie 获取工具（配合 checkin.py v5.0 使用）

干什么：
  打开一个浏览器窗口 -> 你正常登录 ikuuu（该点验证码就点一下，反正是你自己点）
  -> 脚本自动把登录 Cookie 抓出来、复制到剪贴板、并帮你打开 GitHub Secret 设置页。
  你把 Cookie 粘进名为 COOKIE 的 Secret 就完事了。

为什么要用脚本抓，而不是让你在浏览器控制台敲 document.cookie：
  登录态的 key 字段很可能是 HttpOnly 的，JS 读不到；用浏览器自动化读才拿得全。

用法（本机 Windows，需要 Python 3.9+）：
  pip install playwright
  python -m playwright install chromium
  python get_cookie.py                 # 登录 1 个账号
  python get_cookie.py --append        # 追加第 2 个账号（多账号场景）

  然后到 https://github.com/weineo/jichang_checkin/settings/secrets/actions
  新建（或更新）Secret：名称 COOKIE，值粘贴 cookie.txt 里那一行。

  备选：如果你已经在浏览器里登录好了，也可以直接把 Cookie 字符串传进来：
  python get_cookie.py --manual "uid=xxx; email=xxx; key=xxx; expire_in=xxx"
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from urllib.parse import urlparse

LOGIN_URL = 'https://ikuuu.top/auth/login'
USER_URL = 'https://ikuuu.top/user'
SITE_HOST = urlparse(LOGIN_URL).hostname or 'ikuuu.top'
SECRETS_PAGE = 'https://github.com/weineo/jichang_checkin/settings/secrets/actions'
HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, 'cookie.txt')
PROFILE_DIR = os.path.join(HERE, 'browser_profile')

NEEDED = ('uid', 'key', 'email')
# 统计/主题类 Cookie，跟登录态无关，不必带进 Secret
DROP = {'_ga', '_gid', '_gat', '_gcl_au', 'cf_clearance', 'PHPSESSID'}


def log(msg):
    print(msg, flush=True)


def to_clipboard(text):
    try:
        if os.name == 'nt':
            p = subprocess.run(['powershell', '-NoProfile', '-Command', '$input | Set-Clipboard'],
                               input=text, text=True, encoding='utf-8',
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            return p.returncode == 0
        p = subprocess.run(['pbcopy'], input=text.encode(), timeout=20)
        return p.returncode == 0
    except Exception:
        return False


def build_cookie_string(cookies):
    """只保留面板自己域名的 Cookie。
    注意：浏览器里还混着 msn.com / bing.com / gcaptcha4.geetest.com 的 Cookie
    （Windows 和 Edge 自动写的），全带上会让 Cookie 头膨胀到 5K+ 甚至被服务端拒收。"""
    parts = []
    for c in cookies:
        name = c.get('name') or ''
        dom = (c.get('domain') or '').lstrip('.').lower()
        if not name or name in DROP or name.startswith('_ga') or name.startswith('_gid'):
            continue
        if not (dom == SITE_HOST or dom.endswith('.' + SITE_HOST)):
            continue
        if c.get('value') is None:
            continue
        parts.append(f"{name}={c['value']}")
    return '; '.join(parts)


def save_cookie(cookie_str, append):
    mode = 'a' if append else 'w'
    with open(COOKIE_FILE, mode, encoding='utf-8') as f:
        if append:
            f.write('\n')
        f.write(cookie_str)
    lines = [l for l in open(COOKIE_FILE, encoding='utf-8').read().splitlines() if l.strip()]
    return len(lines)


def finish(cookie_str, append, show=False):
    n = save_cookie(cookie_str, append)
    ok = to_clipboard(cookie_str)
    print()
    log("=" * 64)
    log(f"✅ 已保存到 {COOKIE_FILE}（现在共 {n} 行 / {n} 个账号）")
    log(f"{'✅ 已复制到剪贴板' if ok else '⚠️ 剪贴板复制失败，请手动打开 cookie.txt 复制'}")
    log("=" * 64)
    log("")
    log("下一步：去 GitHub 新建/更新 Secret，名称必须叫 COOKIE，值直接粘贴（已在剪贴板）：")
    log(SECRETS_PAGE)
    log("")
    if show:
        print(cookie_str)
        print()
    else:
        keys = [p.split('=', 1)[0] for p in cookie_str.split('; ')]
        log(f"（为防凭据泄露，这里不打印完整值。字段：{', '.join(keys)}；"
            f"长度 {len(cookie_str)}。要看完整值请加 --show，或打开 cookie.txt）")
    try:
        webbrowser.open(SECRETS_PAGE)
        log("已帮你打开 Secret 设置页。")
    except Exception:
        pass


def run_browser(append, show=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("缺少 playwright。请先执行：")
        log("  pip install playwright && python -m playwright install chromium")
        sys.exit(1)

    os.makedirs(PROFILE_DIR, exist_ok=True)
    log("正在启动浏览器（用的是独立配置目录，不影响你平时的浏览器）…")

    with sync_playwright() as p:
        # 优先复用本机已装的 Edge / Chrome —— 这样不必再下载 Playwright 自带的 chromium
        ctx = None
        last_err = None
        for label, kw in (('本机 Edge', {'channel': 'msedge'}),
                          ('本机 Chrome', {'channel': 'chrome'}),
                          ('Playwright 内置 chromium', {})):
            try:
                ctx = p.chromium.launch_persistent_context(
                    PROFILE_DIR, headless=False,
                    viewport={'width': 1280, 'height': 900},
                    args=['--disable-blink-features=AutomationControlled'],
                    **kw,
                )
                log(f"已启动：{label}")
                break
            except Exception as e:
                last_err = e
                log(f"  {label} 启动失败：{str(e)[:140]}")
        if ctx is None:
            log("❌ 没有任何可用浏览器。请执行一次：python -m playwright install chromium")
            log(f"最后错误：{last_err}")
            sys.exit(1)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)

        # 顺手勾上「Remember Me」—— 能让登录态明显活得更久，少刷新几次
        for _ in range(20):
            try:
                done = page.evaluate("""
                    () => {
                        const el = document.querySelector('#remember-me');
                        if (!el) return false;
                        if (!el.checked) {
                            el.checked = true;
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        return true;
                    }
                """)
                if done:
                    log("已自动勾选「Remember Me」（可延长 Cookie 有效期）")
                    break
            except Exception:
                pass
            time.sleep(1)

        log("")
        log("=" * 64)
        log("请在弹出的窗口里完成登录（邮箱 + 密码 + 验证码）")
        log("登录成功后本脚本会自动抓取 Cookie，最多等 8 分钟。")
        log("=" * 64)

        deadline = time.time() + 480
        got = None
        while time.time() < deadline:
            try:
                cookies = ctx.cookies()
            except Exception:
                log("❌ 浏览器窗口被关闭了，没能取到 Cookie。重新运行一次即可。")
                sys.exit(1)

            jar = {c['name']: c['value'] for c in cookies}
            if all(k in jar and jar[k] for k in NEEDED):
                # 用「访问 /user 会不会被弹回登录页」来确认真的登进去了
                try:
                    page.goto(USER_URL, wait_until='domcontentloaded', timeout=30000)
                    time.sleep(2)
                    if '/login' in (page.url or '') or page.query_selector('#password, .embed-captcha'):
                        log("检测到仍在登录页，请继续完成登录…")
                        time.sleep(3)
                        continue
                except Exception:
                    pass
                got = cookies
                break
            time.sleep(2)

        if not got:
            log("❌ 等待超时，没有抓到登录 Cookie。")
            ctx.close()
            sys.exit(1)

        cookie_str = build_cookie_string(got)
        ctx.close()

    # 自检：必要字段在不在
    have = {p.split('=', 1)[0] for p in cookie_str.split('; ')}
    missing = [k for k in NEEDED if k not in have]
    if missing:
        log(f"⚠️ 抓到的 Cookie 里缺少字段 {missing} —— 可能还没登录成功，请重试。")
    finish(cookie_str, append, show)


def main():
    ap = argparse.ArgumentParser(description="获取 iKuuu 登录 Cookie")
    ap.add_argument('--append', action='store_true', help='追加一行（多账号）而不是覆盖')
    ap.add_argument('--manual', metavar='COOKIE', help='直接传入 Cookie 字符串（不启动浏览器）')
    ap.add_argument('--show', action='store_true', help='在终端完整打印 Cookie（默认不打印，防泄露）')
    args = ap.parse_args()

    if args.manual:
        s = args.manual.strip()
        if not s:
            log("传入的 Cookie 是空的"); sys.exit(1)
        finish(s, args.append, args.show)
        return

    run_browser(args.append, args.show)


if __name__ == '__main__':
    main()
