#!/usr/bin/env python
# cron:40 7,21 * * *
# new Env("科研通签到")
# coding=utf-8

"""
AbleSci自动签到脚本 
创建日期：2025年8月8日
更新日期：2025年9月2日 >> 修复日志输出时间为北京时间 ; 修复签到前后用户信息显示 ; 优化登录失败处理 ; 优化签到已签到处理
更新日期：2025年9月3日 >> 保护隐私，不在日志中显示完整邮箱和用户名
更新日期：2026年3月22日 >> 支持本地.env文件; 使用zoneinfo/pytz处理时区; 统一通知器; 修复zoneinfo时区查找失败问题,增加回退机制; 修复已签到处理逻辑; 
作者：daitcl
"""

import os
import sys
import time
import requests
from bs4 import BeautifulSoup
import json
import datetime
from pathlib import Path
from datetime import timezone, timedelta


try:
    from zoneinfo import ZoneInfo
    ZONEINFO_AVAILABLE = True
except ImportError:
    ZONEINFO_AVAILABLE = False

# 环境变量名常量
ENV_ACCOUNTS = "ABLESCI_ACCOUNTS"

def load_env_file():
    """
    加载脚本所在目录的 .env 文件，支持两种格式：
    1. 标准键值对：KEY=VALUE
    2. 无键账号行：直接写 邮箱:密码（自动归入 ABLESCI_ACCOUNTS）
    
    优先级：系统环境变量 > .env 中的标准键值对 > .env 中的无键账号行
    重复键会合并（用换行符连接）
    """
    script_dir = Path(__file__).parent
    env_file = script_dir / ".env"
    if not env_file.exists():
        return

    env_vars = {}
    account_lines = []

    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key in env_vars:
                    env_vars[key] = env_vars[key] + "\n" + value
                else:
                    env_vars[key] = value
            else:
                account_lines.append(line)

    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value
            print(f"已从 {env_file} 设置环境变量: {key}")
        else:
            print(f"环境变量 {key} 已存在（来自系统），跳过 .env 中的值")

    if ENV_ACCOUNTS not in os.environ and ENV_ACCOUNTS not in env_vars:
        if account_lines:
            accounts_value = "\n".join(account_lines)
            os.environ[ENV_ACCOUNTS] = accounts_value
            print(f"已从 {env_file} 的无键行设置 {ENV_ACCOUNTS}（共 {len(account_lines)} 个账号）")
    elif account_lines:
        print(f"警告：存在无键账号行，但 {ENV_ACCOUNTS} 已通过系统或标准键值对设置，忽略无键行")

    if ENV_ACCOUNTS in os.environ:
        val = os.environ[ENV_ACCOUNTS]
        # 2026-09-15 隐私修复：原版直接按原样打印账号行，会把密码明文写进
        # cron 输出/日志。现在只保留邮箱前缀，密码一律隐去。
        safe_lines = []
        for line in val.splitlines():
            if ":" in line:
                local = line.split(":", 1)[0].strip()
                safe_lines.append((local[:2] + "***") if local else "***")
            else:
                safe_lines.append("***")
        safe = " / ".join(safe_lines)
        print(f"当前 {ENV_ACCOUNTS} 内容预览: {safe}（共 {len(safe_lines)} 个账号，密码已隐藏）")

load_env_file()

def get_beijing_time():
    """
    返回北京时间（带时区信息）
    优先使用 zoneinfo，如果失败则尝试 pytz，最后使用手动 UTC+8 偏移。
    """
    if ZONEINFO_AVAILABLE:
        try:
            return datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            pass
        
    try:
        import pytz
        return datetime.datetime.now(pytz.timezone("Asia/Shanghai"))
    except ImportError:
        pass

    return datetime.datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))

def protect_privacy(text):
    """保护隐私信息，隐藏部分邮箱和用户名"""
    if not text:
        return text
        
    # 邮箱隐私处理
    if "@" in text:
        parts = text.split("@")
        if len(parts[0]) > 2:
            protected_local = parts[0][:2] + "***"
        else:
            protected_local = "***"
        return f"{protected_local}@{parts[1]}"
    
    # 用户名隐私处理
    if len(text) > 2:
        return text[:2] + "***"
    else:
        return "***"

class Notifier:
    def __init__(self, title="科研通签到"):
        self.log_content = []
        self.title = title
        self.notify_enabled = False
        
        try:
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            from sendNotify import send
            self.send = send
            self.notify_enabled = True
        except ImportError:
            try:
                parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.append(parent_dir)
                from sendNotify import send
                self.send = send
                self.notify_enabled = True
            except Exception as e:
                self.log(f"导入通知模块失败: {str(e)}", "warning")
                self.notify_enabled = False
    
    def log(self, message, level="info"):
        """格式化日志输出并保存到内容 - 使用北京时间"""
        beijing_time = get_beijing_time()
        timestamp = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        
        level_map = {
            "info": "ℹ️",
            "success": "✅",
            "error": "❌",
            "warning": "⚠️"
        }
        symbol = level_map.get(level, "ℹ️")
        log_message = f"[{timestamp}] {symbol} {message}"
        print(log_message)
        self.log_content.append(log_message)
        
    def send_notification(self):
        """发送通知"""
        if not self.notify_enabled:
            self.log("通知功能未启用", "warning")
            return False
            
        content = "\n".join(self.log_content)
        
        try:
            self.send(self.title, content)
            self.log("通知发送成功", "success")
            return True
        except Exception as e:
            self.log(f"发送通知失败: {str(e)}", "error")
            return False
    
    def get_content(self):
        """获取日志内容"""
        return "\n".join(self.log_content)

class AbleSciAuto:
    def __init__(self, email, password, notifier=None):
        self.session = requests.Session()
        self.email = email
        self.password = password
        self.username = None
        self.points = None
        self.sign_days = None
        self.notifier = notifier if notifier else Notifier()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "X-Requested-With": "XMLHttpRequest"
        }
        self.start_time = time.time()
        protected_email = protect_privacy(self.email)
        self.log(f"处理账号: {protected_email}", "info")
        
    def log(self, message, level="info"):
        """代理日志到通知系统"""
        self.notifier.log(message, level)
        
    def get_csrf_token(self):
        """获取CSRF令牌

        2026-09-15 站点前端改版：登录页不再输出 <input name="_csrf">，
        令牌改为 <meta name="csrf-token" content="...">（同名 input 由 JS 在提交前填充，
        静态抓取拿不到），同时新增 iframe 图形验证码（仅风控命中时触发）与
        /site/confirm-login 二次确认（实测签到不需要走这一步）。
        保留 input[_csrf] 作为回退，防止站点再改回去。
        """
        login_url = "https://www.ablesci.com/site/login"
        try:
            response = self.session.get(login_url, headers=self.headers, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                token = ''
                meta = soup.find('meta', {'name': 'csrf-token'})
                if meta and meta.get('content'):
                    token = meta.get('content', '').strip()
                if not token:
                    csrf_input = soup.find('input', {'name': '_csrf'})
                    if csrf_input:
                        token = csrf_input.get('value', '')
                if token:
                    return token
                self.log("CSRF令牌元素未找到（meta[csrf-token] 与 input[_csrf] 均缺失，页面结构可能再次变更）", "error")
            else:
                self.log(f"获取CSRF令牌失败，状态码: {response.status_code}", "error")
        except Exception as e:
            self.log(f"获取CSRF令牌时出错: {str(e)}", "error")
        return ''

    def login(self):
        """执行登录操作"""
        if not self.email or not self.password:
            self.log("邮箱或密码为空", "error")
            return False
            
        login_url = "https://www.ablesci.com/site/login"
        csrf_token = self.get_csrf_token()
        
        if not csrf_token:
            self.log("无法获取CSRF令牌", "error")
            return False
        
        login_data = {
            "_csrf": csrf_token,
            "email": self.email,
            "password": self.password,
            "remember": "off"
        }
        
        headers = self.headers.copy()
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["Referer"] = "https://www.ablesci.com/site/login"
        
        try:
            response = self.session.post(
                login_url,
                data=login_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get("code") == 0:
                        self.log(f"登录成功: {result.get('msg')}", "success")
                        return True
                    else:
                        self.log(f"登录失败: {result.get('msg')}", "error")
                except json.JSONDecodeError:
                    if "退出" in response.text:
                        self.log("登录成功", "success")
                        return True
                    else:
                        self.log("登录失败: 无法解析响应", "error")
            else:
                self.log(f"登录请求失败，状态码: {response.status_code}", "error")
        except Exception as e:
            self.log(f"登录过程中出错: {str(e)}", "error")
        return False

    def get_user_info(self):
        """获取用户信息（包括用户名、积分和签到天数）"""
        home_url = "https://www.ablesci.com/"
        headers = self.headers.copy()
        headers["Referer"] = "https://www.ablesci.com/"
        
        try:
            response = self.session.get(home_url, headers=headers, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 用户名
                username_element = soup.select_one('.mobile-hide.able-head-user-vip-username')
                if username_element:
                    self.username = username_element.text.strip()
                    protected_username = protect_privacy(self.username)
                    self.log(f"用户名: {protected_username}", "info")
                else:
                    self.log("无法定位用户名元素", "warning")
                
                # 积分
                points_element = soup.select_one('#user-point-now')
                if points_element:
                    self.points = points_element.text.strip()
                    self.log(f"当前积分: {self.points}", "info")
                else:
                    self.log("无法获取积分信息", "warning")
                
                # 连续签到天数
                sign_days_element = soup.select_one('#sign-count')
                if sign_days_element:
                    self.sign_days = sign_days_element.text.strip()
                    self.log(f"连续签到天数: {self.sign_days}", "info")
                else:
                    self.log("无法获取连续签到天数", "warning")
                
                return True
            else:
                self.log(f"获取首页失败，状态码: {response.status_code}", "error")
        except Exception as e:
            self.log(f"获取用户信息时出错: {str(e)}", "error")
        return False

    def sign_in(self):
        """执行签到操作 - 处理已签到情况"""
        sign_url = "https://www.ablesci.com/user/sign"
        headers = self.headers.copy()
        headers["Referer"] = "https://www.ablesci.com/"
        
        try:
            response = self.session.get(sign_url, headers=headers, timeout=30)
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get("code") == 0:
                        msg = result.get("msg", "").replace("签到成功，", "", 1)
                        self.log(f"签到成功: {result.get('msg')}", "success")
                        
                        data = result.get("data", {})
                        if data:
                            if "points" in data:
                                self.points = data["points"]
                                self.log(f"更新积分: {self.points}", "info")
                            if "sign_days" in data:
                                self.sign_days = data["sign_days"]
                                self.log(f"更新连续签到天数: {self.sign_days}", "info")
                        return True
                    else:
                        msg = result.get('msg', '')
                        if "您今天已于" in msg:
                            msg = result.get("msg", "").replace("签到失败，", "", 1)
                            self.log(f"今日已签到: {msg}", "info")
                            return True
                        else:
                            self.log(f"签到失败: {msg}", "error")
                except json.JSONDecodeError:
                    self.log("签到响应不是有效的JSON", "error")
            else:
                self.log(f"签到请求失败，状态码: {response.status_code}", "error")
        except Exception as e:
            self.log(f"签到过程中出错: {str(e)}", "error")
        return False

    def display_summary(self, is_before_sign=False):
        """显示执行摘要"""
        elapsed = round(time.time() - self.start_time, 2)
        title = "签到前信息" if is_before_sign else "签到后信息"
        self.log("=" * 50)
        self.log(f"用户 {protect_privacy(self.username)} {title}:")
        if self.username:
            self.log(f"  • 用户名: {protect_privacy(self.username)}")
        if self.points:
            self.log(f"  • 当前积分: {self.points}")
        if self.sign_days:
            self.log(f"  • 连续签到: {self.sign_days}天")
        self.log(f"  • 执行耗时: {elapsed}秒")
        self.log("=" * 50)
        self.log("")

    def run(self):
        """执行完整的登录和签到流程"""
        if self.login():
            self.get_user_info()
            self.display_summary(is_before_sign=True)
            
            sign_result = self.sign_in()
            
            if sign_result:
                self.log("签到完成，刷新用户信息...", "info")
                time.sleep(2)
                self.get_user_info()
                self.display_summary(is_before_sign=False)
        
        return self.notifier.get_content()

def get_accounts():
    """从环境变量获取所有账号"""
    accounts_env = os.getenv(ENV_ACCOUNTS)
    if not accounts_env:
        return []
    
    # 2026-09-15 隐私修复：原版此处打印原始账号串（含明文密码），已移除
    accounts = []
    # 支持换行符、分号、逗号分隔
    for line in accounts_env.splitlines():
        line = line.strip()
        if not line:
            continue
        if ";" in line:
            accounts.extend(line.split(";"))
        elif "," in line:
            accounts.extend(line.split(","))
        else:
            accounts.append(line)
    
    valid_accounts = []
    for account in accounts:
        account = account.strip()
        if not account:
            continue
        # 支持邮箱和密码用冒号、分号或竖线分隔
        if ":" in account:
            email, password = account.split(":", 1)
        elif ";" in account:
            email, password = account.split(";", 1)
        elif "|" in account:
            email, password = account.split("|", 1)
        else:
            print(f"警告：跳过格式错误的账号项: {account}")
            continue
            
        email = email.strip()
        password = password.strip()
        if email and password:
            valid_accounts.append((email, password))
        else:
            print(f"警告：账号或密码为空: {email}:{password}")
    
    return valid_accounts

def main():
    """主函数，处理多账号签到，统一通知器"""
    # 创建全局通知器，所有账号共享
    global_notifier = Notifier("科研通多账号签到")
    global_notifier.log("科研通多账号签到任务开始", "info")
    
    accounts = get_accounts()
    account_count = len(accounts)
    
    if account_count == 0:
        global_notifier.log("未找到有效的账号配置", "error")
        global_notifier.log(f"请设置环境变量 {ENV_ACCOUNTS}，格式为：邮箱1:密码1[换行]邮箱2:密码2", "warning")
        if global_notifier.notify_enabled:
            global_notifier.send_notification()
        return
    
    global_notifier.log(f"找到 {account_count} 个账号", "info")
    
    for i, (email, password) in enumerate(accounts, 1):
        global_notifier.log(f"\n===== 开始处理第 {i}/{account_count} 个账号 =====", "info")
        
        automator = AbleSciAuto(email, password, notifier=global_notifier)
        automator.run()
        
        global_notifier.log(f"===== 完成第 {i}/{account_count} 个账号处理 =====", "info")
    
    global_notifier.log("\n===== 所有账号处理完成 =====", "info")
    
    if global_notifier.notify_enabled:
        global_notifier.send_notification()
    
    if os.getenv("GITHUB_ACTIONS") == "true":
        content = global_notifier.get_content()
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'log_content<<EOF\n{content}\nEOF\n')
        print(f"::set-output name=log_content::{global_notifier.get_content()}")

if __name__ == "__main__":
    main()
