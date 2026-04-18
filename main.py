#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
            print("⚠️ Server酱今日推送已达上限")
        else:
            print(f"⚠️ 推送失败: {res.status_code}")
    except:
        pass

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
        
        # 屏蔽自动化检测
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
        """)
        
        page = context.new_page()
        
        try:
            # 1. 访问主页触发CF验证
            print(f"🏠 访问主页: {URL}")
            page.goto(URL, wait_until='networkidle', timeout=40000)
            time.sleep(5)  # 关键：等待CF验证完成
            
            # 2. 跳转到登录页
            login_url = f"{URL}/auth/login"
            print("🔑 访问登录页")
            
            if page.query_selector('a[href="/auth/login"]'):
                page.click('a[href="/auth/login"]')
                page.wait_for_load_state('networkidle')
            else:
                page.goto(login_url, wait_until='domcontentloaded', timeout=25000)
            time.sleep(3)
            
            # 3. 检查是否已登录
            if '/user' in page.url or page.query_selector('a[href="/user/logout"]'):
                print("✅ 已登录，跳过登录")
            else:
                # 填写表单
                print("📝 填写登录信息")
                page.fill('input[name="email"], input[type="email"]', email)
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
                        """, timeout=30000)
                        print("✅ Turnstile通过")
                    except:
                        print("⚠️ Turnstile超时，尝试继续")
                
                # 点击登录
                page.click('button[type="submit"], input[type="submit"]', timeout=15000)
                page.wait_for_url(f"{URL}/user*", timeout=25000)
                print("✅ 登录成功")
            
            # 4. 🔥 浏览器内执行签到（关键修改！）
            time.sleep(3)
            print("📅 执行签到...")
            
            # 确保在用户中心页面
            if '/user' not in page.url:
                page.goto(f"{URL}/user", wait_until='networkidle', timeout=20000)
                time.sleep(3)
            
            # 查找并点击签到按钮（多种选择器兼容）
            checkin_selectors = [
                '#checkin-btn',
                '.check-in-btn', 
                'button:has-text("签到")',
                'button:has-text("Check In")',
                'button:has-text("checkin")',
                '.layui-btn[lay-filter="checkin"]',
                'a:has-text("签到")'
            ]
            
            clicked = False
            for selector in checkin_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        print(f"🔘 点击签到按钮: {selector}")
                        btn.click()
                        clicked = True
                        break
                except:
                    continue
            
            if not clicked:
                # 尝试JS直接触发签到事件（SSPANEL常见）
                print("🔘 尝试JS触发签到...")
                result = page.evaluate("""
                    () => {
                        // 尝试直接调用SSPANEL的签到函数
                        if (typeof checkin === 'function') {
                            checkin();
                            return 'function_called';
                        }
                        // 尝试触发按钮点击事件
                        const btn = document.querySelector('#checkin-btn, .check-in-btn');
                        if (btn) {
                            btn.click();
                            return 'button_clicked';
                        }
                        return 'not_found';
                    }
                """)
                print(f"🔘 JS执行结果: {result}")
            
            # 5. 等待签到结果
            print("⏳ 等待签到响应...")
            time.sleep(4)
            
            # 提取结果消息（多种可能位置）
            msg_selectors = [
                '.msg', '.alert', '.alert-success', '.layui-layer-content',
                '[role="alert"]', '.swal2-html-container', '#result'
            ]
            
            msg = None
            for selector in msg_selectors:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        text = el.inner_text().strip()
                        if text and len(text) < 200:  # 过滤过长内容
                            msg = text
                            break
                except:
                    continue
            
            # 备用：检查页面是否显示已签到
            if not msg:
                page_text = page.text_content('body').lower()
                if '已签到' in page_text or 'already checked' in page_text or '今日奖励' in page_text:
                    msg = "✅ 签到成功（检测到页面提示）"
            
            # 最终结果
            if msg:
                print(f"🎉 签到结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                # 保存调试信息
                timestamp = int(time.time())
                page.screenshot(path=f'checkin_debug_{timestamp}.png')
                print("📸 已保存签到页截图用于调试")
                result_msg = f"账号 {email}: ❓ 未检测到签到结果（请查看artifact截图）"
                    
        except Exception as e:
            print(f"💥 异常: {str(e)}")
            result_msg = f"账号 {email}: {str(e)[:100]}"
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
        results.append(sign_account_playwright(idx, email, pwd))
        if idx < len(accounts) - 1:
            time.sleep(random.randint(25, 50))  # 多账号间隔更长
    
    if SCKEY and results:
        summary = "📊 iKuuu签到汇总\n\n" + "\n\n".join(results)
        push_notification("机场签到", summary)
    
    print("\n🏁 完成")
