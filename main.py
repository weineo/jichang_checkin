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
        res = requests.post(f"https://sctapi.ftqq.com/{SCKEY}.send", 
                          data={"title": title, "desp": content}, timeout=10)
        print("📤 推送成功" if res.status_code == 200 else f"⚠️ 推送失败: {res.status_code}")
    except:
        pass

def sign_account_playwright(index, email, password):
    print(f"\n{'='*20} 账号 {index+1} {'='*20}")
    print(f"👤 账号: {email}")
    
    result_msg = ""
    
    with sync_playwright() as p:
        # 启动 Chromium（无头模式 + 防检测参数）
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu'
            ]
        )
        
        # 创建带真实 User-Agent 的上下文
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
            timezone_id='Asia/Shanghai'
        )
        
        page = context.new_page()
        
        try:
            # 1. 访问登录页（等待网络空闲，确保 CF 验证加载完成）
            print("🔑 正在加载登录页面...")
            page.goto(f"{URL}/auth/login", wait_until='networkidle', timeout=30000)
            
            # 2. 等待并填写表单（兼容不同 SSPANEL 主题）
            page.wait_for_selector('input[name="email"], input[type="email"]', timeout=10000)
            page.fill('input[name="email"]', email)
            page.fill('input[name="passwd"]', password)
            
            # 3. 关键：等待 Turnstile 验证完成（如果有）
            # 检测是否存在 cf-turnstile 元素
            if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                print("🔄 检测到 Cloudflare Turnstile，等待自动验证...")
                # 等待验证完成（最多 15 秒）
                try:
                    page.wait_for_function(
                        '''() => {
                            const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                            if (!iframe) return true; // 没有 iframe 说明已通过
                            const token = iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                            return token && token.length > 20;
                        }''',
                        timeout=15000
                    )
                    print("✅ Turnstile 验证通过")
                except PlaywrightTimeout:
                    print("⚠️ Turnstile 验证超时，尝试继续提交")
            
            # 4. 点击登录
            page.click('button[type="submit"], .login-btn, #login-btn', timeout=10000)
            
            # 5. 等待登录响应（跳转或出现用户中心元素）
            page.wait_for_url(f"{URL}/user", timeout=15000)
            print("✅ 登录成功")
            
            # 6. 执行签到
            time.sleep(2)
            print("📅 正在签到...")
            
            # 方法A：直接请求签到接口（需要提取登录后的 cookie）
            cookies = context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            import requests
            session = requests.Session()
            session.headers.update({
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'origin': URL,
                'referer': f"{URL}/user",
                'cookie': cookie_str
            })
            
            res = session.post(f"{URL}/user/checkin", timeout=15)
            if res.text.strip().startswith('{'):
                data = res.json()
                msg = data.get('msg', '签到完成')
                print(f"🎉 签到结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                # 方法B：如果接口失败，尝试点击页面签到按钮
                page.goto(f"{URL}/user", wait_until='networkidle')
                if page.query_selector('#checkin-btn, .check-in-btn, button:has-text("签到")'):
                    page.click('#checkin-btn, .check-in-btn, button:has-text("签到")')
                    page.wait_for_load_state('networkidle')
                    # 尝试从页面提取结果
                    result_text = page.text_content('.msg, .alert, #result') or "签到请求已发送"
                    print(f"🎉 签到结果: {result_text}")
                    result_msg = f"账号 {email}: {result_text}"
                else:
                    result_msg = f"账号 {email}: 未找到签到按钮"
                    
        except Exception as e:
            print(f"💥 签到失败: {str(e)}")
            result_msg = f"账号 {email}: {str(e)}"
            # 保存截图用于调试（GitHub Actions 中会上传为 artifact）
            try:
                page.screenshot(path=f'error_{index}.png')
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
            delay = random.randint(15, 40)  # 浏览器操作更慢，间隔加长
            print(f"⏳ 等待 {delay} 秒后处理下一个账号...")
            time.sleep(delay)
    
    if SCKEY and results:
        summary = "📊 机场签到汇总 (Playwright)\n\n" + "\n\n".join(results)
        push_notification("机场每日签到", summary)
    
    print("\n🏁 全部流程执行完毕")
