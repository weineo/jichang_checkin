#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iKuuu 机场自动签到 v4.0
================================================================
适配 2026-10 站点改版（旧版必挂的根因）：

1. 登录页新增 **GeeTest v4 交互式验证码**（文字点选 / 图标点选 / 九宫格 …）。
   旧的「点一下按钮等它自动通过」不再有效，服务端会拒绝伪造的验证结果。
2. 登录改为 **分阶段协议**：POST /auth/login
   字段：host / phase=password / captcha_result / email / passwd / remember_me / pageLoadedAt
   成功响应：{"phase":"authenticated"}（不再有 ret==1）
   其他阶段：totp(2FA) / email_code(邮箱验证码) / reverse_email_verify(反向邮箱验证)

策略：**保留 Playwright 浏览器流程**（cookie、分阶段、跳转全交给真实浏览器），
     只把验证码交给打码平台求真实解，再注入页面的 window.geetestV4Result。
     站点前端读取的就是这个全局变量（Captcha.getResponse() 内部读它），
     所以注入真解后页面自己的登录逻辑会带上合法 captcha_result。

打码平台（二选一，按存在的 Key 自动选择，也可用 CAPTCHA_PROVIDER 指定）：
  - Capsolver : 环境变量 CAPSOLVER_KEY
  - 2Captcha  : 环境变量 TWOCAPTCHA_KEY

可选：
  CAPTCHA_PROVIDER = auto | capsolver | 2captcha
  CAPTCHA_PROXY    = ip:port:user:pass   （当平台侧需要同 IP 时使用，走带代理的任务类型）
  CAPTCHA_ID       = 覆盖自动探测到的极验 captchaId
  SOLVE_TIMEOUT    = 单次求解超时秒数，默认 180
"""

import os
import sys
import time
import random
import re
import json
from pathlib import Path
from urllib.parse import urlparse

import requests as req_lib
from playwright.sync_api import sync_playwright

URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')

CAPSOLVER_KEY = os.environ.get('CAPSOLVER_KEY', '').strip()
TWOCAPTCHA_KEY = os.environ.get('TWOCAPTCHA_KEY', '').strip()
CAPTCHA_PROVIDER = (os.environ.get('CAPTCHA_PROVIDER', '') or 'auto').strip().lower()
CAPTCHA_PROXY = os.environ.get('CAPTCHA_PROXY', '').strip()
CAPTCHA_ID_OVERRIDE = os.environ.get('CAPTCHA_ID', '').strip()
CAPTCHA_ID_FALLBACK = 'cc96d05ba8b60f9112f76e18526fcb73'
SOLVE_TIMEOUT = int(os.environ.get('SOLVE_TIMEOUT', '180'))

SCREENSHOT_DIR = Path("debug")

API_BASE = 'https://api.capsolver.com'
TWOCAPTCHA_BASE = 'https://2captcha.com'


# ==================== 基础工具 ====================

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icon = {"INFO": "🔹", "OK": "✅", "FAIL": "❌", "WARN": "⚠️", "STEP": "👉"}.get(level, "  ")
    print(f"[{ts}] {icon} {msg}", flush=True)


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


def take_screenshot(page, name):
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=False)
    except Exception:
        pass


def push_notification(title, content):
    if not SCKEY:
        return
    try:
        req_lib.post(f"https://sctapi.ftqq.com/{SCKEY}.send",
                     data={"title": title, "desp": content}, timeout=10)
    except Exception:
        pass


def close_popups(page):
    closed = False
    for sel in ['.swal2-confirm', '.swal2-close', 'button:has-text("OK")',
                'button:has-text("确定")', 'button:has-text("知道了")',
                'button:has-text("关闭")', '.layui-layer-btn0']:
        try:
            for btn in page.query_selector_all(sel):
                if btn.is_visible():
                    btn.click(); closed = True; time.sleep(0.5)
        except Exception:
            continue
    return closed


def find_and_fill(page, selectors, value):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(); time.sleep(0.2); el.fill(""); el.fill(value)
                log(f"填写: {sel}", "OK"); return True
        except Exception:
            continue
    return False


def find_and_click(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(); log(f"点击: {sel}", "OK"); return True
        except Exception:
            continue
    return False


def get_swal_text(page):
    """读取 sweetalert 弹窗文字（错误提示如「登录流程无效…」都从这里出）"""
    for sel in ['.swal2-html-container', '.swal2-title', '.swal2-content', '.sweet-alert p']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                t = (el.inner_text() or '').strip()
                if t:
                    return t
        except Exception:
            continue
    return ""


# ==================== 打码平台：GeeTest v4 适配层 ====================

def _pick_provider():
    if CAPTCHA_PROVIDER in ('capsolver', '2captcha'):
        return CAPTCHA_PROVIDER
    if CAPSOLVER_KEY:
        return 'capsolver'
    if TWOCAPTCHA_KEY:
        return '2captcha'
    return None


def _normalize_solution(raw):
    """统一成 {lot_number, captcha_output, pass_token, gen_time}"""
    if not isinstance(raw, dict):
        return None
    out = {
        'lot_number': raw.get('lot_number'),
        'captcha_output': raw.get('captcha_output'),
        'pass_token': raw.get('pass_token'),
        'gen_time': raw.get('gen_time'),
    }
    if all(out.values()):
        return out
    return None


def _post_json(url, payload=None, form=False, timeout=30):
    """容错 JSON 请求：4xx/5xx 也尝试解析响应体，解析不了就抛出带状态码的错误
    form=True 时以 application/x-www-form-urlencoded 提交（2Captcha 传统接口要求）"""
    if form:
        r = req_lib.post(url, data=payload, timeout=timeout)
    else:
        r = req_lib.post(url, json=payload, timeout=timeout)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{url} 返回非 JSON（HTTP {r.status_code}）: {r.text[:200]}")


def _get_json(url, params=None, timeout=30):
    r = req_lib.get(url, params=params, timeout=timeout)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{url} 返回非 JSON（HTTP {r.status_code}）: {r.text[:200]}")


def _solve_capsolver(page_url, captcha_id):
    # 任务类型名各版本文档不一致，遇到「类型不支持」时自动换名重试
    type_candidates = ['GeeTestTask'] if CAPTCHA_PROXY else ['GeeTestTaskProxyLess', 'GeeTestTask']

    task_id = None
    last_err = None
    for ttype in type_candidates:
        task = {'type': ttype, 'websiteURL': page_url, 'captchaId': captcha_id}
        if CAPTCHA_PROXY:
            task['proxy'] = CAPTCHA_PROXY
        r = _post_json(f'{API_BASE}/createTask', {'clientKey': CAPSOLVER_KEY, 'task': task})
        if r.get('taskId'):
            task_id = r['taskId']
            break
        desc = str(r.get('errorDescription', ''))
        last_err = f"{r.get('errorCode')} {desc}"
        if 'type' in desc.lower():
            continue
        break
    if not task_id:
        raise RuntimeError(f"Capsolver 创建任务失败: {last_err}")

    deadline = time.time() + SOLVE_TIMEOUT
    while time.time() < deadline:
        time.sleep(3)
        rr = _post_json(f'{API_BASE}/getTaskResult',
                        {'clientKey': CAPSOLVER_KEY, 'taskId': task_id})
        st = rr.get('status')
        if st == 'ready':
            return _normalize_solution(rr.get('solution'))
        if rr.get('errorId'):
            raise RuntimeError(f"Capsolver 求解失败: {rr.get('errorCode')} {rr.get('errorDescription')}")
    raise RuntimeError('Capsolver 求解超时')


def _solve_2captcha(page_url, captcha_id):
    data = {
        'key': TWOCAPTCHA_KEY,
        'method': 'geetest',
        'version': 4,
        'pageurl': page_url,
        'initParameters': json.dumps({'captcha_id': captcha_id}),
        'json': 1,
    }
    if CAPTCHA_PROXY:
        data['proxy'] = CAPTCHA_PROXY
        data['proxytype'] = 'HTTP'

    r = _post_json(f'{TWOCAPTCHA_BASE}/in.php', data, form=True)
    if r.get('status') != 1:
        raise RuntimeError(f"2Captcha 创建任务失败: {r.get('request')}")
    task_id = r['request']

    time.sleep(15)
    deadline = time.time() + SOLVE_TIMEOUT
    while time.time() < deadline:
        rr = _get_json(f'{TWOCAPTCHA_BASE}/res.php',
                       {'key': TWOCAPTCHA_KEY, 'action': 'get', 'id': task_id, 'json': 1})
        if rr.get('status') == 1:
            return _normalize_solution(rr.get('request'))
        if rr.get('request') != 'CAPCHA_NOT_READY':
            raise RuntimeError(f"2Captcha 求解失败: {rr.get('request')}")
        time.sleep(5)
    raise RuntimeError('2Captcha 求解超时')


def solve_geetest_v4(page_url, captcha_id):
    provider = _pick_provider()
    if not provider:
        raise RuntimeError("未配置打码平台 Key（CAPSOLVER_KEY 或 TWOCAPTCHA_KEY）")

    log(f"打码平台: {provider} | captchaId: {captcha_id[:16]}... | 页面: {page_url}", "STEP")
    t0 = time.time()
    sol = _solve_capsolver(page_url, captcha_id) if provider == 'capsolver' \
        else _solve_2captcha(page_url, captcha_id)
    if not sol:
        raise RuntimeError("打码平台返回的验证结果不完整")
    log(f"验证码求解成功（{time.time() - t0:.1f}s）", "OK")
    return sol


# ==================== 反检测 & 注入脚本 ====================

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

# 钩住 initGeetest4，把站点传入的 config（含 captchaId）截留下来
GT4_HOOK_SCRIPT = r"""
(() => {
    try {
        let _orig = null;
        Object.defineProperty(window, 'initGeetest4', {
            configurable: true,
            get() { return _orig; },
            set(fn) {
                _orig = function (cfg) {
                    try { window.__gt4cfg = cfg || null; } catch (e) {}
                    return fn.apply(this, arguments);
                };
            }
        });
    } catch (e) {}
})();
"""

# 把打码平台的真实解写进站点读取的全局变量
INJECT_SOLUTION_JS = r"""
(sol) => {
    window.geetestV4Result = sol;
    if (window.Captcha) {
        window.Captcha.isReady = function () { return true; };
        window.Captcha.getResponse = function () {
            return {
                lot_number: sol.lot_number,
                captcha_output: sol.captcha_output,
                pass_token: sol.pass_token,
                gen_time: sol.gen_time
            };
        };
        window.Captcha.reset = function () { window.geetestV4Result = sol; };
    }
    return true;
}
"""

EXTRACT_CAPTCHA_ID_JS = r"""
() => {
    if (window.__gt4cfg && window.__gt4cfg.captchaId) return window.__gt4cfg.captchaId;
    const html = document.documentElement ? document.documentElement.innerHTML : '';
    const blobs = html.match(/[A-Za-z0-9+\/]{300,}={0,3}/g) || [];
    for (const b of blobs) {
        try {
            const t = atob(b);
            const m = t.match(/captchaId\s*:\s*['"]([^'"]+)['"]/);
            if (m) return m[1];
        } catch (e) {}
    }
    return null;
}
"""


def get_captcha_id(page):
    if CAPTCHA_ID_OVERRIDE:
        return CAPTCHA_ID_OVERRIDE
    try:
        cid = page.evaluate(EXTRACT_CAPTCHA_ID_JS)
        if cid:
            log(f"探测到 captchaId: {cid}", "OK")
            return cid
    except Exception as e:
        log(f"captchaId 探测异常: {e}", "WARN")
    log(f"captchaId 探测失败，使用内置缺省值", "WARN")
    return CAPTCHA_ID_FALLBACK


# ==================== GeeTest 处理 ====================

def handle_geetest(page):
    """等待极验初始化 -> 打码平台求真实解 -> 注入站点读取的全局变量"""
    log("等待 GeeTest v4 初始化...", "STEP")
    loaded = False
    for i in range(20):
        try:
            if page.evaluate("() => !!(window.geetestV4Loaded || document.querySelector('.embed-captcha canvas, .geetest_holder, .geetest_panel'))"):
                loaded = True
                break
        except Exception:
            pass
        time.sleep(1)
    log("GeeTest 已加载" if loaded else "GeeTest 未确认加载，继续尝试", "OK" if loaded else "WARN")

    captcha_id = get_captcha_id(page)
    page_url = page.url

    try:
        sol = solve_geetest_v4(page_url, captcha_id)
    except Exception as e:
        log(f"验证码求解失败: {e}", "FAIL")
        return False

    try:
        page.evaluate(INJECT_SOLUTION_JS, sol)
    except Exception as e:
        log(f"注入验证结果失败: {e}", "FAIL")
        return False

    # 确认站点自己的 Captcha 已就绪
    try:
        ready = page.evaluate("() => !!(window.Captcha && window.Captcha.isReady && window.Captcha.isReady())")
    except Exception:
        ready = False
    if not ready:
        log("注入后站点 Captcha 仍未就绪", "FAIL")
        return False

    log("验证结果已注入，站点 Captcha.isReady() = true", "OK")
    return True


# ==================== 登录状态判定 ====================

def is_logged_in(page):
    try:
        names = {c['name'] for c in page.context.cookies()}
        if {'uid', 'key', 'email'}.issubset(names):
            return True
    except Exception:
        pass
    u = page.url or ''
    if '/user' in u and '/auth/login' not in u:
        return True
    return False


def wait_login(page, timeout=45):
    start = time.time()
    while time.time() - start < timeout:
        if is_logged_in(page):
            return True
        time.sleep(2)
    return False


# ==================== 签到 ====================

def api_checkin(base_url, cookies_dict):
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
            r = session.post(f'{base_url}{ep}', timeout=15, allow_redirects=False)
            if r.status_code == 200:
                try:
                    d = r.json()
                except json.JSONDecodeError:
                    continue
                msg = d.get('msg', d.get('data', ''))
                if d.get('ret') == 1 or d.get('success'):
                    return True, msg or '签到成功'
                if '已签到' in str(msg):
                    return True, str(msg)
                if msg:
                    return False, str(msg)
            elif r.status_code == 302 and 'login' in r.headers.get('Location', ''):
                return False, "Cookie过期"
        except Exception:
            continue
    return False, "API均未命中"


def extract_checkin_result(page):
    for sel in ['.swal2-html-container', '.swal2-title', '.swal2-content']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                text = (el.inner_text() or '').strip()
                if text and len(text) < 200 and _is_checkin_text(text):
                    return text
        except Exception:
            continue
    try:
        body = page.text_content('body') or ""
        for pattern in [r'签到成功.*?(\d+\.?\d*)\s*(GB|MB)',
                        r'获得.*?(\d+\.?\d*)\s*(GB|MB)',
                        r'已连续签到.*?(\d+)\s*天']:
            m = re.search(pattern, body)
            if m:
                return m.group(0).strip()
        if '今日已签到' in body or '已经签到' in body:
            return "今日已签到"
        if '签到成功' in body:
            return "签到成功"
    except Exception:
        pass
    try:
        if page.query_selector('button:has-text("明日再来")'):
            return "签到成功（按钮已变化）"
    except Exception:
        pass
    return None


def _is_checkin_text(text):
    skip = ['下载客户端', '新手', '注册', '购买', '套餐', '教程',
            'Telegram', '联系', '客服', '二维码', '复制']
    if any(kw in text for kw in skip):
        return False
    return any(kw in text for kw in ['签到', '获得', '流量', 'MB', 'GB', '成功', '已签', '连续'])


# ==================== 登录页导航 ====================

def navigate_to_login(page, base_url):
    log(f"访问 {base_url}", "STEP")
    try:
        resp = page.goto(base_url, wait_until='domcontentloaded', timeout=60000)
        log(f"HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        log(f"访问异常: {e}", "FAIL"); return False

    time.sleep(5)

    for i in range(12):
        try:
            if "Just a moment" in (page.title() or ""):
                log(f"Cloudflare 校验中... ({(i + 1) * 5}s)"); time.sleep(5)
            else:
                break
        except Exception:
            break

    if page.query_selector('#email, input[name="email"], input[type="password"]'):
        return True

    for path in ['/auth/login', '/login']:
        try:
            page.goto(f"{base_url}{path}", wait_until='domcontentloaded', timeout=20000)
            time.sleep(3)
            if page.query_selector('#email, input[name="email"], input[type="password"]'):
                return True
        except Exception:
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
        context.add_init_script(GT4_HOOK_SCRIPT)
        page = context.new_page()

        try:
            # 1. 登录页
            if not navigate_to_login(page, URL):
                raise Exception("未找到登录页")
            take_screenshot(page, f"acct{index}_01")

            # 2. 填表
            log("填写登录信息...", "STEP")
            if not find_and_fill(page, ['#email', 'input[name="email"]', 'input[type="email"]'], email):
                raise Exception("未找到邮箱输入框")
            if not find_and_fill(page, ['#password', 'input[name="password"]', 'input[type="password"]'], password):
                raise Exception("未找到密码输入框")
            time.sleep(1)
            close_popups(page)

            # 3. 验证码（真解注入）
            if not handle_geetest(page):
                raise Exception("GeeTest v4 验证未通过")
            take_screenshot(page, f"acct{index}_02_geetest")

            # 4. 提交登录（页面自身会带 host / phase / captcha_result / pageLoadedAt）
            log("点击登录...", "STEP")
            if not find_and_click(page, ['.login-submit button.login', 'button.login',
                                         'button[type="submit"]', 'button:has-text("Login")',
                                         'button:has-text("登录")']):
                page.keyboard.press('Enter')

            # 5. 判定结果
            ok = wait_login(page, timeout=45)
            if not ok:
                swal = get_swal_text(page)
                take_screenshot(page, f"acct{index}_03_error")
                if swal:
                    raise Exception(f"登录未通过：{swal}")
                # 直接访问用户中心再判一次
                log("尝试直接访问用户中心...", "WARN")
                try:
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(3)
                    close_popups(page)
                except Exception:
                    pass
                if not wait_login(page, timeout=15):
                    raise Exception("登录失败（cookie 未下发）")

            log("登录成功", "OK")
            take_screenshot(page, f"acct{index}_04_logged_in")

            # 6. 用最终落地域名作为接口基址（站点可能把非白名单域名跳转到 ikuuu.top）
            parsed = urlparse(page.url)
            base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else URL
            log(f"接口基址: {base_url}", "INFO")

            if '/user' not in page.url:
                try:
                    page.goto(f"{base_url}/user", wait_until='domcontentloaded', timeout=30000)
                    time.sleep(3)
                    close_popups(page)
                except Exception:
                    pass
            take_screenshot(page, f"acct{index}_05_user")

            # 7. 签到
            log("执行签到...", "STEP")
            cookies = {c['name']: c['value'] for c in context.cookies()}
            log(f"Cookies: {[k for k in cookies if k in ('uid', 'email', 'key', 'expire_in')]}")

            api_ok, api_msg = api_checkin(base_url, cookies)
            if api_ok:
                result_msg = api_msg
                success = True
                log(f"API签到: {api_msg}", "OK")
            else:
                log(f"API签到: {api_msg}，尝试页面签到...", "WARN")
                if not find_and_click(page, [
                    'button:has-text("每日签到")', 'a:has-text("每日签到")',
                    'button:has-text("签到")', '#checkin-btn', '.checkin-btn',
                    'button[onclick*="checkin"]', '[lay-filter="checkin"]'
                ]):
                    js_result = page.evaluate("""
                        async () => {
                            const btns = document.querySelectorAll('button, a');
                            for (let btn of btns) {
                                const t = (btn.textContent || '').trim();
                                if (t.includes('每日签到') || t === '签到') {
                                    btn.click(); return 'clicked:' + t;
                                }
                            }
                            try {
                                const r = await fetch('/user/checkin', {
                                    method:'POST',
                                    headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
                                    credentials:'same-origin'
                                });
                                return await r.text();
                            } catch(e) { return 'ERROR:' + e.message; }
                        }
                    """)
                    log(f"JS签到: {str(js_result)[:120]}")
                    try:
                        if isinstance(js_result, str) and '{' in js_result:
                            d = json.loads(js_result)
                            if d.get('ret') == 1:
                                result_msg = d.get('msg', '签到成功'); success = True
                            elif d.get('msg') and ('已签到' in d['msg'] or '签到成功' in d['msg']):
                                result_msg = d['msg']; success = True
                    except Exception:
                        pass

                if not success:
                    time.sleep(3)
                    close_popups(page)
                    result_msg = extract_checkin_result(page)
                    if not result_msg:
                        time.sleep(3)
                        result_msg = extract_checkin_result(page)
                    if result_msg:
                        success = True
                    else:
                        try:
                            btn = page.query_selector('button:has-text("明日再来")')
                            if btn and btn.is_visible():
                                result_msg = "签到成功"; success = True
                        except Exception:
                            pass
                        if not success:
                            result_msg = "签到已执行，但未取到结果文本"

            close_popups(page)
            take_screenshot(page, f"acct{index}_06_final")

        except Exception as e:
            result_msg = str(e)[:300]
            success = False
            log(f"异常: {e}", "FAIL")
            take_screenshot(page, f"acct{index}_error")
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    if success:
        return f"账号 {email}: ✅ {result_msg}"
    return f"账号 {email}: ❌ {result_msg}"


# ==================== 入口 ====================

if __name__ == '__main__':
    if not URL:
        print("❌ URL 未配置"); sys.exit(1)

    provider = _pick_provider()
    accounts = get_accounts()
    if not accounts:
        sys.exit(1)

    log("=" * 55)
    log("  iKuuu 机场自动签到 v4.0")
    log(f"  目标: {URL}")
    log(f"  共 {len(accounts)} 个账号")
    log(f"  打码平台: {provider or '未配置（登录必失败）'}")
    log("=" * 55)

    if not provider:
        log("缺少 CAPSOLVER_KEY / TWOCAPTCHA_KEY，GeeTest v4 无法求解", "FAIL")

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
