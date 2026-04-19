#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 - 增强版
基于原版改进：
  1. xvfb 有头模式绕过 Turnstile 检测
  2. 登录后优先 API 签到（最稳定）
  3. 全面反检测注入（12项）
  4. 每步截图，上传 Artifact 可查看
  5. 多选择器容错 + 自动列出页面元素辅助调试
"""

import os
import sys
import time
import random
import re
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== 环境变量（与你已配置的完全一致）====================
URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')

# 截图目录
SCREENSHOT_DIR = Path("debug")


# ==================== 账号解析（完全复用原逻辑）====================

def get_accounts():
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG格式错误，应为邮箱密码交替排列")
            return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i+1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置账号（需要 EMAIL+PASSWD 或 CONFIG）")
    return accounts


# ==================== 工具函数 ====================

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icon = {"INFO": "🔹", "OK": "✅", "FAIL": "❌", "WARN": "⚠️", "STEP": "👉"}.get(level, "  ")
    print(f"[{ts}] {icon} {msg}", flush=True)


def take_screenshot(page, name):
    """保存调试截图"""
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
    """关闭所有弹窗（强化版）"""
    closed = 0
    selectors = [
        '.swal2-confirm', '.swal2-close', '.swal2-deny',
        'button:has-text("OK")', 'button:has-text("Ok")', 'button:has-text("ok")',
        'button:has-text("确定")', 'button:has-text("确认")', 'button:has-text("知道了")',
        'button:has-text("关闭")', 'button:has-text("Close")', 'button:has-text("Got it")',
        'button:has-text("我已知晓")', 'button:has-text("取消")',
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
    
    # JS 关闭
    try:
        page.evaluate("""
            () => {
                try { if (typeof Swal !== 'undefined') Swal.close(); } catch(e) {}
                try { if (typeof layer !== 'undefined') layer.closeAll(); } catch(e) {}
            }
        """)
    except:
        pass
    
    # Escape
    try:
        page.keyboard.press('Escape')
        time.sleep(0.3)
    except:
        pass
    
    return closed


def is_logged_in(page):
    """检查是否已登录（增强版）"""
    try:
        if '/user' in page.url and '/auth/login' not in page.url:
            return True
        if page.query_selector('a[href="/user/logout"]'):
            return True
        if page.query_selector('a[href*="logout"]'):
            return True
        if page.query_selector('.user-avatar, .user-info, #user-center'):
            return True
        if page.query_selector('button:has-text("每日签到"), button:has-text("明日再来"), #checkin-btn'):
            return True
        # 检查导航栏中的用户相关元素
        if page.query_selector('a[href*="/user"], a[href*="/dashboard"]'):
            try:
                el = page.query_selector('a[href*="/user"]')
                if el and el.is_visible():
                    return True
            except:
                pass
        return False
    except:
        return False


def wait_for_turnstile_complete(page, timeout=120):
    """等待 Cloudflare Turnstile 验证完全完成"""
    log("等待 Turnstile 验证...", "STEP")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            token = page.evaluate("""
                () => {
                    // 方法1: 检查隐藏 input 中的 token
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
                    
                    // 方法2: 通过 turnstile API
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
                    
                    // 方法3: iframe 内部
                    try {
                        const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                        if (iframe) {
                            const inner = iframe.contentWindow?.document?.querySelector('input[name="cf-turnstile-response"]');
                            if (inner && inner.value && inner.value.length > 20) return inner.value;
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
    """用 cookies 直接调签到 API（最可靠的方式）"""
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
                    
                    # ret=0 但有 msg，可能是"已签到"
                    if msg and ('已签到' in msg or '已经签到' in msg or 'already' in msg.lower()):
                        return True, msg
                    
                    if msg:
                        return False, msg
                        
                except json.JSONDecodeError:
                    log(f"非 JSON: {r.text[:200]}", "WARN")
                    
            elif r.status_code == 302:
                loc = r.headers.get('Location', '')
                log(f"302 重定向 → {loc}", "WARN")
                if '/auth/login' in loc:
                    return False, "Cookie 已过期，需要重新登录"
            else:
                log(f"HTTP {r.status_code}", "WARN")
                
        except Exception as e:
            log(f"API 请求异常: {e}", "WARN")
    
    return False, "所有 API 端点均失败"


# ==================== 反检测注入脚本 ====================

ANTI_DETECT_SCRIPT = """
    // 1. 隐藏 webdriver 标识（最关键）
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. 模拟 Chrome 特有对象
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = { connect: function(){}, sendMessage: function(){} };
    window.chrome.loadTimes = function() { return {}; };
    window.chrome.csi = function() { return {}; };
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };

    // 3. 修正 plugins
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

    // 4. 修正 mimeTypes
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => {
            return [
                { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            ];
        }
    });

    // 5. 修正语言
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

    // 6. 修正平台
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

    // 7. 修正 hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

    // 8. 修正 deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

    // 9. 修正 connection
    if (!navigator.connection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
        });
    }

    // 10. Permissions API 修复
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(parameters)
    );

    // 11. 修复 toString 检测
    const origToString = Function.prototype.toString;
    const marked = new Set();
    Function.prototype.toString = function() {
        if (marked.has(this)) return 'function ' + (this.name || '') + '() { [native code] }';
        return origToString.call(this);
    };

    // 12. WebGL 渲染器修复
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParam.call(this, p);
    };
"""


# ==================== 主签到流程 ====================

def sign_account(index, email, password):
    log(f"\n{'='*25} 账号 {index+1} {'='*25}")
    log(f"账号: {email[:3]}***@{email.split('@')[-1] if '@' in email else '***'}")
    
    result_msg = ""
    success = False
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    
    with sync_playwright() as p:
        # ====== 启动浏览器 ======
        has_display = bool(os.environ.get('DISPLAY'))
        headless = not has_display
        log(f"显示环境: {'xvfb (' + os.environ.get('DISPLAY') + ')' if has_display else '无'} → {'有头' if not headless else '无头'}模式")
        
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
        
        # 全面反检测注入
        context.add_init_script(ANTI_DETECT_SCRIPT)
        
        page = context.new_page()
        
        try:
            # ====== 1. 访问登录页 ======
            login_url = f"{URL}/auth/login"
            log(f"访问 {login_url}", "STEP")
            
            resp = page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
            log(f"HTTP {resp.status if resp else 'N/A'}")
            time.sleep(5)
            take_screenshot(page, f"acct{index}_01_login_page")
            
            # ====== 2. 等待 Cloudflare ======
            log("检查 Cloudflare 验证...", "STEP")
            cf_passed = False
            for i in range(24):
                title = page.title()
                if "Just a moment" in title or "Checking" in title or "cloudflare" in title.lower():
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
            take_screenshot(page, f"acct{index}_02_after_cf")
            close_all_popups(page)
            
            # 检查是否已经登录
            if is_logged_in(page):
                log("已是登录状态", "OK")
                if '/user' not in page.url:
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(5)
                    close_all_popups(page)
            else:
                # ====== 3. 填写登录信息 ======
                log("填写登录信息...", "STEP")
                
                # 邮箱
                email_filled = False
                for sel in ['input[name="email"]', 'input[type="email"]', '#email',
                           'input[placeholder*="邮箱"]', 'input[placeholder*="email" i]']:
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
                    # 打印所有 input 帮助调试
                    inputs_info = page.evaluate("""
                        () => Array.from(document.querySelectorAll('input'))
                            .map(el => `${el.name||'-'} | ${el.type} | ${el.placeholder||'-'} | vis=${el.offsetParent!==null}`)
                    """)
                    log("页面 input 元素:", "WARN")
                    for info in inputs_info:
                        log(f"  {info}", "WARN")
                    raise Exception("未找到邮箱输入框")
                
                # 密码
                pwd_filled = False
                for sel in ['input[name="passwd"]', 'input[name="password"]', 'input[type="password"]',
                           '#passwd', '#password', 'input[placeholder*="密码"]']:
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
                
                # ====== 4. 等待 Turnstile ======
                turnstile_ok = wait_for_turnstile_complete(page, timeout=120)
                if not turnstile_ok:
                    log("Turnstile 验证未通过，继续尝试登录...", "WARN")
                    take_screenshot(page, f"acct{index}_turnstile_timeout")
                
                time.sleep(3)
                close_all_popups(page)
                time.sleep(1)
                take_screenshot(page, f"acct{index}_04_after_turnstile")
                
                # ====== 5. 点击登录 ======
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
                
                # ====== 6. 确认登录 ======
                log("确认登录状态...", "STEP")
                logged_in = False
                
                # URL 检测
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    logged_in = True
                    log("登录成功 (URL)", "OK")
                except PlaywrightTimeout:
                    pass
                
                # 元素检测
                if not logged_in:
                    try:
                        page.wait_for_selector('a[href="/user/logout"], a[href*="logout"], #checkin-btn, button:has-text("每日签到")', 
                                             timeout=20000)
                        logged_in = True
                        log("登录成功 (元素)", "OK")
                    except PlaywrightTimeout:
                        pass
                
                # 轮询检测
                if not logged_in:
                    for _ in range(10):
                        if is_logged_in(page):
                            logged_in = True
                            log("登录成功 (轮询)", "OK")
                            break
                        time.sleep(2)
                
                if not logged_in:
                    # 最后尝试直接访问用户中心
                    log("登录状态不确定，直接访问用户中心...", "WARN")
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(3)
                    close_all_popups(page)
                    
                    if '/auth/login' in page.url:
                        take_screenshot(page, f"acct{index}_error_login_failed")
                        raise Exception("登录失败 - 被重定向回登录页")
                    logged_in = True
                
                take_screenshot(page, f"acct{index}_06_logged_in")
            
            # ====== 7. 签到 ======
            log("执行签到...", "STEP")
            
            # 确保在用户中心
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                time.sleep(3)
                close_all_popups(page)
            
            take_screenshot(page, f"acct{index}_07_user_panel")
            
            # ✅ 优先方案：API 签到（最可靠）
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
                
                # 备选方案：页面点击签到
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
                    # 列出所有可见按钮帮助调试
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
                    
                    # 最后兜底：JS 直接触发
                    log("JS 兜底签到...", "WARN")
                    try:
                        js_result = page.evaluate("""
                            async () => {
                                // 查找按钮点击
                                const btns = document.querySelectorAll('button, a');
                                for (let btn of btns) {
                                    const t = btn.textContent.trim();
                                    if (t.includes('每日签到') || t.includes('签到')) {
                                        btn.click();
                                        return 'clicked:' + t;
                                    }
                                }
                                // 调用函数
                                if (typeof checkin === 'function') { checkin(); return 'function'; }
                                // fetch API
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
                            data = json.loads(js_result) if isinstance(js_result, str) and js_result.startswith('{') else None
                            if data:
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
                    # 点击后等待结果
                    time.sleep(3)
                    close_all_popups(page)
                    take_screenshot(page, f"acct{index}_08_after_checkin")
                    
                    # 提取签到结果
                    time.sleep(5)
                    
                    # 从弹窗获取
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
                    
                    # 从页面文本匹配
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
                    
                    # 按钮变化
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
    
    # 格式化结果
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
    log(f"  iKuuu 机场自动签到 v2.0")
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
    
    # 推送通知
    if SCKEY and results:
        summary = "## 📊 iKuuu 签到结果\n\n" + "\n\n".join(
            f"- {r}" for r in results
        )
        push_notification("机场签到", summary)
    
    log("\n" + "=" * 55)
    for r in results:
        log(r, "OK" if "✅" in r else "FAIL")
    log("=" * 55)
    log("🏁 完成")
    
    # 全部失败则退出码1
    if all("❌" in r for r in results):
        sys.exit(1)
