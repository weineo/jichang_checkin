#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import time
import random
import requests
import cloudscraper

# 获取 GitHub Secrets / Workflow 环境变量
URL = os.environ.get('URL', '').rstrip('/')
SCKEY = os.environ.get('SCKEY', '')
EMAIL = os.environ.get('EMAIL', '')
PASSWD = os.environ.get('PASSWD', '')
CONFIG = os.environ.get('CONFIG', '')

def get_accounts():
    """解析账号配置：优先使用 CONFIG，未配置则降级使用 EMAIL+PASSWD"""
    accounts = []
    if CONFIG.strip():
        lines = [line.strip() for line in CONFIG.strip().splitlines() if line.strip()]
        if len(lines) % 2 != 0:
            print("⚠️ CONFIG 格式错误：应为偶数行（一行账号，一行密码）")
            return []
        for i in range(0, len(lines), 2):
            accounts.append((lines[i], lines[i+1]))
    elif EMAIL and PASSWD:
        accounts.append((EMAIL, PASSWD))
    else:
        print("❌ 未配置有效的账号信息（请设置 CONFIG 或 EMAIL+PASSWD）")
    return accounts

def push_notification(title, content):
    """Server酱 V3 推送"""
    if not SCKEY:
        print("⏭️ 未配置 SCKEY，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    payload = {"title": title, "desp": content}
    try:
        res = requests.post(url, data=payload, timeout=10)
        if res.status_code == 200:
            print("📤 推送成功")
        else:
            print(f"⚠️ 推送失败，状态码: {res.status_code}")
    except Exception as e:
        print(f"⚠️ 推送异常: {e}")

def sign_account(index, email, password):
    """执行单个账号的登录与签到"""
    print(f"\n{'='*20} 账号 {index+1} {'='*20}")
    print(f"👤 账号: {email}")

    # 初始化 CloudScraper（自动处理 Cloudflare 5秒盾/JS Challenge）
    session = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        delay=5  # 等待 CF 验证完成
    )

    login_url = f"{URL}/auth/login"
    checkin_url = f"{URL}/user/checkin"

    headers = {
        'origin': URL,
        'referer': f"{URL}/auth/login",
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
    }

    try:
        # 1. 登录
        print("🔑 正在登录...")
        res_login = session.post(login_url, headers=headers, data={'email': email, 'passwd': password}, timeout=15)

        # 防御性检查：如果返回 HTML，说明被 CF 拦截或接口变动
        if res_login.text.strip().startswith('<'):
            raise Exception("❌ 登录被 Cloudflare 拦截，返回了 HTML 页面。")

        login_data = res_login.json()
        if login_data.get('ret', 0) != 1:
            raise Exception(f"登录失败: {login_data.get('msg', '未知错误')}")
        print("✅ 登录成功")

        # 2. 签到 (间隔2秒防风控)
        time.sleep(2)
        print("📅 正在签到...")
        res_checkin = session.post(checkin_url, headers=headers, timeout=15)

        if res_checkin.text.strip().startswith('<'):
            raise Exception("❌ 签到被 Cloudflare 拦截")

        checkin_data = res_checkin.json()
        msg = checkin_data.get('msg', '签到完成')
        print(f"🎉 签到结果: {msg}")
        return f"账号 {email}: {msg}"

    except requests.exceptions.RequestException as e:
        err_msg = f"网络请求异常: {e}"
        print(f"💥 {err_msg}")
        return f"账号 {email}: {err_msg}"
    except json.JSONDecodeError:
        err_msg = "解析响应失败，服务器可能未返回 JSON 数据"
        print(f"💥 {err_msg}")
        return f"账号 {email}: {err_msg}"
    except Exception as e:
        err_msg = str(e)
        print(f"💥 签到失败: {err_msg}")
        return f"账号 {email}: {err_msg}"

if __name__ == '__main__':
    if not URL:
        print("❌ 环境变量 URL 未设置，请在 GitHub Secrets 中配置 URL")
        exit(1)

    accounts = get_accounts()
    if not accounts:
        print("❌ 没有可执行的账号，流程终止")
        exit(1)

    print(f"🚀 共检测到 {len(accounts)} 个账号，开始执行...")
    results = []

    for idx, (email, pwd) in enumerate(accounts):
        result = sign_account(idx, email, pwd)
        results.append(result)
        # 多账号间随机延迟，降低风控概率
        if idx < len(accounts) - 1:
            delay = random.randint(10, 30)
            print(f"⏳ 等待 {delay} 秒后处理下一个账号...")
            time.sleep(delay)

    # 汇总推送
    if SCKEY:
        summary = "📊 机场签到汇总\n\n" + "\n\n".join(results)
        push_notification("机场每日签到", summary)

    print("\n🏁 全部流程执行完毕")
