#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 - 增强版 v2.1
核心改进：
  1. 自动探测登录页路径（不再硬编码 /auth/login）
  2. xvfb 有头模式绕过 Turnstile 检测
  3. 登录后优先 API 签到（最稳定）
  4. 全面反检测注入（12项）
  5. 每步截图，上传 Artifact 可查看
"""

import os
import sys
import time
import random
import re
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== 环境变量 ====================
URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')

SCREENSHOT_DIR = Path("debug")


# ==================== 账号解析 ====================

def get_accounts():
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG格式错误")
            return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i+1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置账号")
    return accounts


# ==================== 工具函数 ====================

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icon = {"INFO": "🔹", "OK": "✅", "FAIL": "❌", "WARN": "⚠️", "STEP": "👉"}.get(level, "  ")
    print(f"[{ts}] {icon} {msg}", flush=True)


def take_screenshot(page, name):
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=False)
        log(f"截图: {path}")
    except Exception as e:
        log(f"截图失败: {e}", "WARN")


def push_notification(title, content):
    if not SCKEY:
        log("未配置 SCKEY，跳过推送")
        return
    try:
        import requests
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send",
                          data={"title": title, "desp": content}, timeout=10)
        if res.status_code == 200:
            log("推送成功", "OK")
        else:
            log(f"推送失败: HTTP {res.status_code}", "WARN")
    except Exception as e:
        log(f"推送异常: {e}", "WARN")


def close_all_popups(page):
    closed = 0
    selectors = [
        '.swal2-confirm', '.swal2-close', '.swal2-deny',
        'button:has-text("OK")', 'button:has-text("Ok")',
        'button:has-text("确定")', 'button:has-text("确认")', 'button:has-text("知道了")',
        'button:has-text("关闭")', 'button:has-text("Close")', 'button:has-text("Got it")',
        'button:has-text("我已知晓")',
        '.layui-layer-btn0', '.layui-layer-close1', '.layui-layer-close',
        '.btn-close', '.modal .btn-primary',
        '[class*="confirm-btn"]', '[class*="close-btn"]',
    ]
    for sel in selectors:
        try:
            buttons = page.query_selector_all(sel)
            for btn in buttons:
                try:
                    if btn.is_visible():
                        btn.click()
                        closed += 1
                        log(f"关闭弹窗: {sel}")
                        time.sleep(0.8)
                except:
                    continue
        except:
            continue
    try:
        page.evaluate("""
            () => {
                try { if (typeof Swal !== 'undefined') Swal.close(); } catch(e) {}
                try { if (typeof layer !== 'undefined') layer.closeAll(); } catch(e) {}
            }
        """)
    except:
        pass
    try:
        page.keyboard.press('Escape')
        time.sleep(0.3)
    except:
        pass
    return closed


def is_logged_in(page):
    try:
        if '/user' in page.url and '/auth/login' not in page.url:
            return True
        if page.query_selector('a[href="/user/logout"], a[href*="logout"]'):
            return True
        if page.query_selector('.user-avatar, .user-info, #user-center'):
            return True
        if page.query_selector('button:has-text("每日签到"), button:has-text("明日再来"), #checkin-btn'):
            return True
        return False
    except:
        return False


def wait_for_turnstile_complete(page, timeout=120):
    log("等待 Turnstile 验证...", "STEP")
    start = time.time()
    while time.time() - start < timeout:
        try:
            token = page.evaluate("""
                () => {
                    const sels = [
                        'input[name="cf-turnstile-response"]',
                        'input[name*="turnstile"]',
                        'textarea[name="cf-turnstile-response"]',
                        '[name="cf-turnstile-response"]',
                    ];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el && el.value && el.value.length > 20) return el.value;
                    }
                    try {
                        if (window.turnstile) {
                            const containers = document.querySelectorAll('.cf-turnstile');
                            for (const container of containers) {
                                const widgetId = container.dataset.widgetId;
                                if (widgetId) {
                                    const resp = turnstile.getResponse(widgetId);
                                    if (resp && resp.length > 20) return resp;
                                }
                            }
                            if (typeof turnstile.getResponse === 'function') {
                                const resp = turnstile.getResponse();
                                if (resp && resp.length > 20) return resp;
                            }
                        }
                    } catch(e) {}
                    return null;
                }
            """)
            if token and len(token) > 20:
                log(f"Turnstile 完成! (token: {len(token)} 字符, 耗时 {int(time.time()-start)}s)", "OK")
                return True
        except Exception as e:
            log(f"Turnstile 检查异常: {e}", "WARN")
        elapsed = int(time.time() - start)
        if elapsed % 15 == 0 and elapsed > 0:
            log(f"Turnstile 等待中... ({elapsed}s)")
            take_screenshot(page, f"turnstile_wait_{elapsed}s")
        time.sleep(3)
    log("Turnstile 等待超时，尝试继续", "WARN")
    return False


def api_checkin(base_url, cookies_dict):
    import requests
    session = requests.Session()
    session.cookies.update(cookies_dict)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Referer': f'{base_url}/user',
        'Origin': base_url,
        'Accept': 'application/json, text/plain, */*',
    })
    endpoints = [
        '/user/checkin',
        '/api/v1/user/checkin',
        '/api/user/checkin',
        '/user/checkin/post',
    ]
    for endpoint in endpoints:
        try:
            url = f"{base_url}{endpoint}"
            log(f"尝试 API: POST {url}")
            r = session.post(url, timeout=15, allow_redirects=False)
            if r.status_code == 200:
                try:
                    data = r.json()
                    log(f"API 响应: {json.dumps(data, ensure_ascii=False)}")
                    msg = data.get('msg', data.get('data', ''))
                    if data.get('ret') == 1 or data.get('success') is True:
                        return True, msg or '签到成功'
                    if msg and ('已签到' in msg or '已经签到' in msg or 'already' in msg.lower()):
                        return True, msg
                    if msg:
                        return False, msg
                except json.JSONDecodeError:
                    log(f"非 JSON: {r.text[:200]}", "WARN")
            elif r.status_code == 302:
                loc = r.headers.get('Location', '')
                log(f"302 → {loc}", "WARN")
                if '/auth/login' in loc or '/login' in loc:
                    return False, "Cookie 已过期"
            else:
                log(f"HTTP {r.status_code}", "WARN")
        except Exception as e:
            log(f"API 请求异常: {e}", "WARN")
    return False, "所有 API 端点均失败"


# ==================== 反检测注入 ====================

ANTI_DETECT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = { connect: function(){}, sendMessage: function(){} };
    window.chrome.loadTimes = function() { return {}; };
    window.chrome.csi = function() { return {}; };
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            arr.refresh = () => {};
            return arr;
        }
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => [
            { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        ]
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    if (!navigator.connection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
        });
    }
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(parameters)
    );
    const origToString = Function.prototype.toString;
    const marked = new Set();
    Function.prototype.toString = function() {
        if (marked.has(this)) return 'function ' + (this.name || '') + '() { [native code] }';
        return origToString.call(this);
    };
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParam.call(this, p);
    };
"""


# ==================== 核心改动：自动探测登录页 ====================

def navigate_to_login(page, base_url):
    """
    自动探测登录页路径，不再硬编码 /auth/login
    SSPANEL 不同版本/主题的登录路径各不相同：
      - /auth/login
      - /#/auth/login （SPA hash 路由）
      - /login
      - /#login
      - 主页直接有登录表单
    """
    
    # 所有可能的登录路径，按优先级排列
    login_paths = [
        '/auth/login',
        '/#/auth/login',
        '/#/login',
        '/login',
        '/auth',
        '/#login',
        '/user/login',
    ]
    
    # ========== 第一步：先访问主页，查看页面上是否有登录链接 ==========
    log(f"先访问主页 {base_url} 探测登录入口...", "STEP")
    try:
        resp = page.goto(base_url, wait_until='domcontentloaded', timeout=60000)
        log(f"主页 HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        log(f"访问主页异常: {e}", "WARN")
    
    time.sleep(5)
    
    # 检查是否已经是登录页（主页直接就是登录表单）
    login_form = page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]')
    if login_form:
        log("主页直接包含登录表单!", "OK")
        return True
    
    # 从主页提取所有登录相关链接
    login_links = page.evaluate("""
        () => {
            const links = [];
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href || '';
                const text = (a.textContent || '').trim().toLowerCase();
                if (href.includes('login') || href.includes('auth') || 
                    text.includes('登录') || text.includes('login') || text.includes('sign in')) {
                    links.push({ href: href, text: text.substring(0, 30) });
                }
            });
            return links;
        }
    """)
    
    if login_links:
        log(f"主页发现 {len(login_links)} 个登录相关链接:", "OK")
        for link in login_links:
            log(f"  → {link['href']} (文字: {link['text']})")
        
        # 优先点击包含"登录"文字的链接
        for link in login_links:
            href = link['href']
            text = link['text']
            if '登录' in text or 'login' in text.lower() or 'sign' in text.lower():
                try:
                    log(f"点击登录链接: {href}")
                    page.goto(href, wait_until='domcontentloaded', timeout=30000)
                    time.sleep(3)
                    # 检查是否有登录表单
                    if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
                        log("成功到达登录页!", "OK")
                        return True
                except:
                    continue
    
    # ========== 第二步：逐个尝试常见登录路径 ==========
    log("逐个尝试常见登录路径...", "STEP")
    
    for path in login_paths:
        full_url = f"{base_url}{path}"
        log(f"尝试: {full_url}")
        try:
            resp = page.goto(full_url, wait_until='domcontentloaded', timeout=20000)
            status = resp.status if resp else 0
            log(f"  HTTP {status}")
            
            time.sleep(3)
            
            # 检查是否是404
            if status == 404:
                log(f"  404，跳过")
                continue
            
            # 检查页面标题是否包含404
            title = page.title()
            if '404' in title:
                log(f"  页面404，跳过")
                continue
            
            # 检查页面内容是否有登录表单
            page_text = page.text_content('body') or ''
            
            # 检查是否有邮箱/密码输入框
            has_email = page.query_selector('input[name="email"], input[type="email"], input[placeholder*="邮箱"], input[placeholder*="email" i]')
            has_passwd = page.query_selector('input[name="passwd"], input[name="password"], input[type="password"]')
            
            if has_email or has_passwd:
                log(f"找到登录页! 路径: {path}", "OK")
                take_screenshot(page, "login_page_found")
                return True
            
            # 如果是 SPA 的 hash 路由，页面可能需要加载
            if '#' in path:
                time.sleep(3)
                has_email = page.query_selector('input[name="email"], input[type="email"], input[placeholder*="邮箱"]')
                has_passwd = page.query_selector('input[name="passwd"], input[name="password"], input[type="password"]')
                if has_email or has_passwd:
                    log(f"找到登录页! 路径: {path}", "OK")
                    take_screenshot(page, "login_page_found")
                    return True
                    
        except Exception as e:
            log(f"  异常: {e}", "WARN")
            continue
    
    # ========== 第三步：回到主页，尝试点击页面中的登录按钮 ==========
    log("尝试从主页点击登录按钮...", "STEP")
    try:
        page.goto(base_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        close_all_popups(page)
        
        # 尝试点击登录按钮/链接
        login_btn_selectors = [
            'a:has-text("登录")',
            'a:has-text("Login")',
            'button:has-text("登录")',
            'a:has-text("Sign In")',
            'a[href*="login"]',
            'a[href*="auth"]',
            '.login-btn',
            '#login-btn',
            'a.nav-link:has-text("登录")',
        ]
        
        for sel in login_btn_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    log(f"点击: {sel}")
                    el.click()
                    time.sleep(5)
                    
                    if page.query_selector('input[name="email"], input[type="email"], input[name="passwd"], input[type="password"]'):
                        log("成功到达登录页!", "OK")
                        take_screenshot(page, "login_page_found")
                        return True
            except:
                continue
    except:
        pass
    
    # ========== 最终：截图当前页面，返回失败 ==========
    take_screenshot(page, "login_page_not_found")
    
    # 打印当前页面所有链接，辅助调试
    all_links = page.evaluate("""
        () => {
            return Array.from(document.querySelectorAll('a[href]')).slice(0, 50).map(a => ({
                href: a.href,
                text: (a.textContent || '').trim().substring(0, 30)
            }));
        }
    """)
    log("页面上所有链接:", "WARN")
    for link in all_links:
        log(f"  {link['href']}  ({link['text']})", "WARN")
    
    return False


# ==================== 主签到流程 ====================

def sign_account(index, email, password):
    log(f"\n{'='*25} 账号 {index+1} {'='*25}")
    log(f"账号: {email[:3]}***@{email.split('@')[-1] if '@' in email else '***'}")
    
    result_msg = ""
    success = False
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    
    with sync_playwright() as p:
        has_display = bool(os.environ.get('DISPLAY'))
        headless = not has_display
        log(f"显示环境: {'xvfb' if has_display else '无'} → {'有头' if not headless else '无头'}模式")
        
        browser = p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1920,1080',
                '--disable-features=SitePerProcess,IsolateOrigins',
            ]
        )
        
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'},
        )
        
        context.add_init_script(ANTI_DETECT_SCRIPT)
        page = context.new_page()
        
        try:
            # ====== 1. 自动探测并导航到登录页 ======
            log("探测登录页...", "STEP")
            found_login = navigate_to_login(page, URL)
            
            if not found_login:
                raise Exception("未找到登录页，请检查 URL 是否正确，或网站是否改版")
            
            take_screenshot(page, f"acct{index}_01_login_page")
            
            # ====== 2. 等待 Cloudflare ======
            log("检查 Cloudflare 验证...", "STEP")
            cf_passed = False
            for i in range(24):
                title = page.title()
                if "Just a moment" in title or "Checking" in title:
                    if (i + 1) % 3 == 0:
                        log(f"Cloudflare 验证中... ({(i+1)*5}s)")
                    time.sleep(5)
                else:
                    cf_passed = True
                    log(f"Cloudflare 通过! (标题: {title})", "OK")
                    break
            
            if not cf_passed:
                log("Cloudflare 超时，继续尝试...", "WARN")
            
            time.sleep(3)
            close_all_popups(page)
            
            # ====== 3. 检查是否已登录 ======
            if is_logged_in(page):
                log("已是登录状态", "OK")
                if '/user' not in page.url:
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(5)
                    close_all_popups(page)
            else:
                # ====== 4. 填写登录信息 ======
                log("填写登录信息...", "STEP")
                
                email_filled = False
                for sel in ['input[name="email"]', 'input[type="email"]', '#email',
                           'input[placeholder*="邮箱"]', 'input[placeholder*="email" i]',
                           'input[placeholder*="Email"]']:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(0.2)
                            el.fill("")
                            el.fill(email)
                            log(f"填写邮箱: {sel}", "OK")
                            email_filled = True
                            break
                    except:
                        continue
                
                if not email_filled:
                    inputs_info = page.evaluate("""
                        () => Array.from(document.querySelectorAll('input'))
                            .map(el => `${el.name||'-'} | ${el.type} | ${el.placeholder||'-'} | vis=${el.offsetParent!==null}`)
                    """)
                    log("页面 input 元素:", "WARN")
                    for info in inputs_info:
                        log(f"  {info}", "WARN")
                    raise Exception("未找到邮箱输入框")
                
                pwd_filled = False
                for sel in ['input[name="passwd"]', 'input[name="password"]', 'input[type="password"]',
                           '#passwd', '#password', 'input[placeholder*="密码"]', 'input[placeholder*="password" i]']:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            time.sleep(0.2)
                            el.fill("")
                            el.fill(password)
                            log(f"填写密码: {sel}", "OK")
                            pwd_filled = True
                            break
                    except:
                        continue
                
                if not pwd_filled:
                    raise Exception("未找到密码输入框")
                
                take_screenshot(page, f"acct{index}_03_form_filled")
                
                # ====== 5. 等待 Turnstile ======
                turnstile_ok = wait_for_turnstile_complete(page, timeout=120)
                if not turnstile_ok:
                    log("Turnstile 未通过，继续尝试...", "WARN")
                    take_screenshot(page, f"acct{index}_turnstile_timeout")
                
                time.sleep(3)
                close_all_popups(page)
                time.sleep(1)
                take_screenshot(page, f"acct{index}_04_after_turnstile")
                
                # ====== 6. 点击登录 ======
                log("点击登录...", "STEP")
                
                login_clicked = False
                for sel in ['button[type="submit"]', 'button:has-text("登录")', 'button:has-text("登 录")',
                           'button:has-text("Login")', 'button:has-text("Sign In")',
                           '#login-btn', '.btn-login', 'input[type="submit"]']:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            log(f"点击登录: {sel}", "OK")
                            login_clicked = True
                            break
                    except:
                        continue
                
                if not login_clicked:
                    log("未找到登录按钮，尝试回车提交", "WARN")
                    page.keyboard.press('Enter')
                
                time.sleep(5)
                close_all_popups(page)
                time.sleep(1)
                take_screenshot(page, f"acct{index}_05_after_login")
                
                # ====== 7. 确认登录 ======
                log("确认登录状态...", "STEP")
                logged_in = False
                
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    logged_in = True
                    log("登录成功 (URL)", "OK")
                except PlaywrightTimeout:
                    pass
                
                if not logged_in:
                    try:
                        page.wait_for_selector('a[href="/user/logout"], a[href*="logout"], #checkin-btn, button:has-text("每日签到")',
                                             timeout=20000)
                        logged_in = True
                        log("登录成功 (元素)", "OK")
                    except PlaywrightTimeout:
                        pass
                
                if not logged_in:
                    for _ in range(10):
                        if is_logged_in(page):
                            logged_in = True
                            log("登录成功 (轮询)", "OK")
                            break
                        time.sleep(2)
                
                if not logged_in:
                    log("登录状态不确定，直接访问用户中心...", "WARN")
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(3)
                    close_all_popups(page)
                    
                    if '/auth/login' in page.url or '/login' in page.url:
                        take_screenshot(page, f"acct{index}_error_login_failed")
                        raise Exception("登录失败 - 被重定向回登录页")
                    logged_in = True
                
                take_screenshot(page, f"acct{index}_06_logged_in")
            
            # ====== 8. 签到 ======
            log("执行签到...", "STEP")
            
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_all_popups(page)
            
            take_screenshot(page, f"acct{index}_07_user_panel")
            
            # 优先 API 签到
            cookies = context.cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            log(f"获取到 {len(cookie_dict)} 个 cookies: {list(cookie_dict.keys())}")
            
            api_ok, api_msg = api_checkin(URL, cookie_dict)
            if api_ok:
                result_msg = api_msg
                success = True
                log(f"API 签到成功: {api_msg}", "OK")
            else:
                log(f"API 签到未成功: {api_msg}，尝试页面签到...", "WARN")
                
                # 页面点击签到
                clicked = False
                for sel in [
                    'button:has-text("每日签到")',
                    'a:has-text("每日签到")',
                    'button:has-text("签到")',
                    'a:has-text("签到")',
                    '#checkin-btn',
                    '.checkin-btn',
                    'button[onclick*="checkin"]',
                    'a[onclick*="checkin"]',
                    '[lay-filter="checkin"]',
                    'button:has-text("Check in")',
                ]:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            log(f"点击签到: {sel}", "OK")
                            clicked = True
                            break
                    except:
                        continue
                
                if not clicked:
                    elements_info = page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('button, a, input[type="submit"], [onclick]');
                            return Array.from(btns)
                                .filter(b => b.offsetParent !== null)
                                .slice(0, 30)
                                .map(b => ({
                                    tag: b.tagName,
                                    text: b.textContent.trim().substring(0, 40),
                                    id: b.id || '-',
                                    cls: (b.className || '-').substring(0, 30),
                                }));
                        }
                    """)
                    log("页面上可见元素:", "WARN")
                    for info in elements_info:
                        log(f"  {info['tag']} text='{info['text']}' id='{info['id']}' class='{info['cls']}'", "WARN")
                    
                    # JS 兜底
                    log("JS 兜底签到...", "WARN")
                    try:
                        js_result = page.evaluate("""
                            async () => {
                                const btns = document.querySelectorAll('button, a');
                                for (let btn of btns) {
                                    const t = btn.textContent.trim();
                                    if (t.includes('每日签到') || t.includes('签到')) {
                                        btn.click();
                                        return 'clicked:' + t;
                                    }
                                }
                                if (typeof checkin === 'function') { checkin(); return 'function'; }
                                try {
                                    const r = await fetch('/user/checkin', {
                                        method: 'POST',
                                        headers: { 'Accept': 'application/json' },
                                        credentials: 'same-origin'
                                    });
                                    return await r.text();
                                } catch(e) { return 'ERROR: ' + e.message; }
                            }
                        """)
                        log(f"JS 签到结果: {js_result}")
                        try:
                            if isinstance(js_result, str) and js_result.startswith('{'):
                                data = json.loads(js_result)
                                if data.get('ret') == 1 or data.get('success') is True:
                                    result_msg = data.get('msg', '签到成功')
                                    success = True
                                elif data.get('msg'):
                                    result_msg = data['msg']
                                    success = '已签到' in result_msg or 'already' in result_msg.lower()
                        except:
                            pass
                    except Exception as e:
                        log(f"JS 签到异常: {e}", "WARN")
                else:
                    time.sleep(3)
                    close_all_popups(page)
                    take_screenshot(page, f"acct{index}_08_after_checkin")
                    
                    time.sleep(5)
                    
                    for sel in ['.swal2-html-container', '.swal2-content', '.swal2-title',
                                '.msg', '.alert', '.layui-layer-content',
                                '.toast-body', '.toast-message', '.noty_body']:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                text = el.inner_text().strip()
                                if text and len(text) < 200:
                                    result_msg = text
                                    log(f"弹窗结果: {text}", "OK")
                                    break
                        except:
                            continue
                    
                    if not result_msg:
                        try:
                            body = page.text_content('body') or ""
                            match = re.search(r'获得.*?(\d+\.?\d*)\s*(GB|MB)', body)
                            if match:
                                result_msg = f"签到成功，获得 {match.group(1)}{match.group(2)}"
                            elif '签到成功' in body:
                                result_msg = "签到成功"
                            elif '今日已签到' in body or '已经签到' in body:
                                result_msg = "今日已签到"
                        except:
                            pass
                    
                    if not result_msg:
                        try:
                            if page.query_selector('button:has-text("明日再来")'):
                                result_msg = "签到成功（按钮变为明日再来）"
                        except:
                            pass
                    
                    if result_msg:
                        success = True
                        log(f"页面签到结果: {result_msg}", "OK")
            
            close_all_popups(page)
            take_screenshot(page, f"acct{index}_09_final")
            
        except Exception as e:
            result_msg = str(e)[:200]
            success = False
            log(f"签到异常: {e}", "FAIL")
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
        print("❌ URL 未配置")
        sys.exit(1)
    
    accounts = get_accounts()
    if not accounts:
        sys.exit(1)
    
    log("=" * 55)
    log(f"  iKuuu 机场自动签到 v2.1")
    log(f"  目标: {URL}")
    log(f"  共 {len(accounts)} 个账号")
    log("=" * 55)
    
    results = []
    
    for idx, (email, pwd) in enumerate(accounts):
        results.append(sign_account(idx, email, pwd))
        if idx < len(accounts) - 1:
            wait = random.randint(30, 60)
            log(f"等待 {wait}s 后处理下一个账号...")
            time.sleep(wait)
    
    if SCKEY and results:
        summary = "## 📊 iKuuu 签到结果\n\n" + "\n\n".join(f"- {r}" for r in results)
        push_notification("机场签到", summary)
    
    log("\n" + "=" * 55)
    for r in results:
        log(r, "OK" if "✅" in r else "FAIL")
    log("=" * 55)
    log("🏁 完成")
    
    if all("❌" in r for r in results):
        sys.exit(1)
