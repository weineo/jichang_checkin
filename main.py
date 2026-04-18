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
        elif '发送次数限制' in res.text:
            print("⚠️ Server酱今日已达上限")
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
            # 1️⃣ 访问主页（触发CF验证）
            print(f"🏠 访问主页...")
            page.goto(URL, wait_until='networkidle', timeout=40000)
            time.sleep(8)  # ️ 关键：等待CF验证完成（8秒）
            
            # 2️⃣ 直接访问用户中心
            print("🔑 访问用户中心...")
            page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
            time.sleep(5)  # 等待页面加载
            
            # 检查是否404
            if '404' in page.title():
                print("❌ 用户中心返回404")
                page.screenshot(path=f'error_{int(time.time())}.png')
                result_msg = f"账号 {email}: ❌ 无法访问用户中心"
                return result_msg
            
            # 3️⃣ 检查是否需要登录
            if '/auth/login' in page.url or '登录' in page.title():
                print("📝 需要登录，寻找登录表单...")
                
                # 尝试点击登录链接
                try:
                    page.click('a:has-text("登录"), a:has-text("Login")', timeout=10000)
                    page.wait_for_load_state('networkidle')
                    time.sleep(3)
                except:
                    pass
                
                # 填写表单
                try:
                    page.fill('input[name="email"], input[type="email"]', email)
                    page.fill('input[name="passwd"], input[type="password"]', password)
                    
                    # 等待Turnstile
                    if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                        print("🔄 等待验证...")
                        time.sleep(10)
                    
                    # 点击登录
                    page.click('button[type="submit"]', timeout=15000)
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    print("✅ 登录成功")
                    time.sleep(3)
                except Exception as e:
                    print(f"❌ 登录失败: {e}")
                    result_msg = f"账号 {email}: ❌ 登录失败"
                    return result_msg
            
            # 4️⃣ 执行签到
            print("📅 执行签到...")
            
            # 查找签到按钮（覆盖所有可能）
            btn_selectors = [
                '#checkin-btn',
                '.check-in-btn',
                'button:has-text("签到")',
                'button:has-text("Check In")',
                '.layui-btn[lay-filter="checkin"]',
                'a:has-text("签到")',
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
                        const btn = document.querySelector('#checkin-btn, .check-in-btn');
                        if (btn) btn.click();
                    }
                """)
            
            # 等待结果
            time.sleep(5)
            
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
                if '已签到' in page_text or '今日奖励' in page_text:
                    msg = "✅ 签到成功"
                elif '今日已签到' in page_text:
                    msg = "ℹ️ 今日已签到"
            
            if msg:
                print(f"🎉 结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                print("⚠️ 未检测到结果")
                page.screenshot(path=f'result_{int(time.time())}.png')
                result_msg = f"账号 {email}: ❓ 未检测到结果"
                    
        except Exception as e:
            print(f"💥 异常: {e}")
            result_msg = f"账号 {email}: {str(e)[:80]}"
            try:
                page.screenshot(path=f'error_{int(time.time())}.png')
            except:
                pass
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
