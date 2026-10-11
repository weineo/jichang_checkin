# iKuuu 签到（Cookie 版）

站点登录页新增了极验 GeeTest v4 交互式验证码，自动化登录已不可行。
本方案绕开登录：签到接口 `POST /user/checkin` 只认登录 Cookie，
所以在本地登录一次把 Cookie 存进仓库 Secret，之后每天由 Actions 直接调接口。

**零验证码、零浏览器、零第三方依赖、零费用。**

## 三步走

1. 本机装一次（只需一次）
   ```
   pip install playwright
   ```
   不用再 `playwright install chromium` —— 脚本会直接调用你本机已有的 **Edge**（找不到才回退 Chrome / 内置 chromium）。

2. 取 Cookie（Cookie 过期时重做这一步）
   ```
   python get_cookie.py
   ```
   在弹出的窗口里正常登录（验证码自己点一下），脚本会自动抓取 Cookie、
   写进 `cookie.txt`、复制到剪贴板，并打开 GitHub Secret 设置页。
   （默认不在终端打印完整 Cookie，需要看就加 `--show`。）

3. 在仓库 `Settings → Secrets and variables → Actions` 新建 Secret：
   - 名称 **`COOKIE`**，值粘贴 `cookie.txt` 里的那一行
   - 多账号：每行一个，用 `python get_cookie.py --append` 追加

之后无需再管，每天 22:32 UTC 自动签到。

## 文件说明

| 文件 | 用途 |
|---|---|
| `checkin.py` | 签到主程序（Cookie 版，仅依赖 requests） |
| `get_cookie.py` | 本地取 Cookie 的小工具 |
| `requirements.txt` | 依赖（只剩 requests） |
| `checkin_solver.py` | 备选：打码平台版（接 Capsolver/2Captcha，需付费，暂未使用） |

## Cookie 什么时候会失效？

脚本每次会检查 Cookie 是否还在登录态；失效时会在日志和推送里明确提示
「请重新运行 get_cookie.py」。登录时勾选 **Remember Me** 可以让 Cookie 活得更久。

只校验不签到（想确认 Cookie 还有效时用）：把 Secret `COOKIE_ONLY_TEST` 设为 `1`。
