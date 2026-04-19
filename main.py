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
            
            # 2️⃣ 检查是否已登录（通过登出链接或用户头像）
            if page.query_selector('a[href="/user/logout"]') or page.query_selector('.user-avatar'):
                print("✅ 已登录状态")
                # 如果已在用户中心，直接签到
                if '/user' in page.url:
                    print("📍 已在用户中心页面")
                else:
                    # 访问用户中心
                    page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                    time.sleep(5)
            else:
                # 3️⃣ 未登录，填写登录表单
                print("📝 填写登录信息...")
                
                # 等待并填写邮箱
                page.wait_for_selector('input[name="email"]', state='visible', timeout=20000)
                page.fill('input[name="email"]', email)
                
                # 填写密码（兼容两种name）
                try:
                    page.fill('input[name="passwd"]', password)
                except:
                    try:
                        page.fill('input[name="password"]', password)
                    except:
                        print("❌ 未找到密码输入框")
                        return f"账号 {email}: ❌ 登录表单异常"
                
                # 等待Turnstile验证（如果有）
                if page.query_selector('iframe[src*="challenges.cloudflare.com"]'):
                    print("🔄 等待Turnstile验证...")
                    time.sleep(10)
                else:
                    time.sleep(3)
                
                # 4️⃣ 点击登录按钮
                print("🔘 点击登录...")
                page.click('button[type="submit"]', timeout=20000)
                
                # 5️⃣ 等待登录成功（多种方式）
                print("⏳ 等待登录响应...")
                logged_in = False
                
                # 方式1: 等待URL变化
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=25000)
                    logged_in = True
                    print("✅ 登录成功（URL跳转）")
                except:
                    pass
                
                # 方式2: 等待出现登出链接或用户中心元素
                if not logged_in:
                    try:
                        page.wait_for_selector('a[href="/user/logout"], .user-avatar', timeout=15000)
                        logged_in = True
                        print("✅ 登录成功（检测到用户元素）")
                    except:
                        pass
                
                # 方式3: 检查当前页面
                if not logged_in:
                    time.sleep(5)
                    if page.query_selector('a[href="/user/logout"]') or '/user' in page.url:
                        logged_in = True
                        print("✅ 登录成功（页面检查）")
                    else:
                        print("⚠️ 登录可能失败，尝试继续")
                
                # 确保在用户中心
                if '/user' not in page.url:
                    page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                    time.sleep(5)
            
            # 6️⃣ 执行签到 - 关键：使用正确的选择器
            print("📅 执行签到...")
            
            # 🔥 签到按钮选择器（根据你的截图："每日签到"）
            btn_selectors = [
                'button:has-text("每日签到")',
                'a:has-text("每日签到")',
                '[onclick*="checkin"]',
                '[lay-filter="checkin"]',
                '.checkin-btn',
                '#checkin-btn'
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
                except Exception as e:
                    continue
            
            # JS兜底方案
            if not clicked:
                print("🔘 JS触发签到...")
                try:
                    result = page.evaluate("""
                        () => {
                            // 方法1: 查找包含"每日签到"或"签到"的按钮
                            const buttons = document.querySelectorAll('button, a');
                            for (let btn of buttons) {
                                if (btn.textContent.includes('每日签到') || btn.textContent.includes('签到')) {
                                    btn.click();
                                    return 'button_text:' + btn.textContent.trim();
                                }
                            }
                            
                            // 方法2: 调用checkin函数
                            if (typeof checkin === 'function') {
                                checkin();
                                return 'function';
                            }
                            
                            // 方法3: 尝试常见ID/class
                            const elements = [
                                document.getElementById('checkin-btn'),
                                document.querySelector('.checkin-btn'),
                                document.querySelector('[onclick*="checkin"]')
                            ];
                            
                            for (let el of elements) {
                                if (el) {
                                    el.click();
                                    return 'element';
                                }
                            }
                            
                            return 'not_found';
                        }
                    """)
                    print(f"🔘 JS执行结果: {result}")
                    clicked = True
                except Exception as e:
                    print(f"⚠️ JS触发失败: {e}")
            
            # 7️⃣ 等待并提取结果
            print("⏳ 等待签到结果...")
            time.sleep(8)  # 增加等待时间
            
            msg = None
            
            # 尝试多种方式提取结果
            # 方式1: 弹窗消息
            for selector in ['.msg', '.alert', '.layui-layer-content', '.swal2-html-container', '[role="alert"]']:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        text = el.inner_text().strip()
                        if text and len(text) < 200 and ('签到' in text or '获得' in text or '成功' in text):
                            msg = text
                            print(f"📋 提取结果（弹窗）: {msg}")
                            break
                except:
                    continue
            
            # 方式2: 检查页面文本
            if not msg:
                page_text = page.text_content('body')
                if '签到成功' in page_text or '获得' in page_text:
                    # 提取具体流量信息
                    import re
                    match = re.search(r'获得.*?(\d+\.?\d*)\s*(GB|MB)', page_text)
                    if match:
                        msg = f"✅ 签到成功，获得 {match.group(1)}{match.group(2)}"
                    else:
                        msg = "✅ 签到成功"
                    print(f"📋 提取结果（页面）: {msg}")
                elif '今日已签到' in page_text or '已经签到' in page_text:
                    msg = "ℹ️ 今日已签到"
                    print(f"📋 提取结果（已签到）: {msg}")
            
            # 方式3: 检查按钮是否变成"明日再来"
            if not msg:
                try:
                    checkin_btn = page.query_selector('button:has-text("明日再来"), a:has-text("明日再来")')
                    if checkin_btn:
                        msg = "✅ 签到成功（按钮变为'明日再来'）"
                        print(f"📋 提取结果（按钮变化）: {msg}")
                except:
                    pass
            
            if msg:
                print(f"🎉 结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                print("⚠️ 未检测到签到结果，保存截图")
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
