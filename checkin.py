#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 v3.1
优化：删除无效API登录、修复签到结果提取、加速登录检测、精简代码
"""

import os
import sys
import time
import random
import re
import json
import requests as req_lib
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')
CAPSOLVER_KEY = os.environ.get('CAPSOLVER_KEY', '')

SCREENSHOT_DIR = Path("debug")


def get_accounts():
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG格式错误"); return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i + 1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置账号")
    return accounts


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icon = {"INFO": "🔹", "OK": "✅", "FAIL": "❌", "WARN": "⚠️", "STEP": "👉"}.get(level, "  ")
    print(f"[{ts}] {icon} {msg}", flush=True)


def take_screenshot(page, name):
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=False)
    except:
        pass


def push_notification(title, content):
    if not SCKEY:
        return
    try:
        req_lib.post(f"https://sctapi.ftqq.com/{SCKEY}.send",
                     data={"title": title, "desp": content}, timeout=10)
    except:
        pass


def close_popups(page):
    """关闭弹窗，返回是否关闭了"""
    closed = False
    for sel in ['.swal2-confirm', '.swal2-close', 'button:has-text("OK")',
                'button:has-text("确定")', 'button:has-text("知道了")',
                'button:has-text("关闭")', '.layui-layer-btn0']:
        try:
            for btn in page.query_selector_all(sel):
                if btn.is_visible():
                    btn.click(); closed = True; time.sleep(0.5)
        except:
            continue
    try:
        page.keyboard.press('Escape')
    except:
        pass
    return closed


def find_and_fill(page, selectors, value):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(); time.sleep(0.2); el.fill(""); el.fill(value)
                log(f"填写: {sel}", "OK"); return True
        except:
            continue
    return False


def find_and_click(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(); log(f"点击: {sel}", "OK"); return True
        except:
            continue
    return False


def wait_login(page, base_url, timeout=40):
    """等待登录成功 - 优先检测cookie（SPA不会跳转URL）"""
    start = time.time()
    while time.time() - start < timeout:
        # 方式1：cookie检测（最快，适合SPA）
        cookies = page.context.cookies()
        cookie_names = {c['name'] for c in cookies}
        if 'uid' in cookie_names and 'key' in cookie_names and 'email' in cookie_names:
            log("登录成功 (cookie)", "OK"); return True

        # 方式2：URL检测
        if '/user' in page.url and '/auth/login' not in page.url:
            log("登录成功 (URL)", "OK"); return True

        # 方式3：元素检测
        for sel in ['a[href*="logout"]', 'button:has-text("每日签到")', 'button:has-text("明日再来")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    log("登录成功 (元素)", "OK"); return True
            except:
                continue
        time.sleep(2)

    return False


def extract_checkin_result(page):
    """提取签到结果 - 过滤掉导航链接等干扰文本"""
    # 从弹窗获取
    for sel in ['.swal2-html-container', '.swal2-title', '.swal2-content']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                text = el.inner_text().strip()
                # 过滤掉导航类文字
                if text and len(text) < 200 and _is_checkin_text(text):
                    return text
        except:
            continue

    # 从页面文本匹配
    try:
        body = page.text_content('body') or ""
        for pattern in [
            r'签到成功.*?(\d+\.?\d*)\s*(GB|MB)',
            r'获得.*?(\d+\.?\d*)\s*(GB|MB)',
            r'已连续签到.*?(\d+)\s*天',
        ]:
            m = re.search(pattern, body)
            if m:
                return m.group(0).strip()

        if '今日已签到' in body or '已经签到' in body:
            return "今日已签到"
        if '签到成功' in body:
            return "签到成功"
    except:
        pass

    # 按钮变化
    try:
        if page.query_selector('button:has-text("明日再来")'):
            return "签到成功（按钮已变化）"
    except:
        pass

    return None


def _is_checkin_text(text):
    """判断文本是否是签到结果（排除导航链接等干扰）"""
    skip_keywords = ['下载客户端', '新手', '点我', '注册', '购买', '套餐', '教程',
                     'Telegram', '联系', '客服', '二维码']
    text_lower = text.lower()
    for kw in skip_keywords:
        if kw in text_lower:
            return False
    # 包含签到相关关键词
    checkin_keywords = ['签到', '获得', '流量', 'MB', 'GB', '成功', '已签', '连续']
    return any(kw in text for kw in checkin_keywords)


# ==================== 反检测注入 ====================

ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = { connect: function(){}, sendMessage: function(){} };
    window.chrome.loadTimes = function() { return {}; };
    window.chrome.csi = function() { return {}; };
    window.chrome.app = { isInstalled: false };
    Object.defineProperty(navigator, 'plugins', {
        get: () => { const a=[{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'PDF'}]; a.refresh=()=>{}; return a; }
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => [{type:'application/pdf',suffixes:'pdf',description:'PDF'}]
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    if (!navigator.connection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
        });
    }
    const gP = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return gP.call(this, p);
    };
"""


# ==================== GeeTest 处理 ====================

def handle_geetest(page):
    """处理 GeeTest v4 验证码"""
    log("检测 GeeTest v4...", "STEP")

    # 等待加载
    for i in range(15):
        loaded = page.evaluate("""
            () => {
                if (window.Captcha && typeof window.Captcha.isLoaded === 'function' && window.Captcha.isLoaded()) return true;
                if (document.querySelector('.geetest_holder, .geetest_panel, .embed-captcha canvas')) return true;
                return false;
            }
        """)
        if loaded:
            log("GeeTest 已加载", "OK"); break
        time.sleep(1)

    # 检查是否已通过
    if _geetest_ready(page):
        log("GeeTest 已通过", "OK"); return True

    # 点击验证按钮
    for sel in ['.geetest_btn_click', '.geetest_radar_tip', '.embed-captcha .geetest_btn_click']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(); log(f"点击 GeeTest: {sel}", "OK"); break
        except:
            continue

    # 等待通过
    for i in range(20):
        if _geetest_ready(page):
            log(f"GeeTest 通过! ({(i + 1) * 2}s)", "OK"); return True
        time.sleep(2)

    take_screenshot(page, "geetest_not_passed")

    # Capsolver 兜底
    if CAPSOLVER_KEY:
        return _solve_geetest_capsolver(page)

    # JS注入兜底
    log("JS 注入绕过（服务端可能拒绝）...", "WARN")
    page.evaluate("""
        () => {
            window.geetestV4Result = {
                lot_number: 'bypass', captcha_output: 'bypass',
                pass_token: 'bypass', gen_time: Math.floor(Date.now() / 1000).toString()
            };
            if (window.Captcha) {
                window.Captcha.isReady = function() { return true; };
                window.Captcha.getResponse = function() { return window.geetestV4Result; };
            }
        }
    """)
    return True


def _geetest_ready(page):
    try:
        return page.evaluate("""
            () => {
                if (window.Captcha && typeof window.Captcha.isReady === 'function' && window.Captcha.isReady()) return true;
                if (window.geetestV4Result && window.geetestV4Result.lot_number) return true;
                return false;
            }
        """)
    except:
        return False


def _solve_geetest_capsolver(page):
    """Capsolver 解决 GeeTest"""
    captcha_id = page.evaluate("""
        () => {
            const m = document.documentElement.innerHTML.match(/captchaId['"]*\\s*:\\s*['"]([^'"]+)['"]/);
            return m ? m[1] : null;
        }
    """)
    if not captcha_id:
        log("无法提取 captchaId", "FAIL"); return False

    log(f"Capsolver: 解决 GeeTest (id: {captcha_id[:16]}...)", "STEP")
    try:
        r = req_lib.post('https://api.capsolver.com/createTask', json={
            'clientKey': CAPSOLVER_KEY,
            'task': {'type': 'GeeTestV4', 'websiteURL': page.url, 'captchaId': captcha_id}
        }, timeout=30)
        resp = r.json()
        task_id = resp.get('taskId')
        if not task_id:
            log(f"Capsolver 创建失败: {resp}", "FAIL"); return False

        for i in range(60):
            time.sleep(2)
            r = req_lib.post('https://api.capsolver.com/getTaskResult', json={
                'clientKey': CAPSOLVER_KEY, 'taskId': task_id
            }, timeout=30)
            resp = r.json()
            if resp.get('status') == 'ready':
                solution = resp.get('solution', {})
                log("Capsolver 解决成功!", "OK")
                page.evaluate("(sol) => { window.geetestV4Result=sol; if(window.Captcha){window.Captcha.getResponse=()=>sol;window.Captcha.isReady=()=>true;} }", solution)
                return True
            if resp.get('status') == 'failed':
                log(f"Capsolver 失败: {resp}", "FAIL"); return False
        log("Capsolver 超时", "FAIL")
    except Exception as e:
        log(f"Capsolver 异常: {e}", "FAIL")
    return False


# ==================== API 签到 ====================

def api_checkin(base_url, cookies_dict):
    """用浏览器 cookies 调 API 签到"""
    session = req_lib.Session()
    session.cookies.update(cookies_dict)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'{base_url}/user',
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    })

    for ep in ['/user/checkin', '/api/v1/user/checkin', '/api/user/checkin',
               '/user/checkin/post', '/user/checkin/ajax']:
        try:
            r = session.post(f'{base_url}{ep}', timeout=10, allow_redirects=False)
            if r.status_code == 200:
                try:
                    d = r.json()
                    msg = d.get('msg', d.get('data', ''))
                    if d.get('ret') == 1 or d.get('success'):
                        return True, msg or '签到成功'
                    if '已签到' in str(msg):
                        return True, str(msg)
                    if msg:
                        return False, str(msg)
                except json.JSONDecodeError:
                    pass
            elif r.status_code == 302 and 'login' in r.headers.get('Location', ''):
                return False, "Cookie过期"
        except:
            continue
    return False, "API均404"


# ==================== 自动探测登录页 ====================

def navigate_to_login(page, base_url):
    log(f"访问 {base_url}", "STEP")
    try:
        resp = page.goto(base_url, wait_until='domcontentloaded', timeout=60000)
        log(f"HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        log(f"访问异常: {e}", "FAIL"); return False

    time.sleep(5)

    # Cloudflare
    for i in range(12):
        if "Just a moment" in page.title():
            log(f"Cloudflare... ({(i + 1) * 5}s)"); time.sleep(5)
        else:
            break

    # 检查登录表单
    if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
        return True

    # 尝试其他路径
    for path in ['/auth/login', '/#/auth/login', '/login', '/#/login']:
        try:
            page.goto(f"{base_url}{path}", wait_until='domcontentloaded', timeout=20000)
            time.sleep(3)
            if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
                return True
        except:
            continue
    take_screenshot(page, "login_not_found")
    return False


# ==================== 主签到流程 ====================

def sign_account(index, email, password):
    log(f"\n{'=' * 25} 账号 {index + 1} {'=' * 25}")
    log(f"账号: {email[:3]}***@{email.split('@')[-1] if '@' in email else '***'}")

    result_msg = ""
    success = False

    with sync_playwright() as p:
        has_display = bool(os.environ.get('DISPLAY'))
        headless = not has_display
        log(f"模式: {'有头(xvfb)' if not headless else '无头'}")

        browser = p.chromium.launch(
            headless=headless,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                  '--disable-gpu', '--no-first-run', '--disable-blink-features=AutomationControlled',
                  '--window-size=1920,1080']
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN', timezone_id='Asia/Shanghai',
            extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'},
        )
        context.add_init_script(ANTI_DETECT_SCRIPT)
        page = context.new_page()

        try:
            # 1. 导航到登录页
            if not navigate_to_login(page, URL):
                raise Exception("未找到登录页")
            take_screenshot(page, f"acct{index}_01")

            # 2. 填写表单
            log("填写登录信息...", "STEP")
            if not find_and_fill(page, ['input[name="email"]', 'input[type="email"]', '#email',
                                        'input[placeholder*="邮箱"]'], email):
                raise Exception("未找到邮箱输入框")
            if not find_and_fill(page, ['input[name="passwd"]', 'input[name="password"]',
                                        'input[type="password"]', '#passwd', '#password'], password):
                raise Exception("未找到密码输入框")

            # 3. GeeTest 验证
            if not handle_geetest(page):
                raise Exception("GeeTest 验证未通过")

            take_screenshot(page, f"acct{index}_02_geetest")
            time.sleep(1)
            close_popups(page)

            # 4. 点击登录
            log("点击登录...", "STEP")
            if not find_and_click(page, ['button[type="submit"]', 'button:has-text("登录")',
                                         'button:has-text("Login")', '.btn-login']):
                page.keyboard.press('Enter')

            time.sleep(3)
            close_popups(page)
            take_screenshot(page, f"acct{index}_03_after_login")

            # 5. 等待登录
            if not wait_login(page, URL, timeout=40):
                # 最后尝试访问用户中心
                log("尝试直接访问用户中心...", "WARN")
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_popups(page)
                if not wait_login(page, URL, timeout=10):
                    raise Exception("登录失败")

            take_screenshot(page, f"acct{index}_04_logged_in")

            # 6. 确保在用户中心
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_popups(page)

            # 7. 签到
            log("执行签到...", "STEP")
            take_screenshot(page, f"acct{index}_05_user")

            # 优先 API 签到
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            log(f"Cookies: {[k for k in cookie_dict if k in ('uid','email','key','expire_in')]}")

            api_ok, api_msg = api_checkin(URL, cookie_dict)
            if api_ok:
                result_msg = api_msg
                success = True
                log(f"API签到: {api_msg}", "OK")
            else:
                log(f"API签到: {api_msg}，尝试页面签到...", "WARN")

                # 页面签到
                if not find_and_click(page, [
                    'button:has-text("每日签到")', 'a:has-text("每日签到")',
                    'button:has-text("签到")', '#checkin-btn', '.checkin-btn',
                    'button[onclick*="checkin"]', '[lay-filter="checkin"]'
                ]):
                    # JS 兜底
                    js_result = page.evaluate("""
                        async () => {
                            const btns = document.querySelectorAll('button, a');
                            for (let btn of btns) {
                                const t = btn.textContent.trim();
                                if (t.includes('每日签到') || t === '签到') {
                                    btn.click(); return 'clicked:' + t;
                                }
                            }
                            try {
                                const r = await fetch('/user/checkin', {
                                    method:'POST', headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
                                    credentials:'same-origin'
                                });
                                return await r.text();
                            } catch(e) { return 'ERROR:' + e.message; }
                        }
                    """)
                    log(f"JS签到: {js_result[:100]}")
                    try:
                        if isinstance(js_result, str) and '{' in js_result:
                            d = json.loads(js_result)
                            if d.get('ret') == 1:
                                result_msg = d.get('msg', '签到成功'); success = True
                            elif d.get('msg') and ('已签到' in d['msg'] or '签到成功' in d['msg']):
                                result_msg = d['msg']; success = True
                    except:
                        pass

                # 等待并提取结果
                if not success:
                    time.sleep(3)
                    close_popups(page)

                    # 可能第一次点击触发弹窗，关闭后再检查
                    result_msg = extract_checkin_result(page)

                    # 如果没获取到，再等一会重试
                    if not result_msg:
                        time.sleep(3)
                        result_msg = extract_checkin_result(page)

                    if result_msg:
                        success = True
                    else:
                        # 最后检查按钮状态
                        try:
                            btn = page.query_selector('button:has-text("明日再来")')
                            if btn and btn.is_visible():
                                result_msg = "签到成功"; success = True
                            else:
                                result_msg = "签到已执行，未获取到结果文本"
                                success = True  # 保守认为成功
                        except:
                            pass

            close_popups(page)
            take_screenshot(page, f"acct{index}_06_final")

        except Exception as e:
            result_msg = str(e)[:200]
            success = False
            log(f"异常: {e}", "FAIL")
            take_screenshot(page, f"acct{index}_error")
        finally:
            try:
                context.close()
                browser.close()
            except:
                pass

    if success:
        return f"账号 {email}: ✅ {result_msg}"
    else:
        return f"账号 {email}: ❌ {result_msg}"


# ==================== 入口 ====================

if __name__ == '__main__':
    if not URL:
        print("❌ URL 未配置"); sys.exit(1)

    accounts = get_accounts()
    if not accounts:
        sys.exit(1)

    log("=" * 55)
    log(f"  iKuuu 机场自动签到 v3.1")
    log(f"  目标: {URL}")
    log(f"  共 {len(accounts)} 个账号")
    log("=" * 55)

    results = []
    for idx, (email, pwd) in enumerate(accounts):
        results.append(sign_account(idx, email, pwd))
        if idx < len(accounts) - 1:
            time.sleep(random.randint(30, 60))

    if SCKEY and results:
        push_notification("机场签到", "## 📊 签到结果\n\n" + "\n\n".join(f"- {r}" for r in results))

    log("\n" + "=" * 55)
    for r in results:
        log(r, "OK" if "✅" in r else "FAIL")
    log("=" * 55)
    log("🏁 完成")

    if all("❌" in r for r in results):
        sys.exit(1)
