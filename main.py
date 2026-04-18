#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import random
from playwright.sync_api import sync_playwright

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
            print("⚠️ CONFIG格式错误")
            return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i+1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置账号")
    return accounts

def push_notification(title, content):
    if not SCKEY:
        return
    try:
        import requests
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send", 
                          data={"title": title, "desp": content}, timeout=10)
        if res.status_code == 200:
            print("📤 推送成功")
    except:
        pass

def wait_for_turnstile(page, timeout=40000):
    """等待Cloudflare Turnstile验证完成"""
    print("🔄 检测Turnstile验证...")
    
    # 检查是否有Turnstile iframe
    if not page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
        print("ℹ️  未检测到Turnstile，跳过")
        return True
    
    start = time.time()
    while time.time() - start < timeout / 1000:
        try:
            # 方法1: 检查token是否生成
            token = page.evaluate("""
                () => {
                    const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    if (!iframe) return null;
                    try {
                        return iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                    } catch { return null; }
                }
            """)
            if token and len(token) > 20:
                print("✅ Turnstile验证通过（检测到token）")
                return True
        except:
            pass
        
        # 方法2: 检查iframe是否消失
        if not page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
            print("✅ Turnstile验证通过（iframe已消失）")
            return True
        
        print(f"⏳ 等待Turnstile... ({int(time.time()-start)}s)")
        time.sleep(3)
    
    print("⚠️ Turnstile等待超时，尝试继续")
    return False

def sign_account(index, email, password):
    print(f"\n{'='*20} 账号 {index+1} {'='*20}")
    print(f"👤 账号: {email}")
    
    result_msg = ""
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                  '--disable-gpu', '--no-first-run', '--disable-blink-features=AutomationControlled']
        )
        
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
            timezone_id='Asia/Shanghai'
        )
        
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        page = context.new_page()
        
        try:
            # 1️⃣ 访问主页
            print(f"🏠 访问主页...")
            page.goto(URL, wait_until='networkidle', timeout=60000)
            time.sleep(5)  # 初始等待
            
            # 2️⃣ 等待Turnstile验证（关键！）
            wait_for_turnstile(page)
            time.sleep(2)  # 验证后稍等
            
            # 3️⃣ 检查是否已登录
            if '/user' in page.url:
                print("✅ 已是用户中心")
            else:
                # 4️⃣ 填写登录表单
                print("📝 填写登录信息...")
                
                # 🔥 使用更灵活的选择器（兼容多种写法）
                email_selectors = ['input[name="email"]', 'input[type="email"]', '#email']
                pwd_selectors = ['input[name="passwd"]', 'input[name="password"]', 'input[type="password"]', '#password']
                
                # 等待并填写邮箱
                email_filled = False
                for selector in email_selectors:
                    try:
                        el = page.wait_for_selector(selector, state='visible', timeout=10000)
                        el.fill(email)
                        print(f"✅ 填写邮箱: {selector}")
                        email_filled = True
                        break
                    except:
                        continue
                
                if not email_filled:
                    print("❌ 未找到邮箱输入框")
                    result_msg = f"账号 {email}: ❌ 未找到邮箱输入框"
                    return result_msg
                
                # 等待并填写密码
                pwd_filled = False
                for selector in pwd_selectors:
                    try:
                        el = page.wait_for_selector(selector, state='visible', timeout=10000)
                        el.fill(password)
                        print(f"✅ 填写密码: {selector}")
                        pwd_filled = True
                        break
                    except:
                        continue
                
                if not pwd_filled:
                    print("❌ 未找到密码输入框")
                    result_msg = f"账号 {email}: ❌ 未找到密码输入框"
                    return result_msg
                
                # 🔥 再次等待Turnstile（填写后可能触发二次验证）
                wait_for_turnstile(page, timeout=20000)
                
                # 5️⃣ 点击登录按钮
                print("🔘 点击登录...")
                login_selectors = [
                    'button:has-text("登录"):not(:has-text("注册"))',
                    'button[type="submit"]',
                    'input[type="submit"]'
                ]
                
                for selector in login_selectors:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            print(f"✅ 点击: {selector}")
                            break
                    except:
                        continue
                
                # 等待登录成功
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    print("✅ 登录成功")
                except:
                    # 备用：检查是否出现用户元素
                    if page.query_selector('a[href="/user/logout"]'):
                        print("✅ 登录成功（检测到登出链接）")
                    else:
                        print("⚠️ 登录响应超时，尝试继续")
                
                time.sleep(3)
            
            # 6️⃣ 确保在用户中心
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                time.sleep(4)
            
            # 7️⃣ 执行签到
            print("📅 执行签到...")
            
            # 签到按钮（"明日再来"在右上角）
            btn_selectors = [
                'button:has-text("明日再来")',
                'a:has-text("明日再来")',
                '#checkin-btn',
                '.check-in-btn',
                'button:has-text("签到")',
                '.btn-checkin'
            ]
            
            clicked = False
            for selector in btn_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        print(f"🔘 点击: {selector}")
                        btn.click()
                        clicked = True
                        break
                except:
                    continue
            
            # JS兜底
            if not clicked:
                print("🔘 JS触发签到...")
                page.evaluate("""
                    () => {
                        if (typeof checkin === 'function') checkin();
                        const btn = document.querySelector('#checkin-btn, .check-in-btn, button:has-text("明日再来")');
                        if (btn) btn.click();
                    }
                """)
            
            # 等待结果
            time.sleep(6)
            
            # 提取结果
            msg = None
            for selector in ['.msg', '.alert', '.layui-layer-content', '[role="alert"]']:
                try:
                    el = page.query_selector(selector)
                    if el:
                        text = el.inner_text().strip()
                        if text and len(text) < 200:
                            msg = text
                            break
                except:
                    continue
            
            # 检查页面文本
            if not msg:
                page_text = page.text_content('body')
                if '签到成功' in page_text or '获得' in page_text:
                    msg = "✅ 签到成功"
                elif '今日已签到' in page_text or '明日再来' in page_text:
                    msg = "ℹ️ 今日已签到"
            
            if msg:
                print(f"🎉 结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                result_msg = f"账号 {email}: ❓ 未检测到结果"
                    
        except Exception as e:
            print(f"💥 异常: {e}")
            result_msg = f"账号 {email}: {str(e)[:100]}"
        finally:
            browser.close()
    
    return result_msg

if __name__ == '__main__':
    if not URL:
        print("❌ URL未配置")
        exit(1)
    
    accounts = get_accounts()
    if not accounts:
        exit(1)
    
    print(f"🚀 共 {len(accounts)} 个账号")
    results = []
    
    for idx, (email, pwd) in enumerate(accounts):
        results.append(sign_account(idx, email, pwd))
        if idx < len(accounts) - 1:
            time.sleep(random.randint(30, 60))
    
    if SCKEY and results:
        summary = "📊 iKuuu签到\n\n" + "\n\n".join(results)
        push_notification("机场签到", summary)
    
    print("\n🏁 完成")
