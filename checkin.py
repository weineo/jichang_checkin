#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 v3.0

关键发现：该站使用 GeeTest v4（极验）验证码，非 Cloudflare Turnstile

策略优先级：
1. 纯 HTTP API 登录（不带验证码）
2. 浏览器点击 GeeTest + 等待无感通过
3. JS 注入绕过客户端检查
4. Capsolver API 解决验证码（需设置 CAPSOLVER_KEY）
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

# ==================== 环境变量 ====================
URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')
CAPSOLVER_KEY = os.environ.get('CAPSOLVER_KEY', '')

SCREENSHOT_DIR = Path("debug")


# ==================== 工具函数 ====================

def get_accounts():
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG格式错误")
            return []
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
        path = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=False)
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


def close_all_popups(page):
    closed = 0
    for sel in ['.swal2-confirm', '.swal2-close', '.swal2-deny',
                'button:has-text("OK")', 'button:has-text("确定")',
                'button:has-text("关闭")', 'button:has-text("知道了")',
                '.layui-layer-btn0', '.layui-layer-close1']:
        try:
            for btn in page.query_selector_all(sel):
                if btn.is_visible():
                    btn.click()
                    closed += 1
                    time.sleep(0.5)
        except:
            continue
    try:
        page.evaluate("() => { try{Swal.close()}catch(e){} try{if(typeof layer!=='undefined')layer.closeAll()}catch(e){} }")
    except:
        pass
    try:
        page.keyboard.press('Escape')
    except:
        pass
    return closed


def is_logged_in(page):
    try:
        if '/user' in page.url and '/auth/login' not in page.url and '/' != page.url.rstrip('/').split('/')[-1]:
            return True
        if page.query_selector('a[href="/user/logout"], a[href*="logout"]'):
            return True
        if page.query_selector('button:has-text("每日签到"), button:has-text("明日再来"), #checkin-btn'):
            return True
        return False
    except:
        return False


# ==================== 反检测注入 ====================

ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = { connect: function(){}, sendMessage: function(){} };
    window.chrome.loadTimes = function() { return {}; };
    window.chrome.csi = function() { return {}; };
    window.chrome.app = { isInstalled: false };
    Object.defineProperty(navigator, 'plugins', {
        get: () => { const a=[{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format'}]; a.refresh=()=>{}; return a; }
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => [{type:'application/pdf',suffixes:'pdf',description:'Portable Document Format'}]
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


# ==================== 策略1：纯 API 登录 ====================

def try_api_login_and_checkin(base_url, email, password):
    """
    纯 HTTP 登录+签到，不使用浏览器
    尝试不带验证码直接登录（某些 SSPANEL 不校验 captcha_result）
    """
    log("策略1: 纯 API 登录（不带验证码）...", "STEP")

    session = req_lib.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': base_url + '/',
        'Origin': base_url,
    })

    # 获取初始 cookies
    try:
        r = session.get(base_url, timeout=30)
        log(f"主页: HTTP {r.status_code}")
    except Exception as e:
        return False, f"无法访问主页: {e}"

    # 尝试登录
    for attempt_name, data in [
        ("无验证码", {
            'email': email, 'passwd': password, 'code': '',
            'remember_me': 'on',
        }),
        ("空验证码字段", {
            'email': email, 'passwd': password, 'code': '',
            'captcha_result[lot_number]': '', 'captcha_result[captcha_output]': '',
            'captcha_result[pass_token]': '', 'captcha_result[gen_time]': '',
            'remember_me': 'on',
        }),
    ]:
        try:
            log(f"  尝试: {attempt_name}")
            r = session.post(f'{base_url}/auth/login', data=data, timeout=15,
                             headers={'X-Requested-With': 'XMLHttpRequest'})
            log(f"  HTTP {r.status_code}: {r.text[:150]}")

            if r.status_code == 200:
                try:
                    resp = r.json()
                    if resp.get('ret') == 1:
                        log("API 登录成功!", "OK")
                        ok, msg = _api_checkin(base_url, session)
                        return ok, f"API登录+{msg}"
                    else:
                        msg = resp.get('msg', '')
                        if '验证' in msg or '人机' in msg or 'captcha' in msg.lower():
                            log(f"  服务端要求验证码: {msg}", "WARN")
                            return False, "服务端要求验证码"
                        log(f"  登录失败: {msg}", "WARN")
                except json.JSONDecodeError:
                    log(f"  非 JSON 响应", "WARN")
        except Exception as e:
            log(f"  异常: {e}", "WARN")

    return False, "API 登录失败"


def _api_checkin(base_url, session):
    """用已登录 session 签到"""
    for ep in ['/user/checkin', '/api/v1/user/checkin', '/api/user/checkin']:
        try:
            r = session.post(f'{base_url}{ep}', timeout=15, allow_redirects=False,
                             headers={'X-Requested-With': 'XMLHttpRequest'})
            if r.status_code == 200:
                try:
                    d = r.json()
                    msg = d.get('msg', '')
                    if d.get('ret') == 1 or d.get('success'):
                        return True, msg or '签到成功'
                    if '已签到' in msg:
                        return True, msg
                    if msg:
                        return False, msg
                except:
                    pass
            elif r.status_code == 302:
                return False, "Cookie过期"
        except:
            continue
    return False, "签到API均失败"


# ==================== 策略4：Capsolver 解决 GeeTest ====================

def solve_geetest_capsolver(captcha_id, page_url, api_key):
    """使用 Capsolver 解决 GeeTest v4"""
    log(f"Capsolver: 解决 GeeTest v4 (id: {captcha_id[:16]}...)", "STEP")

    try:
        r = req_lib.post('https://api.capsolver.com/createTask', json={
            'clientKey': api_key,
            'task': {
                'type': 'GeeTestV4',
                'websiteURL': page_url,
                'captchaId': captcha_id,
            }
        }, timeout=30)

        resp = r.json()
        if resp.get('errorId', -1) != 0:
            log(f"Capsolver 创建任务失败: {resp}", "FAIL")
            return None

        task_id = resp.get('taskId')
        if not task_id:
            return None

        log(f"Capsolver: 任务已创建，等待解决...")

        for i in range(60):
            time.sleep(2)
            r = req_lib.post('https://api.capsolver.com/getTaskResult', json={
                'clientKey': api_key,
                'taskId': task_id,
            }, timeout=30)

            resp = r.json()
            if resp.get('status') == 'ready':
                solution = resp.get('solution', {})
                log(f"Capsolver: 验证码已解决!", "OK")
                return solution
            elif resp.get('status') == 'failed':
                log(f"Capsolver: 解决失败: {resp}", "FAIL")
                return None

            if (i + 1) % 10 == 0:
                log(f"Capsolver: 等待中... ({(i + 1) * 2}s)")

        log("Capsolver: 超时", "FAIL")
        return None

    except Exception as e:
        log(f"Capsolver 异常: {e}", "FAIL")
        return None


# ==================== GeeTest v4 处理 ====================

def handle_geetest(page, base_url):
    """
    处理 GeeTest v4 验证码
    返回 True 表示可以继续登录，False 表示无法处理
    """
    log("检测 GeeTest v4 验证码...", "STEP")

    # 1. 等待 GeeTest 加载
    geetest_loaded = False
    for i in range(30):
        loaded = page.evaluate("""
            () => {
                if (window.Captcha && typeof window.Captcha.isLoaded === 'function' && window.Captcha.isLoaded()) return true;
                if (document.querySelector('.geetest_holder, .geetest_panel, .embed-captcha canvas')) return true;
                if (typeof initGeetest4 !== 'undefined') return true;
                return false;
            }
        """)
        if loaded:
            geetest_loaded = True
            log("GeeTest 已加载", "OK")
            break
        time.sleep(1)

    if not geetest_loaded:
        log("未检测到 GeeTest，可能不需要验证码", "WARN")
        return True

    # 2. 检查是否已经自动通过
    if _check_geetest_ready(page):
        log("GeeTest 已自动通过!", "OK")
        return True

    # 3. 策略2：点击 GeeTest 按钮，等待无感验证
    log("尝试点击 GeeTest 按钮（可能触发无感验证）...", "STEP")
    take_screenshot(page, "before_geetest_click")

    for sel in ['.geetest_btn_click', '.geetest_radar_tip', '.embed-captcha .geetest_btn_click',
                '.embed-captcha button', '.geetest_radar_tip_content',
                '.embed-captcha .geetest_commit']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                log(f"点击 GeeTest: {sel}", "OK")
                break
        except:
            continue

    # 等待验证码通过
    for i in range(30):
        if _check_geetest_ready(page):
            log(f"GeeTest 通过! (耗时 {(i + 1) * 2}s)", "OK")
            return True
        time.sleep(2)

    take_screenshot(page, "geetest_not_auto_passed")
    log("GeeTest 未自动通过", "WARN")

    # 4. 策略4：Capsolver
    if CAPSOLVER_KEY:
        log("使用 Capsolver 解决 GeeTest...", "STEP")

        captcha_id = page.evaluate("""
            () => {
                const html = document.documentElement.innerHTML;
                const m = html.match(/captchaId['"]*\\s*:\\s*['"]([^'"]+)['"]/);
                return m ? m[1] : null;
            }
        """)

        if captcha_id:
            log(f"captchaId: {captcha_id}")
            solution = solve_geetest_capsolver(captcha_id, page.url, CAPSOLVER_KEY)
            if solution:
                page.evaluate("""
                    (solution) => {
                        window.geetestV4Result = {
                            lot_number: solution.lot_number || '',
                            captcha_output: solution.captcha_output || '',
                            pass_token: solution.pass_token || '',
                            gen_time: solution.gen_time || ''
                        };
                        if (window.Captcha) {
                            window.Captcha.getResponse = function() { return window.geetestV4Result; };
                            window.Captcha.isReady = function() { return true; };
                        }
                    }
                """, solution)
                log("Capsolver 方案已注入", "OK")
                return True
        else:
            log("无法提取 captchaId", "FAIL")

    # 5. 策略3：JS 注入绕过（服务端可能拒绝，但值得一试）
    log("策略3: JS 注入绕过客户端检查（服务端可能拒绝）...", "WARN")
    page.evaluate("""
        () => {
            window.geetestV4Result = {
                lot_number: 'bypass',
                captcha_output: 'bypass',
                pass_token: 'bypass',
                gen_time: Math.floor(Date.now() / 1000).toString()
            };
            if (window.Captcha) {
                window.Captcha.isReady = function() { return true; };
                window.Captcha.getResponse = function() { return window.geetestV4Result; };
            }
        }
    """)
    log("JS 注入完成", "OK")
    return True


def _check_geetest_ready(page):
    """检查 GeeTest 是否已完成验证"""
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


# ==================== API 签到（用 cookies） ====================

def api_checkin(base_url, cookies_dict):
    """用浏览器 cookies 调 API 签到"""
    session = req_lib.Session()
    session.cookies.update(cookies_dict)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'{base_url}/user',
        'Origin': base_url,
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    })

    for ep in ['/user/checkin', '/api/v1/user/checkin', '/api/user/checkin', '/user/checkin/post']:
        try:
            r = session.post(f'{base_url}{ep}', timeout=15, allow_redirects=False)
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
                except:
                    pass
            elif r.status_code == 302 and 'login' in r.headers.get('Location', ''):
                return False, "Cookie过期"
        except:
            continue
    return False, "API签到均失败"


# ==================== 自动探测登录页 ====================

def navigate_to_login(page, base_url):
    """自动探测并导航到登录页"""
    log(f"访问 {base_url}", "STEP")
    try:
        resp = page.goto(base_url, wait_until='domcontentloaded', timeout=60000)
        log(f"HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        log(f"访问异常: {e}", "FAIL")
        return False

    time.sleep(5)

    # 检查 Cloudflare
    for i in range(12):
        title = page.title()
        if "Just a moment" in title:
            log(f"Cloudflare 验证中... ({(i + 1) * 5}s)")
            time.sleep(5)
        else:
            break

    # 检查是否有登录表单
    if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
        log("找到登录表单!", "OK")
        return True

    # 尝试其他路径
    for path in ['/auth/login', '/#/auth/login', '/login', '/#/login']:
        try:
            page.goto(f"{base_url}{path}", wait_until='domcontentloaded', timeout=20000)
            time.sleep(3)
            if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
                log(f"登录页: {path}", "OK")
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

    # ====== 策略1：纯 API 登录 ======
    api_ok, api_msg = try_api_login_and_checkin(URL, email, password)
    if api_ok:
        log(f"策略1成功: {api_msg}", "OK")
        return f"账号 {email}: ✅ {api_msg}"
    log(f"策略1失败: {api_msg}", "WARN")

    # ====== 策略2/3/4：浏览器登录 ======
    log("使用浏览器登录...", "STEP")

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
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'},
        )

        context.add_init_script(ANTI_DETECT_SCRIPT)
        page = context.new_page()

        try:
            # 1. 导航到登录页
            if not navigate_to_login(page, URL):
                raise Exception("未找到登录页")

            take_screenshot(page, f"acct{index}_01_login")

            # 2. 填写表单
            log("填写登录信息...", "STEP")
            email_filled = False
            for sel in ['input[name="email"]', 'input[type="email"]', '#email',
                        'input[placeholder*="邮箱"]', 'input[placeholder*="email" i]']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click(); time.sleep(0.2); el.fill(""); el.fill(email)
                        email_filled = True; log(f"邮箱: {sel}", "OK"); break
                except:
                    continue

            if not email_filled:
                raise Exception("未找到邮箱输入框")

            pwd_filled = False
            for sel in ['input[name="passwd"]', 'input[name="password"]', 'input[type="password"]',
                        '#passwd', '#password', 'input[placeholder*="密码"]']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click(); time.sleep(0.2); el.fill(""); el.fill(password)
                        pwd_filled = True; log(f"密码: {sel}", "OK"); break
                except:
                    continue

            if not pwd_filled:
                raise Exception("未找到密码输入框")

            take_screenshot(page, f"acct{index}_02_form")

            # 3. 处理 GeeTest 验证码
            geetest_ok = handle_geetest(page, URL)
            if not geetest_ok:
                raise Exception("GeeTest 验证码无法通过")

            take_screenshot(page, f"acct{index}_03_geetest")
            time.sleep(2)
            close_all_popups(page)

            # 4. 点击登录
            log("点击登录...", "STEP")
            login_clicked = False
            for sel in ['button[type="submit"]', 'button:has-text("登录")', 'button:has-text("Login")',
                        '#login-btn', '.btn-login', 'button.login']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click(); login_clicked = True; log(f"登录: {sel}", "OK"); break
                except:
                    continue

            if not login_clicked:
                page.keyboard.press('Enter')

            time.sleep(5)
            close_all_popups(page)
            take_screenshot(page, f"acct{index}_04_after_login")

            # 5. 确认登录
            log("确认登录状态...", "STEP")
            logged_in = False

            try:
                page.wait_for_url(f"{URL}/user*", timeout=15000)
                logged_in = True; log("登录成功(URL)", "OK")
            except PlaywrightTimeout:
                pass

            if not logged_in:
                try:
                    page.wait_for_selector(
                        'a[href*="logout"], button:has-text("每日签到"), button:has-text("明日再来"), #checkin-btn',
                        timeout=15000)
                    logged_in = True; log("登录成功(元素)", "OK")
                except PlaywrightTimeout:
                    pass

            if not logged_in:
                for _ in range(5):
                    if is_logged_in(page):
                        logged_in = True; log("登录成功(轮询)", "OK"); break
                    time.sleep(2)

            if not logged_in:
                # 直接尝试访问用户中心
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_all_popups(page)
                page_text = page.text_content('body') or ''
                if '登录' in page.title() or ('邮箱' in page_text and '密码' in page_text and '签到' not in page_text):
                    take_screenshot(page, f"acct{index}_login_failed")
                    # 检查是否有错误提示
                    for sel in ['.swal2-html-container', '.swal2-content', '.alert']:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                err_text = el.inner_text().strip()
                                if err_text:
                                    raise Exception(f"登录失败: {err_text}")
                        except:
                            continue
                    raise Exception("登录失败 - 可能是验证码被服务端拒绝")
                logged_in = True

            take_screenshot(page, f"acct{index}_05_logged_in")

            # 6. 签到
            log("执行签到...", "STEP")

            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_all_popups(page)

            take_screenshot(page, f"acct{index}_06_user")

            # 优先 API 签到
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            log(f"Cookies: {list(cookie_dict.keys())}")

            api_ok, api_msg = api_checkin(URL, cookie_dict)
            if api_ok:
                result_msg = api_msg
                success = True
                log(f"API签到成功: {api_msg}", "OK")
            else:
                log(f"API签到失败: {api_msg}，尝试页面签到...", "WARN")

                # 页面签到
                clicked = False
                for sel in ['button:has-text("每日签到")', 'a:has-text("每日签到")',
                            'button:has-text("签到")', '#checkin-btn', '.checkin-btn',
                            'button[onclick*="checkin"]', '[lay-filter="checkin"]']:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click(); clicked = True; log(f"点击签到: {sel}", "OK"); break
                    except:
                        continue

                if not clicked:
                    # JS 兜底
                    js_result = page.evaluate("""
                        async () => {
                            const btns = document.querySelectorAll('button, a');
                            for (let btn of btns) {
                                const t = btn.textContent.trim();
                                if (t.includes('每日签到') || t.includes('签到')) {
                                    btn.click(); return 'clicked:' + t;
                                }
                            }
                            if (typeof checkin === 'function') { checkin(); return 'function'; }
                            try {
                                const r = await fetch('/user/checkin', {
                                    method:'POST', headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
                                    credentials:'same-origin'
                                });
                                return await r.text();
                            } catch(e) { return 'ERROR:' + e.message; }
                        }
                    """)
                    log(f"JS签到: {js_result[:200]}")
                    try:
                        if isinstance(js_result, str) and '{' in js_result:
                            d = json.loads(js_result)
                            if d.get('ret') == 1:
                                result_msg = d.get('msg', '签到成功'); success = True
                            elif d.get('msg'):
                                result_msg = d['msg']
                                success = '已签到' in result_msg
                    except:
                        pass
                else:
                    time.sleep(3)
                    close_all_popups(page)
                    take_screenshot(page, f"acct{index}_07_checkin")

                    # 提取结果
                    for sel in ['.swal2-html-container', '.swal2-content', '.swal2-title',
                                '.msg', '.alert', '.layui-layer-content']:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                text = el.inner_text().strip()
                                if text and len(text) < 200:
                                    result_msg = text; break
                        except:
                            continue

                    if not result_msg:
                        try:
                            body = page.text_content('body') or ''
                            m = re.search(r'获得.*?(\d+\.?\d*)\s*(GB|MB)', body)
                            if m:
                                result_msg = f"签到成功，获得 {m.group(1)}{m.group(2)}"
                            elif '签到成功' in body:
                                result_msg = "签到成功"
                            elif '已签到' in body:
                                result_msg = "今日已签到"
                        except:
                            pass

                    if result_msg:
                        success = True
                    else:
                        try:
                            if page.query_selector('button:has-text("明日再来")'):
                                result_msg = "签到成功"; success = True
                        except:
                            pass

            close_all_popups(page)
            take_screenshot(page, f"acct{index}_08_final")

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
    log(f"  iKuuu 机场自动签到 v3.0")
    log(f"  目标: {URL}")
    log(f"  共 {len(accounts)} 个账号")
    log(f"  GeeTest: {'Capsolver' if CAPSOLVER_KEY else '免费策略'}")
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
