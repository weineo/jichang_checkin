#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
            print("⚠️ CONFIG 格式错误")
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
        # Server酱免费额度每天5次，超限会返回400
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send", 
                          data={"title": title, "desp": content}, 
                          timeout=10)
        if res.status_code == 200:
            print("📤 推送成功")
        elif res.status_code == 400 and '发送次数限制' in res.text:
            print("⚠️ Server酱今日推送已达上限（免费5次/天）")
        else:
            print(f"⚠️ 推送失败: {res.status_code}")
    except Exception as e:
        print(f"⚠️ 推送异常: {e}")

def sign_account_playwright(index, email, password):
    print(f"\n{'='*20} 账号 {index+1} {'='*20}")
    print(f"👤 账号: {email}")
    
    result_msg = ""
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', 
                  '--disable-gpu', '--no-first-run', '--no-zygote', 
                  '--disable-blink-features=AutomationControlled']
        )
        
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
            timezone_id='Asia/Shanghai'
        )
        
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)
        
        page = context.new_page()
        
        try:
            # 1. 访问主页触发CF验证
            print(f"🏠 访问主页: {URL}")
            page.goto(URL, wait_until='networkidle', timeout=30000)
            time.sleep(4)  # 等待CF验证
            
            # 2. 跳转到登录页
            login_url = f"{URL}/auth/login"
            print(f"🔑 访问登录页")
            
            # 尝试点击登录链接
            if page.query_selector('a[href="/auth/login"]'):
                page.click('a[href="/auth/login"]')
                page.wait_for_load_state('networkidle')
            else:
                page.goto(login_url, wait_until='domcontentloaded', timeout=20000)
            
            time.sleep(2)
            
            # 3. 检查是否已登录
            if '/user' in page.url or page.query_selector('a[href="/user/logout"]'):
                print("✅ 已登录，跳过登录步骤")
            else:
                # 填写表单
                email_input = page.wait_for_selector('input[name="email"], input[type="email"]', state='visible', timeout=10000)
                email_input.fill(email)
                page.fill('input[name="passwd"], input[type="password"]', password)
                
                # 等待Turnstile
                if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                    print("🔄 等待Turnstile验证...")
                    try:
                        page.wait_for_function("""
                            () => {
                                const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                                if (!iframe) return true;
                                const token = iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                                return token && token.length > 20;
                            }
                        """, timeout=25000)
                        print("✅ Turnstile通过")
                    except:
                        print("⚠️ Turnstile超时")
                
                # 点击登录
                page.click('button[type="submit"], input[type="submit"]', timeout=10000)
                page.wait_for_url(f"{URL}/user*", timeout=20000)
                print("✅ 登录成功")
            
            # 4. 🔥 签到核心逻辑（多方案尝试）
            time.sleep(2)
            print("📅 执行签到...")
            
            cookies = context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            import requests
            session = requests.Session()
            session.headers.update({
                'user-agent': user_agent,
                'origin': URL,
                'referer': f"{URL}/user",
                'cookie': cookie_str,
                'x-requested-with': 'XMLHttpRequest',  # SSPANEL关键头
                'accept': 'application/json, text/javascript, */*; q=0.01'
            })
            
            # 🔄 方案A: 标准 /user/checkin 接口
            checkin_url = f"{URL}/user/checkin"
            print(f"🔹 尝试接口: {checkin_url}")
            res = session.post(checkin_url, timeout=15)
            
            if res.text.strip().startswith('{'):
                try:
                    data = res.json()
                    msg = data.get('msg', data.get('message', '签到完成'))
                    print(f"🎉 签到成功: {msg}")
                    result_msg = f"账号 {email}: {msg}"
                except json.JSONDecodeError:
                    pass
            
            # 🔄 方案B: 备用接口 /user?action=checkin
            if not result_msg:
                print(f"🔹 尝试备用接口: {URL}/user")
                res2 = session.post(f"{URL}/user", data={'action': 'checkin'}, timeout=15)
                if res2.text.strip().startswith('{'):
                    try:
                        data = res2.json()
                        msg = data.get('msg', data.get('message', '签到完成'))
                        print(f"🎉 签到成功: {msg}")
                        result_msg = f"账号 {email}: {msg}"
                    except:
                        pass
            
            # 🔄 方案C: 页面点击签到按钮（最后手段）
            if not result_msg:
                print("🔹 尝试页面点击签到...")
                page.goto(f"{URL}/user", wait_until='domcontentloaded', timeout=15000)
                time.sleep(3)
                
                # 查找签到按钮
                btn = page.query_selector('#checkin-btn, .check-in-btn, button:has-text("签到"), button:has-text("Check In")')
                if btn:
                    btn.click()
                    page.wait_for_load_state('networkidle')
                    # 提取结果
                    msg_el = page.query_selector('.msg, .alert, .layui-layer-content')
                    msg = msg_el.inner_text().strip() if msg_el else "签到请求已发送"
                    print(f"🎉 签到结果: {msg}")
                    result_msg = f"账号 {email}: {msg}"
                else:
                    # 检查是否已签到
                    if page.query_selector('text=今日已签到, text=Already checked in, text=您已签到'):
                        result_msg = f"账号 {email}: ✅ 今日已签到"
                        print("ℹ️  今日已签到")
                    else:
                        # 打印响应调试
                        print(f"⚠️ 签到响应预览: {res.text[:300]}")
                        result_msg = f"账号 {email}: ❌ 签到失败（接口返回非预期内容）"
                    
        except Exception as e:
            print(f"💥 异常: {str(e)}")
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
        results.append(sign_account_playwright(idx, email, pwd))
        if idx < len(accounts) - 1:
            time.sleep(random.randint(20, 40))
    
    if SCKEY and results:
        summary = "📊 iKuuu签到汇总\n\n" + "\n\n".join(results)
        push_notification("机场签到", summary)
    
    print("\n🏁 完成")
