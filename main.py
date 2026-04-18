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
            time.sleep(8)  # 等待CF验证
            
            # 检查CF
            for i in range(5):
                html = page.content().lower()
                if 'cloudflare' not in html:
                    break
                print(f"⏳ 等待CF... ({i+1}/5)")
                time.sleep(5)
            
            # 🔍 关键：检查主页是否已是登录页
            page_title = page.title().lower()
            has_email_input = page.query_selector('input[name="email"], input[type="email"]')
            has_password_input = page.query_selector('input[name="passwd"], input[type="password"]')
            
            if has_email_input and has_password_input:
                print("✅ 主页已是登录页，直接登录")
                # 不点击任何链接，直接在当前页登录
            elif '/user' in page.url:
                print("✅ 主页已是用户中心")
            else:
                # 寻找登录入口
                print("🔍 寻找登录入口...")
                # 点击"登录"链接（不是"注册"）
                try:
                    page.click('a:has-text("登录"), a:has-text("Login")', timeout=10000)
                    page.wait_for_load_state('networkidle')
                    time.sleep(3)
                except:
                    page.goto(f"{URL}/auth/login", wait_until='domcontentloaded', timeout=20000)
                    time.sleep(3)
            
            # 2️⃣ 检查是否已登录
            if '/user' in page.url:
                print("✅ 已登录")
            else:
                # 3️⃣ 填写登录表单
                print("📝 填写登录信息...")
                
                # 等待输入框
                page.wait_for_selector('input[name="email"]', timeout=15000)
                page.fill('input[name="email"]', email)
                page.fill('input[name="passwd"]', password)
                
                # 🔥 等待Turnstile验证（"点我开始验证"）
                print("🔄 等待Turnstile验证...")
                
                # 检查是否有Turnstile iframe
                if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                    try:
                        # 等待验证完成（最多30秒）
                        page.wait_for_function("""
                            () => {
                                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                                if (!iframe) return true;
                                const token = iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                                return token && token.length > 20;
                            }
                        """, timeout=30000)
                        print("✅ Turnstile验证通过")
                    except:
                        print("⚠️ Turnstile超时，尝试继续")
                        time.sleep(5)
                else:
                    # 没有Turnstile，等待3秒
                    time.sleep(3)
                
                # 4️⃣ 点击登录按钮
                print("🔘 点击登录...")
                
                # 查找登录按钮（排除注册按钮）
                login_btn_selectors = [
                    'button:has-text("登录"):not(:has-text("注册"))',
                    'button[type="submit"]:not(button:has-text("注册"))',
                    'input[type="submit"]'
                ]
                
                for selector in login_btn_selectors:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            print(f"✅ 点击登录按钮")
                            break
                    except:
                        continue
                
                # 等待登录成功
                page.wait_for_url(f"{URL}/user*", timeout=30000)
                print("✅ 登录成功")
                time.sleep(3)
            
            # 5️⃣ 访问用户中心
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                time.sleep(5)
            
            # 检查404
            if '404' in page.title():
                print("❌ 用户中心404")
                result_msg = f"账号 {email}: ❌ 404"
                return result_msg
            
            # 6️⃣ 签到
            print("📅 执行签到...")
            
            # 查找签到按钮（"明日再来"）
            btn_selectors = [
                'button:has-text("明日再来")',
                'a:has-text("明日再来")',
                '#checkin-btn',
                '.check-in-btn',
                'button:has-text("签到")'
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
            
            if not clicked:
                print("🔘 JS触发...")
                page.evaluate("""
                    () => {
                        if (typeof checkin === 'function') checkin();
                        const btn = document.querySelector('#checkin-btn, .check-in-btn');
                        if (btn) btn.click();
                    }
                """)
            
            time.sleep(6)
            
            # 提取结果
            msg = None
            for selector in ['.msg', '.alert', '.layui-layer-content']:
                try:
                    el = page.query_selector(selector)
                    if el:
                        msg = el.inner_text().strip()
                        if msg:
                            break
                except:
                    continue
            
            if not msg:
                page_text = page.text_content('body')
                if '已签到' in page_text or '签到成功' in page_text:
                    msg = "✅ 签到成功"
                elif '今日已签到' in page_text:
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
