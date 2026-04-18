#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import time
import random
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 获取环境变量
URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')

def get_accounts():
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG 格式错误：应为偶数行")
            return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i+1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置有效的账号信息")
    return accounts

def push_notification(title, content):
    if not SCKEY:
        return
    try:
        import requests
        # ServerChan 新版接口，desp 支持 Markdown
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send", 
                          data={"title": title, "desp": content}, 
                          timeout=10,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        if res.status_code == 200:
            print("📤 推送成功")
        else:
            print(f"⚠️ 推送失败: {res.status_code} - {res.text[:100]}")
    except Exception as e:
        print(f"⚠️ 推送异常: {e}")

def debug_page(page, label="页面快照"):
    """调试辅助：打印页面关键信息 + 保存截图/源码"""
    try:
        title = page.title()
        url = page.url
        html_snippet = page.content()[:500].replace('\n', ' ')
        print(f"🔍 [{label}] URL: {url}")
        print(f"🔍 [{label}] Title: {title}")
        print(f"🔍 [{label}] HTML 片段: {html_snippet}...")
        
        # 保存截图和源码（GitHub Actions 中会作为 artifact 上传）
        timestamp = int(time.time())
        page.screenshot(path=f'debug_{label}_{timestamp}.png')
        with open(f'debug_{label}_{timestamp}.html', 'w', encoding='utf-8') as f:
            f.write(page.content())
        print(f"📸 调试文件已保存: debug_{label}_{timestamp}.{{png,html}}")
    except Exception as e:
        print(f"⚠️ 调试信息收集失败: {e}")

def sign_account_playwright(index, email, password):
    print(f"\n{'='*20} 账号 {index+1} {'='*20}")
    print(f"👤 账号: {email}")
    
    result_msg = ""
    
    with sync_playwright() as p:
        # 启动 Chromium（无头 + 防检测 + 中文环境）
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-blink-features=AutomationControlled'  # 隐藏自动化特征
            ]
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            extra_http_headers={
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            }
        )
        
        # 关键：屏蔽自动化检测特征
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)
        
        page = context.new_page()
        
        try:
            login_url = f"{URL}/auth/login"
            print(f"🔑 正在加载: {login_url}")
            
            # 1. 访问登录页，等待网络空闲 + DOM 加载
            page.goto(login_url, wait_until='domcontentloaded', timeout=30000)
            # 再等 3 秒让动态内容渲染（应对 Vue/React 应用）
            time.sleep(3)
            
            # 🔍 调试：输出页面信息
            debug_page(page, "登录页加载后")
            
            # 2. 检查是否已登录（避免重复登录）
            if '/user' in page.url or page.query_selector('#user-center, .user-panel, a[href="/user/logout"]'):
                print("✅ 检测到已登录状态，跳过登录")
            else:
                # 3. 等待登录表单出现（使用更通用的选择器）
                # 兼容：name="email", name="username", type="email", id="email", .form-control[placeholder*="邮箱"]
                email_selectors = [
                    'input[name="email"]',
                    'input[name="username"]', 
                    'input[type="email"]',
                    'input#email',
                    'input.form-control[placeholder*="邮箱" i]',
                    'input[placeholder*="Email" i]'
                ]
                
                print("🔍 正在查找邮箱输入框...")
                email_input = None
                for selector in email_selectors:
                    try:
                        email_input = page.wait_for_selector(selector, state='visible', timeout=8000)
                        print(f"✅ 找到邮箱输入框: {selector}")
                        break
                    except PlaywrightTimeout:
                        continue
                
                if not email_input:
                    # 检查是否是 Cloudflare 拦截页
                    html = page.content().lower()
                    if 'cloudflare' in html or 'challenges.cloudflare.com' in html or 'checking your browser' in html:
                        print("🔄 检测到 Cloudflare 验证页，等待自动通过...")
                        # 等待最多 20 秒让 CF 验证完成
                        try:
                            page.wait_for_function(
                                "() => !document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]')",
                                timeout=20000
                            )
                            print("✅ Cloudflare 验证通过，重试查找表单...")
                            # 重试查找
                            for selector in email_selectors:
                                try:
                                    email_input = page.wait_for_selector(selector, state='visible', timeout=5000)
                                    print(f"✅ 找到邮箱输入框: {selector}")
                                    break
                                except PlaywrightTimeout:
                                    continue
                        except PlaywrightTimeout:
                            print("⚠️ Cloudflare 验证超时")
                    
                    if not email_input:
                        raise Exception("❌ 未找到邮箱输入框，可能页面结构不匹配或验证未通过")
                
                # 4. 填写表单
                email_input.fill(email)
                
                # 密码框选择器（同样兼容多种写法）
                pwd_selectors = [
                    'input[name="passwd"]',
                    'input[name="password"]',
                    'input[type="password"]',
                    'input#password'
                ]
                for selector in pwd_selectors:
                    try:
                        page.fill(selector, password, timeout=5000)
                        print(f"✅ 填写密码框: {selector}")
                        break
                    except:
                        continue
                
                # 5. 处理 Turnstile/验证码（如果有）
                if page.query_selector('iframe[src*="challenges.cloudflare.com"], .cf-turnstile'):
                    print("🔄 检测到 Turnstile 验证，等待 token 生成...")
                    try:
                        page.wait_for_function("""
                            () => {
                                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                                if (!iframe) return true;
                                const token = iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                                return token && token.length > 20;
                            }
                        """, timeout=25000)
                        print("✅ Turnstile 验证通过")
                    except PlaywrightTimeout:
                        print("⚠️ Turnstile 等待超时，尝试继续提交")
                
                # 6. 点击登录按钮
                login_btn_selectors = [
                    'button[type="submit"]',
                    'button:has-text("登录")',
                    'button:has-text("Login")',
                    '.login-btn',
                    '#login-btn',
                    'input[type="submit"]'
                ]
                for selector in login_btn_selectors:
                    try:
                        if page.query_selector(selector):
                            page.click(selector, timeout=10000)
                            print(f"✅ 点击登录按钮: {selector}")
                            break
                    except:
                        continue
                
                # 7. 等待登录成功（跳转或出现用户元素）
                print("⏳ 等待登录响应...")
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=20000)
                    print("✅ 登录成功（页面跳转）")
                except PlaywrightTimeout:
                    # 备用：检查是否出现用户中心元素
                    if page.query_selector('#user-center, .user-info, a[href="/user/logout"]'):
                        print("✅ 登录成功（检测到用户元素）")
                    else:
                        debug_page(page, "登录后页面")
                        raise Exception("❌ 登录无响应，可能账号密码错误或触发风控")
            
            # 8. 执行签到
            time.sleep(2)
            print("📅 正在签到...")
            
            # 提取浏览器 cookie 用于 API 请求
            cookies = context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            import requests
            session = requests.Session()
            session.headers.update({
                'user-agent': context._options.get('user_agent', ''),
                'origin': URL,
                'referer': f"{URL}/user",
                'cookie': cookie_str,
                'x-requested-with': 'XMLHttpRequest'  # SSPANEL 常用头
            })
            
            # 尝试签到接口
            checkin_url = f"{URL}/user/checkin"
            try:
                res = session.post(checkin_url, timeout=15)
                if res.text.strip().startswith('{'):
                    data = res.json()
                    msg = data.get('msg', data.get('message', '签到完成'))
                    print(f"🎉 签到结果: {msg}")
                    result_msg = f"账号 {email}: {msg}"
                else:
                    # 接口返回 HTML，尝试页面点击签到
                    print("⚠️ 接口返回非 JSON，尝试页面签到...")
                    page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=15000)
                    time.sleep(2)
                    
                    # 查找签到按钮（多种可能）
                    checkin_btn = page.query_selector('#checkin-btn, .check-in-btn, button:has-text("签到"), button:has-text("Checkin")')
                    if checkin_btn:
                        checkin_btn.click()
                        page.wait_for_load_state('networkidle')
                        # 尝试提取结果消息
                        msg_el = page.query_selector('.msg, .alert-success, #result, .layui-layer-content')
                        msg = msg_el.inner_text() if msg_el else "签到请求已发送"
                        print(f"🎉 签到结果: {msg}")
                        result_msg = f"账号 {email}: {msg}"
                    else:
                        # 检查是否已签到
                        if page.query_selector('text=今日已签到, text=Already checked in'):
                            result_msg = f"账号 {email}: 今日已签到"
                            print("ℹ️  今日已签到")
                        else:
                            debug_page(page, "签到页")
                            result_msg = f"账号 {email}: 未找到签到入口"
                            
            except Exception as e:
                print(f"⚠️ 签到请求异常: {e}")
                result_msg = f"账号 {email}: 签到请求失败 - {str(e)[:50]}"
                    
        except Exception as e:
            print(f"💥 签到失败: {str(e)}")
            result_msg = f"账号 {email}: {str(e)[:100]}"
            debug_page(page, "错误时页面")
        finally:
            browser.close()
    
    return result_msg

if __name__ == '__main__':
    if not URL:
        print("❌ 环境变量 URL 未设置")
        exit(1)
    
    accounts = get_accounts()
    if not accounts:
        print("❌ 没有可执行的账号")
        exit(1)
    
    print(f"🚀 共检测到 {len(accounts)} 个账号，开始执行...")
    results = []
    
    for idx, (email, pwd) in enumerate(accounts):
        results.append(sign_account_playwright(idx, email, pwd))
        if idx < len(accounts) - 1:
            delay = random.randint(20, 50)
            print(f"⏳ 等待 {delay} 秒后处理下一个账号...")
            time.sleep(delay)
    
    if SCKEY and results:
        summary = "📊 机场签到汇总 (Playwright)\n\n" + "\n\n".join(results)
        push_notification("机场每日签到", summary)
    
    print("\n🏁 全部流程执行完毕")
