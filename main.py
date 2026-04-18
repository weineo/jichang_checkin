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
            time.sleep(5)
            
            # 2️⃣ 检查是否已登录
            if '/user' in page.url or page.query_selector('a[href="/user/logout"]'):
                print("✅ 已是用户中心")
            else:
                # 3️⃣ 填写登录表单
                print("📝 填写登录信息...")
                
                # 邮箱
                page.wait_for_selector('input[name="email"]', state='visible', timeout=15000)
                page.fill('input[name="email"]', email)
                
                # 密码（兼容 name="passwd" 和 name="password"）
                try:
                    page.fill('input[name="passwd"]', password)
                except:
                    page.fill('input[name="password"]', password)
                
                # 等待可能的二次验证
                time.sleep(3)
                
                # 4️⃣ 点击登录
                print("🔘 点击登录...")
                page.click('button[type="submit"]', timeout=15000)
                
                # 5️⃣ 等待登录成功（多种方式）
                print("⏳ 等待登录响应...")
                try:
                    # 方式1: 等待URL跳转
                    page.wait_for_url(f"{URL}/user*", timeout=20000)
                    print("✅ 登录成功（URL跳转）")
                except:
                    # 方式2: 等待页面出现用户中心元素
                    try:
                        page.wait_for_selector('a[href="/user/logout"], #checkin-btn, button:has-text("签到")', timeout=15000)
                        print("✅ 登录成功（检测到用户元素）")
                    except:
                        # 方式3: 再等5秒让页面加载
                        time.sleep(5)
                        print("⚠️ 登录响应慢，尝试继续")
                
                time.sleep(3)
            
            # 6️⃣ 确保在用户中心
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                time.sleep(4)
            
            # 7️⃣ 执行签到
            print("📅 执行签到...")
            
            # 🔥 签到按钮选择器（使用原生兼容的选择器）
            btn_selectors = [
                '#checkin-btn',
                '.check-in-btn',
                '.btn-checkin',
                '[onclick*="checkin"]',
                '[lay-filter="checkin"]'
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
            
            # 🔥 JS兜底（使用原生兼容的选择器）
            if not clicked:
                print("🔘 JS触发签到...")
                try:
                    page.evaluate("""
                        () => {
                            // 尝试调用SSPANEL原生函数
                            if (typeof checkin === 'function') {
                                checkin();
                                return 'function';
                            }
                            // 尝试点击常见ID/class
                            const ids = ['checkin-btn', 'signin-btn'];
                            for (let id of ids) {
                                const el = document.getElementById(id);
                                if (el) { el.click(); return 'id:' + id; }
                            }
                            const classes = ['check-in-btn', 'btn-checkin'];
                            for (let cls of classes) {
                                const el = document.querySelector('.' + cls);
                                if (el) { el.click(); return 'class:' + cls; }
                            }
                            return 'not_found';
                        }
                    """)
                    clicked = True
                except Exception as e:
                    print(f"⚠️ JS触发失败: {e}")
            
            # 8️⃣ 等待并提取结果
            print("⏳ 等待签到结果...")
            time.sleep(6)
            
            msg = None
            
            # 尝试提取结果消息
            for selector in ['.msg', '.alert', '.layui-layer-content', '[role="alert"]', '.swal2-html-container']:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
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
