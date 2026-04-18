#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import time
import random
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
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send", 
                          data={"title": title, "desp": content}, 
                          timeout=10)
        if res.status_code == 200:
            print("📤 推送成功")
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
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--no-zygote',
                '--disable-blink-features=AutomationControlled'
            ]
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
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)
        
        page = context.new_page()
        
        try:
            # 1. 先访问主页（触发 Cloudflare 验证）
            print(f"🏠 正在访问主页: {URL}")
            page.goto(URL, wait_until='networkidle', timeout=30000)
            time.sleep(3)  # 等待 CF 验证完成
            
            # 检查是否被 CF 拦截
            html = page.content().lower()
            if 'cloudflare' in html and 'checking your browser' in html:
                print("🔄 检测到 Cloudflare 验证，等待中...")
                time.sleep(10)  # 等待 CF 自动验证
            
            # 2. 点击登录或跳转到登录页
            login_url = f"{URL}/auth/login"
            print(f"🔑 正在访问登录页: {login_url}")
            
            # 尝试点击登录按钮（如果有）
            if page.query_selector('a[href="/auth/login"], a:has-text("登录"), a:has-text("Login")'):
                page.click('a[href="/auth/login"], a:has-text("登录"), a:has-text("Login")')
                page.wait_for_load_state('networkidle')
            else:
                # 直接导航到登录页
                page.goto(login_url, wait_until='domcontentloaded', timeout=30000)
            
            time.sleep(3)
            
            # 3. 检查是否已登录
            if '/user' in page.url or page.query_selector('#user-center, a[href="/user/logout"]'):
                print("✅ 检测到已登录状态，跳过登录")
            else:
                # 4. 查找并填写登录表单
                print("📝 正在查找登录表单...")
                
                # 邮箱输入框（多种可能）
                email_selectors = [
                    'input[name="email"]',
                    'input[name="username"]',
                    'input[type="email"]',
                    'input#email'
                ]
                
                email_input = None
                for selector in email_selectors:
                    try:
                        email_input = page.wait_for_selector(selector, state='visible', timeout=8000)
                        print(f"✅ 找到邮箱输入框")
                        break
                    except PlaywrightTimeout:
                        continue
                
                if not email_input:
                    raise Exception("❌ 未找到邮箱输入框，页面可能未完全加载")
                
                # 填写邮箱
                email_input.fill(email)
                
                # 密码输入框
                pwd_selectors = [
                    'input[name="passwd"]',
                    'input[name="password"]',
                    'input[type="password"]',
                    'input#password'
                ]
                
                for selector in pwd_selectors:
                    try:
                        page.fill(selector, password, timeout=5000)
                        print(f"✅ 填写密码")
                        break
                    except:
                        continue
                
                # 5. 等待并处理 Turnstile 验证（如果有）
                if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                    print("🔄 检测到 Turnstile，等待验证...")
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
                        print("⚠️ Turnstile 超时，尝试继续")
                
                # 6. 点击登录按钮
                print("🔘 正在提交登录...")
                login_btn_selectors = [
                    'button[type="submit"]',
                    'button:has-text("登录")',
                    'button:has-text("Login")',
                    'input[type="submit"]'
                ]
                
                for selector in login_btn_selectors:
                    try:
                        if page.query_selector(selector):
                            page.click(selector, timeout=10000)
                            print("✅ 点击登录按钮")
                            break
                    except:
                        continue
                
                # 7. 等待登录完成
                print("⏳ 等待登录响应...")
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=20000)
                    print("✅ 登录成功")
                except PlaywrightTimeout:
                    if page.query_selector('#user-center, a[href="/user/logout"]'):
                        print("✅ 登录成功（检测到用户元素）")
                    else:
                        raise Exception("❌ 登录无响应，请检查账号密码")
            
            # 8. 执行签到
            time.sleep(2)
            print("📅 正在签到...")
            
            # 提取 Cookie
            cookies = context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            import requests
            session = requests.Session()
            session.headers.update({
                'user-agent': user_agent,
                'origin': URL,
                'referer': f"{URL}/user",
                'cookie': cookie_str,
                'x-requested-with': 'XMLHttpRequest'
            })
            
            # 签到请求
            try:
                res = session.post(f"{URL}/user/checkin", timeout=15)
                if res.text.strip().startswith('{'):
                    data = res.json()
                    msg = data.get('msg', data.get('message', '签到完成'))
                    print(f"🎉 签到结果: {msg}")
                    result_msg = f"账号 {email}: {msg}"
                else:
                    result_msg = f"账号 {email}: 签到请求返回非JSON数据"
                    print(f"⚠️ {result_msg}")
            except Exception as e:
                result_msg = f"账号 {email}: 签到失败 - {str(e)[:50]}"
                print(f"💥 {result_msg}")
                    
        except Exception as e:
            print(f"💥 签到失败: {str(e)}")
            result_msg = f"账号 {email}: {str(e)[:100]}"
            
            # 保存调试信息
            try:
                timestamp = int(time.time())
                page.screenshot(path=f'error_{timestamp}.png')
                with open(f'error_{timestamp}.html', 'w', encoding='utf-8') as f:
                    f.write(page.content())
                print("📸 错误截图已保存")
            except:
                pass
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
        summary = "📊 机场签到汇总\n\n" + "\n\n".join(results)
        push_notification("机场每日签到", summary)
    
    print("\n🏁 全部流程执行完毕")
