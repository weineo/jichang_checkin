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
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    
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
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
        """)
        
        page = context.new_page()
        
        try:
            # 1️⃣ 访问主页（触发CF验证）- 关键步骤
            print(f"🏠 访问主页，等待CF验证...")
            page.goto(URL, wait_until='networkidle', timeout=60000)
            
            # 🔥 关键：等待Cloudflare验证完成（检测是否还有CF页面）
            for i in range(10):  # 最多等50秒
                html = page.content().lower()
                if 'cloudflare' not in html and 'checking your browser' not in html:
                    print(f"✅ CF验证完成（第{i+1}次检测）")
                    break
                print(f"⏳ 等待CF验证... ({i+1}/10)")
                time.sleep(5)
            
            # 再等3秒确保页面完全加载
            time.sleep(3)
            
            # 2️⃣ 检查是否已登录（在主页）
            page_url = page.url
            if '/user' in page_url:
                print("✅ 主页已是用户中心（已登录）")
            else:
                # 3️⃣ 未登录，寻找登录入口
                print("🔍 寻找登录入口...")
                
                # 尝试点击登录链接
                login_selectors = [
                    'a[href*="/auth/login"]',
                    'a[href*="/login"]',
                    'a:has-text("登录")',
                    'a:has-text("Login")',
                    'a:has-text("登录/注册")'
                ]
                
                login_clicked = False
                for selector in login_selectors:
                    try:
                        if page.query_selector(selector):
                            page.click(selector)
                            page.wait_for_load_state('networkidle')
                            print(f"✅ 点击登录链接: {selector}")
                            login_clicked = True
                            break
                    except:
                        continue
                
                if not login_clicked:
                    # 直接访问登录页
                    print("🔑 直接访问登录页...")
                    page.goto(f"{URL}/auth/login", wait_until='domcontentloaded', timeout=20000)
                    time.sleep(3)
                
                # 4️⃣ 填写登录表单
                print("📝 填写登录信息...")
                try:
                    page.fill('input[name="email"], input[type="email"]', email)
                    page.fill('input[name="passwd"], input[type="password"]', password)
                    
                    # 等待Turnstile验证
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
                            print("⚠️ Turnstile超时，继续尝试")
                            time.sleep(5)
                    
                    # 点击登录按钮
                    page.click('button[type="submit"], input[type="submit"]', timeout=15000)
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    print("✅ 登录成功")
                    time.sleep(3)
                    
                except Exception as e:
                    print(f"❌ 登录失败: {e}")
                    page.screenshot(path=f'login_error_{int(time.time())}.png')
                    result_msg = f"账号 {email}: ❌ 登录失败"
                    return result_msg
            
            # 5️⃣ 确保在用户中心
            if '/user' not in page.url:
                print("🔄 跳转到用户中心...")
                page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                time.sleep(5)
            
            # 检查是否404
            page_title = page.title().lower()
            if '404' in page_title:
                print("❌ 用户中心返回404，可能是IP被封或CF拦截")
                page.screenshot(path=f'user_404_{int(time.time())}.png')
                result_msg = f"账号 {email}: ❌ 无法访问用户中心（404）"
                return result_msg
            
            # 6️⃣ 执行签到 - 关键：点击右上角"明日再来"按钮
            print("📅 执行签到...")
            
            # 签到按钮选择器（根据你的截图，在右上角）
            checkin_selectors = [
                'button:has-text("明日再来")',
                'a:has-text("明日再来")',
                '#checkin-btn',
                '.check-in-btn',
                'button:has-text("签到")',
                'button:has-text("Check In")',
                '.layui-btn[lay-filter="checkin"]',
                '.btn-checkin',
                '[onclick*="checkin"]',
                '[ng-click*="checkin"]'
            ]
            
            clicked = False
            for selector in checkin_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        print(f"🔘 点击签到: {selector}")
                        btn.click()
                        clicked = True
                        break
                except:
                    continue
            
            # JS兜底方案
            if not clicked:
                print("🔘 JS触发签到...")
                try:
                    page.evaluate("""
                        () => {
                            // 尝试调用SSPANEL的签到函数
                            if (typeof checkin === 'function') {
                                checkin();
                                return 'function';
                            }
                            // 尝试点击按钮
                            const btn = document.querySelector('button:has-text("明日再来"), #checkin-btn, .check-in-btn');
                            if (btn) {
                                btn.click();
                                return 'button';
                            }
                            // 尝试触发事件
                            const links = document.getElementsByTagName('a');
                            for (let link of links) {
                                if (link.textContent.includes('明日再来')) {
                                    link.click();
                                    return 'link';
                                }
                            }
                            return 'not_found';
                        }
                    """)
                    clicked = True
                except Exception as e:
                    print(f"⚠️ JS触发失败: {e}")
            
            # 等待签到响应
            print("⏳ 等待签到结果...")
            time.sleep(6)
            
            # 7️⃣ 提取签到结果
            msg = None
            
            # 尝试多种结果选择器
            result_selectors = [
                '.msg', '.alert', '.alert-success', '.alert-info',
                '.layui-layer-content', '[role="alert"]',
                '.swal2-html-container', '#result', '.toast-message',
                '.notification', '.message'
            ]
            
            for selector in result_selectors:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        text = el.inner_text().strip()
                        if text and len(text) < 200 and not text.isdigit():
                            msg = text
                            print(f"📋 提取结果: {msg}")
                            break
                except:
                    continue
            
            # 检查页面文本
            if not msg:
                page_text = page.text_content('body')
                if '已签到' in page_text or '签到成功' in page_text:
                    msg = "✅ 签到成功"
                elif '今日已签到' in page_text or '明日再来' in page_text:
                    msg = "ℹ️ 今日已签到"
                elif '获得' in page_text and '流量' in page_text:
                    # 提取流量信息
                    import re
                    match = re.search(r'获得.*?(\d+\.?\d*)\s*(GB|MB)', page_text)
                    if match:
                        msg = f"✅ 签到成功，获得 {match.group(1)}{match.group(2)}"
                    else:
                        msg = "✅ 签到成功"
            
            if msg:
                print(f"🎉 签到结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                print("⚠️ 未检测到签到结果，保存截图")
                page.screenshot(path=f'checkin_{int(time.time())}.png')
                result_msg = f"账号 {email}: ❓ 未检测到结果（查看artifact）"
                    
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
        results.append(sign_account(idx, email, pwd))
        if idx < len(accounts) - 1:
            time.sleep(random.randint(30, 60))
    
    if SCKEY and results:
        summary = "📊 iKuuu签到汇总\n\n" + "\n\n".join(results)
        push_notification("机场签到", summary)
    
    print("\n🏁 完成")
