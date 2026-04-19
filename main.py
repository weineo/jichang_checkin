#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import random
import re
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
    except:
        pass

def wait_for_turnstile_complete(page, timeout=90):
    """等待Cloudflare Turnstile验证完全完成（包括token生成）"""
    print("🔄 等待Turnstile验证...")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            # 🔥 关键：检查token是否生成（不仅仅是iframe消失）
            token = page.evaluate("""
                () => {
                    const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    if (!iframe) {
                        // iframe消失后，检查是否有token
                        const inputs = document.querySelectorAll('input[name*="cf-turnstile"]');
                        for (let input of inputs) {
                            if (input.value && input.value.length > 20) {
                                return input.value;
                            }
                        }
                        return null;
                    }
                    try {
                        return iframe.contentWindow?.document.querySelector('input[name="cf-turnstile-response"]')?.value;
                    } catch { return null; }
                }
            """)
            
            if token and len(token) > 20:
                print(f"✅ Turnstile完成（token长度: {len(token)}）")
                return True
            
            # 检查iframe是否消失
            iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if not iframe:
                print("⏳ Turnstile iframe消失，等待token生成...")
                time.sleep(3)  # 等待token生成
                continue
                
        except Exception as e:
            print(f"⚠️ Turnstile检查异常: {e}")
        
        print(f"⏳ Turnstile验证中... ({int(time.time()-start)}s)")
        time.sleep(3)
    
    print("⚠️ Turnstile等待超时，尝试继续")
    return False

def close_all_popups(page):
    """关闭所有可能的弹窗"""
    try:
        # 查找并点击所有弹窗的确定按钮
        popup_buttons = [
            'button:has-text("OK")',
            'button:has-text("确定")',
            'button:has-text("关闭")',
            '.swal2-confirm',
            '.layui-layer-btn0'
        ]
        
        for selector in popup_buttons:
            try:
                buttons = page.query_selector_all(selector)
                for btn in buttons:
                    if btn.is_visible():
                        btn.click()
                        print(f"🔘 关闭弹窗: {selector}")
                        time.sleep(1)
            except:
                continue
        
        # 使用JS关闭常见弹窗
        page.evaluate("""
            () => {
                // 关闭SweetAlert
                if (typeof Swal !== 'undefined') Swal.close();
                // 关闭Layui弹窗
                if (typeof layer !== 'undefined') layer.closeAll();
                // 点击所有确定按钮
                document.querySelectorAll('button').forEach(btn => {
                    if (btn.textContent.includes('OK') || btn.textContent.includes('确定')) {
                        btn.click();
                    }
                });
            }
        """)
        return True
    except:
        return False

def is_logged_in(page):
    """检查是否已登录"""
    try:
        if '/user' in page.url:
            return True
        if page.query_selector('a[href="/user/logout"]'):
            return True
        if page.query_selector('.user-avatar, .user-info, #user-center'):
            return True
        if page.query_selector('button:has-text("每日签到"), button:has-text("明日再来"), #checkin-btn'):
            return True
        return False
    except:
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
            time.sleep(5)
            
            # 2️⃣ 检查是否已登录
            if is_logged_in(page):
                print("✅ 已登录状态")
                if '/user' not in page.url:
                    page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                    time.sleep(5)
            else:
                # 3️⃣ 填写登录表单
                print("📝 填写登录信息...")
                
                # 等待邮箱输入框
                page.wait_for_selector('input[name="email"]', state='visible', timeout=25000)
                page.fill('input[name="email"]', email)
                
                # 填写密码
                try:
                    page.fill('input[name="passwd"]', password)
                except:
                    page.fill('input[name="password"]', password)
                
                # 🔥 关键：等待Turnstile验证完全完成（包括token生成）
                turnstile_success = wait_for_turnstile_complete(page, timeout=90)
                
                if not turnstile_success:
                    print("❌ Turnstile验证失败")
                    page.screenshot(path=f'turnstile_failed_{int(time.time())}.png')
                    return f"账号 {email}: ❌ 验证码验证失败"
                
                time.sleep(3)  # 额外等待确保验证生效
                
                # 🔥 关键：关闭可能的身份验证弹窗
                print("🔍 检查身份验证弹窗...")
                close_all_popups(page)
                time.sleep(2)
                
                # 4️⃣ 点击登录
                print("🔘 点击登录...")
                page.click('button[type="submit"]', timeout=20000)
                
                # 🔥 关键：关闭登录后的弹窗
                time.sleep(3)
                close_all_popups(page)
                time.sleep(2)
                
                # 5️⃣ 等待登录成功（多重检测）
                print("⏳ 等待登录响应...")
                logged_in = False
                
                # 方式1: 等待URL
                try:
                    page.wait_for_url(f"{URL}/user*", timeout=30000)
                    logged_in = True
                    print("✅ 登录成功（URL）")
                except PlaywrightTimeout:
                    pass
                
                # 方式2: 等待元素
                if not logged_in:
                    try:
                        page.wait_for_selector('a[href="/user/logout"], #checkin-btn', timeout=20000)
                        logged_in = True
                        print("✅ 登录成功（元素）")
                    except PlaywrightTimeout:
                        pass
                
                # 方式3: 轮询检查
                if not logged_in:
                    for _ in range(10):
                        if is_logged_in(page):
                            logged_in = True
                            print("✅ 登录成功（轮询）")
                            break
                        time.sleep(2)
                
                if not logged_in:
                    print("❌ 登录失败，保存截图")
                    page.screenshot(path=f'login_failed_{int(time.time())}.png')
                    return f"账号 {email}: ❌ 登录失败"
                
                # 确保在用户中心
                if '/user' not in page.url:
                    page.goto(f"{URL}/user", wait_until='networkidle', timeout=30000)
                    time.sleep(5)
            
            # 🔥 关键：关闭用户中心弹窗
            print("🔍 检查用户中心弹窗...")
            if close_all_popups(page):
                print("✅ 弹窗已关闭")
            time.sleep(2)
            
            # 6️⃣ 执行签到
            print("📅 执行签到...")
            
            # 签到按钮选择器
            btn_selectors = [
                'button:has-text("每日签到")',
                'a:has-text("每日签到")',
                '#checkin-btn',
                '.checkin-btn',
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
            
            # JS兜底
            if not clicked:
                print("🔘 JS触发签到...")
                try:
                    result = page.evaluate("""
                        () => {
                            // 查找签到按钮
                            const buttons = document.querySelectorAll('button, a');
                            for (let btn of buttons) {
                                const text = btn.textContent.trim();
                                if (text.includes('每日签到') || text.includes('签到')) {
                                    btn.click();
                                    return 'button:' + text;
                                }
                            }
                            // 调用函数
                            if (typeof checkin === 'function') {
                                checkin();
                                return 'function';
                            }
                            return 'not_found';
                        }
                    """)
                    print(f"🔘 JS结果: {result}")
                except Exception as e:
                    print(f"⚠️ JS失败: {e}")
            
            # 关闭签到结果弹窗
            time.sleep(1)
            close_all_popups(page)
            
            # 7️⃣ 提取结果
            print("⏳ 等待结果...")
            time.sleep(8)
            
            msg = None
            
            # 弹窗消息
            for selector in ['.msg', '.alert', '.layui-layer-content', '.swal2-html-container']:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        text = el.inner_text().strip()
                        if text and len(text) < 200:
                            msg = text
                            break
                except:
                    continue
            
            # 页面文本
            if not msg:
                page_text = page.text_content('body')
                if '签到成功' in page_text or '获得' in page_text:
                    match = re.search(r'获得.*?(\d+\.?\d*)\s*(GB|MB)', page_text)
                    msg = f"✅ 签到成功，获得 {match.group(1)}{match.group(2)}" if match else "✅ 签到成功"
                elif '今日已签到' in page_text:
                    msg = "ℹ️ 今日已签到"
            
            # 按钮变化
            if not msg:
                try:
                    if page.query_selector('button:has-text("明日再来")'):
                        msg = "✅ 签到成功（按钮变化）"
                except:
                    pass
            
            if msg:
                print(f"🎉 结果: {msg}")
                result_msg = f"账号 {email}: {msg}"
            else:
                print("⚠️ 未检测到结果")
                page.screenshot(path=f'checkin_{int(time.time())}.png')
                result_msg = f"账号 {email}: ❓ 未检测到结果"
                    
        except Exception as e:
            print(f"💥 异常: {e}")
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
        summary = "📊 iKuuu签到\n\n" + "\n\n".join(results)
        push_notification("机场签到", summary)
    
    print("\n🏁 完成")
