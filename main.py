import json, re, os, cloudscraper, time

url = os.environ.get('URL')
config = os.environ.get('CONFIG')
SCKEY = os.environ.get('SCKEY')

login_url = '{}/auth/login'.format(url)
check_url = '{}/user/checkin'.format(url)

def sign(order, user, pwd):
    session = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        delay=5
    )
    global url, SCKEY
    header = {
        'origin': url,
        'user-agent': session.headers['User-Agent']
    }
    data = {'email': user, 'passwd': pwd}
    
    try:
        print(f'===账号{order}进行登录...===')
        print(f'账号：{user}')
        
        res = session.post(url=login_url, headers=header, data=data).text
        
        # 二次校验：如果还是HTML，说明CF验证失败
        if res.strip().startswith('<'):
            raise Exception(f"登录返回HTML，可能被拦截: {res[:150]}")
            
        response = json.loads(res)
        print(response['msg'])
        
        # 签到
        time.sleep(2)  # 避免请求过快
        res2 = session.post(url=check_url, headers=header).text
        result = json.loads(res2)
        print(result['msg'])
        content = result['msg']
        
        # 推送
        if SCKEY:
            push_url = f'https://sctapi.ftqq.com/{SCKEY}.send?title=机场签到&desp={content}'
            requests.post(url=push_url)
            print('推送成功')
            
    except Exception as ex:
        content = '签到失败'
        print(content)
        print(f"出现如下异常: {ex}")
        if SCKEY:
            push_url = f'https://sctapi.ftqq.com/{SCKEY}.send?title=机场签到&desp={content}'
            requests.post(url=push_url)
            print('推送成功')
    finally:
        print(f'===账号{order}签到结束===\n')
        time.sleep(30)  # 多账号间隔

if __name__ == '__main__':
    configs = config.splitlines()
    if len(configs) % 2 != 0 or len(configs) == 0:
        print('配置文件格式错误')
        exit()
    user_quantity = len(configs) // 2
    for i in range(user_quantity):
        user = configs[i*2]
        pwd = configs[i*2+1]
        sign(i, user, pwd)
