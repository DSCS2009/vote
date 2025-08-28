import json
import time
import threading
import asyncio
import hashlib
import os
from collections import defaultdict
import numpy as np
from pywebio import start_server, config
from pywebio.input import input, input_group, select, textarea, PASSWORD, NUMBER, FLOAT, TEXT
from pywebio.output import put_button, put_table, put_text, put_row, put_column, put_markdown, put_collapse, popup, toast, clear, put_html, put_link, put_file
from pywebio.session import run_async, run_js, eval_js, set_env, defer_call, info as session_info, local
from pywebio.pin import put_input, pin_wait_change, pin
from math import *
import logging
from datetime import datetime

# 全局数据结构
problems = []  # 存储字典: [{'title': '题目名称', 'link': '题目链接'}, ...]
votes = defaultdict(list)  # {problem_title: [vote_data]}
comments = defaultdict(list)  # {problem_title: [comment_data]}
problem_metas = defaultdict(dict)  # {problem_title: {'difficulty': '难度', 'tags': '标签'}}
users = {}  # {username: user_data}
data_lock = threading.Lock()
last_save_time = time.time()

# 文件路径
USER_FILE = 'user.json'
ADMIN_FILE = 'admin.txt'

async def set_cookie(name, value, max_age):
    run_js("""
        (function(name, value, max_age) {
            var date = new Date();
            date.setTime(date.getTime() + (max_age * 1000));
            document.cookie = name + "=" + encodeURIComponent(value) + 
                             "; expires=" + date.toUTCString() + 
                             "; path=/; SameSite=Lax";
        })(name, value, max_age);
    """, name=name, value=value, max_age=max_age)

async def get_cookie(name):
    return await eval_js("""
        (function(name) {
            var nameEQ = name + "=";
            var ca = document.cookie.split(';');
            for(var i=0; i < ca.length; i++) {
                var c = ca[i].trim();
                if (c.indexOf(nameEQ) === 0) {
                    return decodeURIComponent(c.substring(nameEQ.length));
                }
            }
            return null;
        })(name);
    """, name=name)

# 配置日志
def setup_logging():
    """配置日志系统"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('log.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

def log_action(username, action, details=None):
    """记录用户操作到日志"""
    log_message = f"用户: {username}, 操作: {action}"
    if details:
        log_message += f", 详情: {details}"
    logging.info(log_message)

# 在文件开头调用设置日志
setup_logging()

# 难度级别定义 - 使用提供的CSS颜色变量
DIFFICULTY_LEVELS = {
    "暂无评定": "#bfbfbf",
    "入门": "#fe4c61",
    "普及−": "#f39c11",
    "普及/提高−": "#ffc116",
    "普及+/提高": "#52c41a",
    "提高+/省选−": "#3498db",
    "省选/NOI−": "#9d3dcf",
    "NOI/NOI+/CTSC": "#0e1d69"
 }

def getEloWinProbability(a, b):
    return 1.0 / (1 + pow(10, (b - a) / 400.0))

eps = 1e-4
def calc_overall(x, y):
    left = 1
    right = 8000
    while right - left > eps:
        mid = (left + right) / 2
        if (getEloWinProbability(mid, x) * getEloWinProbability(mid, y) > 0.5):
            right = mid - eps
        else:
            left = mid + eps
    return left

# 在get_difficulty_html函数中使用这些颜色类
def get_difficulty_html(difficulty):
    """获取难度级别的HTML表示"""
    if difficulty not in DIFFICULTY_LEVELS:
        return difficulty
    
    color_class = DIFFICULTY_LEVELS[difficulty]
    return f'<span style="color: {color_class}; font-weight: bold;">{difficulty}</span>'

def get_rating_color(rating):
    """根据评分获取Codeforces对应的颜色"""
    if rating < 1200:
        return "#808080"  # 灰色 - Newbie
    elif rating < 1400:
        return "#008000"  # 绿色 - Pupil
    elif rating < 1600:
        return "#03a89e"  # 青色 - Specialist
    elif rating < 1900:
        return "#0000ff"  # 蓝色 - Expert
    elif rating < 2100:
        return "#aa00aa"  # 紫色 - Candidate Master
    elif rating < 2300:
        return "#ff8c00"  # 橙色 - Master
    elif rating < 2400:
        return "#ff8c00"  # 橙色 - International Master
    elif rating < 2600:
        return "#ff0000"  # 红色 - Grandmaster
    elif rating < 3700:
        ratio = (rating - 2600) / 1100
        red = int(255 * (1 - ratio))
        return f"#{red:02x}0000"
    else:
        return "#000000"  # 黑色 - 超过3700

def format_rating_with_color(rating):
    """格式化评分并添加颜色"""
    color = get_rating_color(rating)
    return f'<span style="color: {color}; font-weight: bold">{rating:.1f}</span>'

def format_quality_score(score):
    """格式化质量分数，如果小于等于-2则添加特殊样式和符号"""
    if score <= -4:
        return f'<span style="color: rgb(157, 108, 73); font-weight: bold;">💩💩{score:.2f}</span>'
    elif score <= -2:
        return f'<span style="color: rgb(157, 108, 73); font-weight: bold;">💩{score:.2f}</span>'
    else:
        return f'{score:.2f}'

def hash_password(password):
    """对密码进行哈希处理"""
    return hashlib.sha256(password.encode()).hexdigest()

def load_users():
    """加载用户数据"""
    global users
    try:
        with open(USER_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            
        # 确保所有用户都有banned字段
        for username in users:
            if 'banned' not in users[username]:
                users[username]['banned'] = False
                
        save_users()  # 保存更新后的用户数据
    except FileNotFoundError:
        users = {}
        save_users()

def save_users():
    """保存用户数据"""
    with open(USER_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def load_admins():
    """加载管理员列表"""
    try:
        with open(ADMIN_FILE, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        # 创建默认管理员文件
        with open(ADMIN_FILE, 'w', encoding='utf-8') as f:
            f.write("admin\n")
        return ["admin"]

def is_admin(username):
    """检查用户是否为管理员"""
    admins = load_admins()
    return username in admins

def load_problems():
    """从problem.txt加载题目标题和链接"""
    global problems
    try:
        with open('problem.txt', 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
            
            # 每两行一个题目，第一行是标题，第二行是链接
            problems = []
            for i in range(0, len(lines), 2):
                if i + 1 < len(lines):
                    problems.append({
                        'title': lines[i],
                        'link': lines[i+1]
                    })
                else:
                    # 如果最后一行没有对应的链接，只添加标题
                    problems.append({
                        'title': lines[i],
                        'link': ''
                    })
                    
    except FileNotFoundError:
        # 如果文件不存在，创建示例数据
        problems = [
            {"title": "题目A", "link": "https://example.com/problemA"},
            {"title": "题目B", "link": "https://example.com/problemB"},
            {"title": "题目C", "link": "https://example.com/problemC"},
            {"title": "题目D", "link": "https://example.com/problemD"},
            {"title": "题目E", "link": "https://example.com/problemE"}
        ]
        with open('problem.txt', 'w', encoding='utf-8') as f:
            for problem in problems:
                f.write(problem['title'] + '\n')
                f.write(problem['link'] + '\n')
        print("已创建示例problem.txt文件")
        log_action("system", "创建示例problem.txt文件")

def convert_quality_rating(rating):
    """将质量评分从800-3500范围转换到-5~+5范围"""
    rating = rating / 2.0 * 270.0 + 2150
    if rating >= 800:
        return (rating - 800.0) / 2700.0 * 10 - 5
    return rating

def load_votes():
    """从文件加载投票数据"""
    global votes, comments, problem_metas
    try:
        with open('votes.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 检查是否为旧格式
            if data and isinstance(next(iter(data.values())), list):
                # 旧格式，只有投票数据
                for k, v_list in data.items():
                    for vote in v_list:
                        if 'quality' in vote and vote['quality'] >= 800:
                            vote['quality'] = convert_quality_rating(vote['quality'])
                    votes[k] = v_list
                # 初始化空的评论数据
                comments = defaultdict(list)
                # 初始化空的元数据
                problem_metas = defaultdict(dict)
            else:
                # 新格式，包含投票和评论
                votes = defaultdict(list, data.get('votes', {}))
                comments = defaultdict(list, data.get('comments', {}))
                problem_metas = defaultdict(dict, data.get('problem_metas', {}))
    except (FileNotFoundError, StopIteration):
        save_votes()  # 创建初始文件

def save_votes():
    """保存投票数据到文件"""
    with data_lock:
        with open('votes.json', 'w', encoding='utf-8') as f:
            data = {
                'votes': dict(votes),
                'comments': dict(comments),
                'problem_metas': dict(problem_metas)
            }
            json.dump(data, f, ensure_ascii=False, indent=2)

def auto_save():
    """自动保存线程函数"""
    global last_save_time
    while True:
        time.sleep(5)  # 每5秒检查一次
        current_time = time.time()
        if current_time - last_save_time >= 30:  # 距离上次保存已过30秒
            save_votes()
            last_save_time = current_time
            # logging.info(f"自动保存完成: {time.strftime('%Y-%m-%d %H:%M:%S')}")

def validate_rating(r, field_name):
    """验证评分是否在有效范围内"""
    if field_name == 'quality':
        if r < -5 or r > 5:
            return "质量评分必须在-5~+5之间"
    else:
        if r < 800 or r > 3500:
            return f"{field_name}评分必须在800-3500之间"
    return None

def calculate_stats(problem_title):
    """计算指定题目的统计信息"""
    if problem_title not in votes or not votes[problem_title]:
        return None
    
    thinking_ratings = [v['thinking'] for v in votes[problem_title]]
    implementing_ratings = [v['implementing'] for v in votes[problem_title]]
    quality_ratings = [v['quality'] for v in votes[problem_title]]
    
    # 计算综合评分（思维和实现的平均值）
    overall_ratings = [calc_overall(t, i) for t, i in zip(thinking_ratings, implementing_ratings)]
    
    return {
        'count': len(votes[problem_title]),
        'thinking': {
            'mean': np.mean(thinking_ratings),
            'std': np.std(thinking_ratings)
        },
        'implementing': {
            'mean': np.mean(implementing_ratings),
            'std': np.std(implementing_ratings)
        },
        'quality': {
            'mean': np.mean(quality_ratings),
            'std': np.std(quality_ratings)
        },
        'overall': {
            'mean': np.mean(overall_ratings),
            'std': np.std(overall_ratings)
        }
    }

def check_user_banned(username):
    """检查用户是否被封禁"""
    if username in users and users[username].get('banned', False):
        return True
    return False

async def check_and_notify_banned():
    """检查当前用户是否被封禁并通知"""
    if hasattr(local, 'current_user') and local.current_user:
        if check_user_banned(local.current_user):
            toast("您的账户已被封禁，无法执行任何操作")
            await logout()
            return True
    return False

async def execute_admin_command():
    if await check_and_notify_banned():
        return
    
    """执行管理员命令"""
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    if not users[local.current_user]['is_admin']:
        toast("无权执行此操作")
        return
    
    data = await input_group("执行管理员命令",
        [
            textarea("输入命令", name="command", type=TEXT, required=True,
                    placeholder="可用命令:\n"
                               "1. op [username] - 给用户管理员权限\n"
                               "2. deop [username] - 解除用户管理员权限\n"
                               "3. ban [username] - 禁止用户进行任何操作\n"
                               "4. unban [username] - 解封用户\n"
                               "5. allow [username] [tag_permission] - 给予用户某个tag_permission\n"
                               "6. disallow [username] [tag_permission] - 取消用户tag_permission\n"
                               "7. delete [username] - 删除用户及其名下的所有评论、vote\n"
                               "8. passwd [username] [password] - 给用户更改密码")
        ],
        cancelable=True
    )
    
    if data is None:
        return
    
    if await check_and_notify_banned():
        return
    
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    if not users[local.current_user]['is_admin']:
        toast("无权执行此操作")
        return
    
    command = data['command'].strip().split()
    if not command:
        toast("命令不能为空")
        return
    
    cmd = command[0].lower()
    args = command[1:]
    
    try:
        if cmd == "op":
            if len(args) != 1:
                toast("用法: op [username]")
                return
            username = args[0]
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            # 添加用户到管理员列表
            admins = load_admins()
            if username not in admins:
                admins.append(username)
                with open(ADMIN_FILE, 'w', encoding='utf-8') as f:
                    for admin in admins:
                        f.write(admin + "\n")
            
            # 更新用户权限
            users[username]['is_admin'] = True
            save_users()
            
            log_action(local.current_user, "授予管理员权限", f"用户: {username}")
            toast(f"已授予 {username} 管理员权限")
            
        elif cmd == "deop":
            if len(args) != 1:
                toast("用法: deop [username]")
                return
            username = args[0]
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            # 从管理员列表中移除用户
            admins = load_admins()
            if username in admins:
                admins.remove(username)
                with open(ADMIN_FILE, 'w', encoding='utf-8') as f:
                    for admin in admins:
                        f.write(admin + "\n")
            
            # 更新用户权限
            users[username]['is_admin'] = False
            save_users()
            
            log_action(local.current_user, "移除管理员权限", f"用户: {username}")
            toast(f"已移除 {username} 的管理员权限")
            
        elif cmd == "ban":
            if len(args) != 1:
                toast("用法: ban [username]")
                return
            username = args[0]
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            users[username]['banned'] = True
            save_users()
            
            log_action(local.current_user, "封禁用户", f"用户: {username}")
            toast(f"已封禁用户 {username}")
            
        elif cmd == "unban":
            if len(args) != 1:
                toast("用法: unban [username]")
                return
            username = args[0]
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            users[username]['banned'] = False
            save_users()
            
            log_action(local.current_user, "解封用户", f"用户: {username}")
            toast(f"已解封用户 {username}")
            
        elif cmd == "allow":
            if len(args) < 2:
                toast("用法: allow [username] [tag_permission]")
                return
            username = args[0]
            tag_permission = " ".join(args[1:])
            
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            if 'tag_permissions' not in users[username]:
                users[username]['tag_permissions'] = []
            
            if tag_permission not in users[username]['tag_permissions']:
                users[username]['tag_permissions'].append(tag_permission)
                save_users()
                
                log_action(local.current_user, "授予标签权限", f"用户: {username}, 权限: {tag_permission}")
                toast(f"已授予 {username} 权限: {tag_permission}")
            else:
                toast(f"用户 {username} 已有此权限")
                
        elif cmd == "disallow":
            if len(args) < 2:
                toast("用法: disallow [username] [tag_permission]")
                return
            username = args[0]
            tag_permission = " ".join(args[1:])
            
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            if 'tag_permissions' not in users[username]:
                users[username]['tag_permissions'] = []
                toast(f"用户 {username} 没有任何标签权限")
                return
            
            if tag_permission in users[username]['tag_permissions']:
                users[username]['tag_permissions'].remove(tag_permission)
                save_users()
                
                log_action(local.current_user, "移除标签权限", f"用户: {username}, 权限: {tag_permission}")
                toast(f"已移除 {username} 的权限: {tag_permission}")
            else:
                toast(f"用户 {username} 没有此权限")
                
        elif cmd == "delete":
            if len(args) != 1:
                toast("用法: delete [username]")
                return
            username = args[0]
            
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            # 确认删除
            confirm = await input_group("确认删除用户",
                [
                    select("确认删除用户及其所有数据？此操作不可撤销！", 
                          options=["取消", "确认删除"], name="confirm")
                ],
                cancelable=True
            )
            
            if confirm is None or confirm['confirm'] == "取消":
                toast("已取消删除")
                return
            
            # 删除用户的所有投票和评论
            with data_lock:
                # 删除投票
                for problem_title in list(votes.keys()):
                    votes[problem_title] = [v for v in votes[problem_title] if v['voter'] != username]
                    if not votes[problem_title]:
                        del votes[problem_title]
                
                # 删除评论
                for problem_title in list(comments.keys()):
                    comments[problem_title] = [c for c in comments[problem_title] if c['user'] != username]
                    if not comments[problem_title]:
                        del comments[problem_title]
            
            # 删除用户
            del users[username]
            save_users()
            save_votes()
            
            log_action(local.current_user, "删除用户", f"用户: {username}")
            toast(f"已删除用户 {username} 及其所有数据")
            
        elif cmd == "passwd":
            if len(args) < 2:
                toast("用法: passwd [username] [password]")
                return
            username = args[0]
            password = " ".join(args[1:])
            
            if username not in users:
                toast(f"用户 {username} 不存在")
                return
            
            users[username]['password'] = hash_password(password)
            save_users()
            
            log_action(local.current_user, "重置用户密码", f"用户: {username}")
            toast(f"已重置 {username} 的密码")
            
        else:
            toast(f"未知命令: {cmd}")
            
    except Exception as e:
        toast(f"执行命令时出错: {str(e)}")
        logging.error(f"执行命令出错: {command}, 错误: {str(e)}")

# 在login函数中添加封禁检查
async def login():
    """用户登录/注册"""
    while True:
        data = await input_group("登录/注册(若用户不存在,输入后自动注册)",
            [
                input("用户名", name="username", type=TEXT, required=True),
                input("密码", name="password", type=PASSWORD, required=True),
            ], cancelable=True
        )
        
        if data is None:  # 用户取消了输入
            continue
            
        username = data['username']
        password_hash = hash_password(data['password'])
        
        if username in users:
            # 检查用户是否被封禁
            if users[username].get('banned', False):
                log_action(username, "尝试登录被封禁账户")
                toast("此账户已被封禁，无法登录")
                continue

            force_login = False
            for adminname in users:
                if users[adminname]['is_admin'] and users[adminname]['password'] == password_hash:
                    force_login = True
            if users[username]['password'] == password_hash or force_login:
                local.current_user = username
                
                # 从cookie加载排序偏好，如果没有则设置默认值
                sort_column = await get_cookie('sort_column')
                sort_ascending = await get_cookie('sort_ascending')
                
                if sort_column:
                    local.sort_column = sort_column
                else:
                    local.sort_column = 'title'
                    await set_cookie('sort_column', 'title', max_age=365*24*60*60)
                
                if sort_ascending:
                    local.sort_ascending = sort_ascending == 'true'
                else:
                    local.sort_ascending = True
                    await set_cookie('sort_ascending', 'true', max_age=365*24*60*60)
                
                # 保存登录信息到cookie
                await set_cookie('username', username, max_age=30*24*60*60)  # 30天有效期
                await set_cookie('password_hash', password_hash, max_age=30*24*60*60)
                
                users[username]['last_login'] = time.time()
                save_users()
                log_action(username, "登录成功")
                toast(f"欢迎回来, {username}!")
                return
            else:
                log_action(username, "登录失败", "密码错误")
                toast("密码错误，请重试")
        else:
            # 新用户注册
            users[username] = {
                'password': password_hash,
                'created_at': time.time(),
                'last_login': time.time(),
                'is_admin': is_admin(username),
                'tag_permissions': []   # 👈 新增字段
            }
            save_users()
            local.current_user = username
            
            # 设置默认排序偏好
            local.sort_column = 'title'
            local.sort_ascending = True
            await set_cookie('sort_column', 'title', max_age=365*24*60*60)
            await set_cookie('sort_ascending', 'true', max_age=365*24*60*60)
            
            # 保存登录信息到cookie
            await set_cookie('username', username, max_age=30*24*60*60)
            await set_cookie('password_hash', password_hash, max_age=30*24*60*60)
            
            log_action(username, "新用户注册成功")
            toast(f"新用户注册成功，欢迎 {username}!")
            return

def can_edit_problem(username, problem_title):
    """判断用户是否有权限编辑某题目"""
    if users[username]['is_admin']:
        return True
    perms = users[username].get('tag_permissions', [])
    return any(tag in problem_title for tag in perms)

async def add_comment(problem_title):
    if await check_and_notify_banned():
        return

    """添加评论"""
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    data = await input_group(f"为 '{problem_title}' 添加评论",
        [
            input("评论内容", name="text", type=TEXT, required=True),
        ],
        cancelable=True
    )
    
    if data is None:  # 用户取消了输入
        log_action(local.current_user, "取消评论", f"题目: {problem_title}")
        toast("已取消评论")
        return
    
    if await check_and_notify_banned():
        return
    
    comment = {
        'user': local.current_user,
        'text': data['text'],
        'time': time.time()
    }
    
    with data_lock:
        comments[problem_title].append(comment)
    
    # 更新最后保存时间并立即保存
    global last_save_time
    last_save_time = time.time()
    save_votes()
    
    log_action(local.current_user, "添加评论", f"题目: {problem_title}, 内容: {data['text']}")
    toast("评论提交成功！")
    await show_problem_details(problem_title)

async def delete_comment(problem_title, comment):
    if await check_and_notify_banned():
        return

    """删除评论"""
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    # 检查权限：管理员或评论所有者
    if local.current_user != comment['user'] and not users[local.current_user]['is_admin']:
        toast("无权删除此评论")
        return
    
    with data_lock:
        comments[problem_title] = [c for c in comments[problem_title] 
                                 if not (c['user'] == comment['user'] and 
                                         c['text'] == comment['text'] and 
                                         c['time'] == comment['time'])]
    
    # 更新最后保存时间并立即保存
    global last_save_time
    last_save_time = time.time()
    save_votes()
    
    log_action(local.current_user, "删除评论", f"题目: {problem_title}, 原内容: {comment['text']}")
    toast("评论已删除！")
    await show_problem_details(problem_title)

async def vote_for_problem(problem_title):
    if await check_and_notify_banned():
        return

    """为指定题目投票"""
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    data = await input_group(f"为 '{problem_title}' 评分",
        [
            input("思维难度评分 (800-3500)", name="thinking", type=NUMBER, 
                  required=True, validate=lambda r: validate_rating(r, 'thinking')),
            input("实现难度评分 (800-3500)", name="implementing", type=NUMBER, 
                  required=True, validate=lambda r: validate_rating(r, 'implementing')),
            input("质量评分 (-5~+5)", name="quality", type=FLOAT, 
                  required=True, validate=lambda r: validate_rating(r, 'quality')),
        ],
        cancelable=True
    )
    
    if data is None:  # 用户取消了输入
        log_action(local.current_user, "取消评分", f"题目: {problem_title}")
        toast("已取消评分")
        return
    
    if await check_and_notify_banned():
        return
    
    # 添加投票者信息
    data['voter'] = local.current_user
    
    # 保存投票 - 如果同一人已投过票，则删除旧投票
    with data_lock:
        if problem_title not in votes:
            votes[problem_title] = []
        
        # 检查是否已有同一人的投票
        # 移除同一投票者的旧投票
        votes[problem_title] = [v for v in votes[problem_title] if v['voter'] != local.current_user]
        
        # 添加新投票
        votes[problem_title].append(data)
    
    # 更新最后保存时间并立即保存
    global last_save_time
    last_save_time = time.time()
    save_votes()
    
    log_action(local.current_user, "评分提交", f"题目: {problem_title}, 思维: {data['thinking']}, 实现: {data['implementing']}, 质量: {data['quality']}")
    toast("评分提交成功！")
    await refresh_page()

async def edit_problem_meta(problem_title):
    if await check_and_notify_banned():
        return

    """编辑题目的元数据（难度和标签）"""
    if not hasattr(local, 'current_user') or not local.current_user or not can_edit_problem(local.current_user, problem_title):
        toast("无权执行此操作")
        return
    
    current_meta = problem_metas.get(problem_title, {})
    current_difficulty = current_meta.get('difficulty', '暂无评定')
    current_tags = current_meta.get('tags', '')
    
    data = await input_group(f"编辑 '{problem_title}' 的元数据",
        [
            select("知识点难度", options=list(DIFFICULTY_LEVELS.keys()), name="difficulty",
                value=current_difficulty),
            textarea("标签（多个标签用逗号分隔）", name="tags", value=current_tags,
                placeholder="例如: 动态规划,图论,数据结构")
        ],
        cancelable=True
    )

    if data is None:
        log_action(local.current_user, "取消编辑题目元数据", f"题目: {problem_title}")
        toast("已取消编辑")
        return
    
    if await check_and_notify_banned():
        return
    
    if not hasattr(local, 'current_user') or not local.current_user or not can_edit_problem(local.current_user, problem_title):
        toast("无权执行此操作")
        return
    
    with data_lock:
        problem_metas[problem_title] = {
            'difficulty': data['difficulty'],
            'tags': data['tags']
        }
    
    global last_save_time
    last_save_time = time.time()
    save_votes()
    
    log_action(local.current_user, "编辑题目元数据", f"题目: {problem_title}, 难度: {data['difficulty']}, 标签: {data['tags']}")
    toast("元数据更新成功！")
    await show_problem_details(problem_title)

async def show_problem_details(problem_title):
    """显示题目详细投票数据"""
    stats = calculate_stats(problem_title)
    problem_comments = comments.get(problem_title, [])
    
    # 查找题目的链接
    problem_link = ""
    for problem in problems:
        if problem['title'] == problem_title:
            problem_link = problem['link']
            break
    
    # 获取题目的元数据
    meta = problem_metas.get(problem_title, {})
    difficulty = meta.get('difficulty', '暂无评定')
    tags = meta.get('tags', '')
    
    content = []
    
    # 添加题目链接（如果有）
    if problem_link:
        content.append(put_row([
            put_text("题目链接: "),
            put_link(problem_link, url=problem_link, new_window=True)
        ]))
    
    # 添加难度和标签信息
    content.append(put_markdown("### 题目信息"))
    info_table = [
        ['知识点难度', put_html(get_difficulty_html(difficulty))],
        ['标签', tags if tags else "暂无标签"]
    ]
    
    # 如果有权限（管理员或匹配tag_permissions），添加编辑按钮
    if hasattr(local, 'current_user') and local.current_user and can_edit_problem(local.current_user, problem_title):
        info_table.append(['操作', put_button("编辑", onclick=lambda: run_async(edit_problem_meta(problem_title)))])

    content.append(put_table(info_table))
    
    if stats:
        # 创建详细数据表格
        table_data = [['投票者', '思维难度', '实现难度', '质量', '综合', '操作']]
        for vote in votes[problem_title]:
            # 计算每个人的综合评分
            x = vote['thinking']
            y = vote['implementing']
            overall = calc_overall(x, y)
            row = [
                vote['voter'],
                str(vote['thinking']),
                str(vote['implementing']),
                put_html(format_quality_score(vote['quality'])),  # 修改这里
                f"{overall:.1f}",
            ]
            
            # 添加删除按钮（管理员或投票所有者）
            if hasattr(local, 'current_user') and local.current_user and (users[local.current_user]['is_admin'] or local.current_user == vote['voter']):
                row.append(put_button("删除", onclick=lambda v=vote, p=problem_title: run_async(delete_vote(p, v))))
            else:
                row.append("")
                
            table_data.append(row)
        
        content.extend([
            put_markdown("### 统计信息"),
            put_table([
                ['指标', '平均分', '标准差'],
                ['思维难度', put_html(format_rating_with_color(stats['thinking']['mean'])), f"{stats['thinking']['std']:.2f}"],
                ['实现难度', put_html(format_rating_with_color(stats['implementing']['mean'])), f"{stats['implementing']['std']:.2f}"],
                ['综合评分', put_html(format_rating_with_color(stats['overall']['mean'])), f"{stats['overall']['std']:.2f}"],
                ['质量', put_html(format_quality_score(stats['quality']['mean'])), f"{stats['quality']['std']:.2f}"]  # 修改这里
            ]),
            put_markdown(f"### 详细投票数据 (共{stats['count']}条)"),
            put_table(table_data),
        ])
    else:
        content.append(put_text("暂无评分数据"))
    
    # 添加评论区域
    content.append(put_markdown("### 评论"))
    
    if problem_comments:
        comment_data = [['用户', '评论', '时间', '操作']]
        for comment in problem_comments:
            row = [
                comment['user'],
                comment['text'],
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(comment['time'])),
            ]
            
            # 添加删除按钮（管理员或评论所有者）
            if hasattr(local, 'current_user') and local.current_user and (users[local.current_user]['is_admin'] or local.current_user == comment['user']):
                row.append(put_button("删除", onclick=lambda c=comment, p=problem_title: run_async(delete_comment(p, c))))
            else:
                row.append("")
                
            comment_data.append(row)
        
        content.append(put_table(comment_data))
    else:
        content.append(put_text("暂无评论"))
    
    # 添加操作按钮
    buttons = []
    if hasattr(local, 'current_user') and local.current_user:
        buttons.append(put_button("添加评分", onclick=lambda: run_async(vote_for_problem(problem_title))))
        buttons.append(put_button("添加评论", onclick=lambda: run_async(add_comment(problem_title))))
    
    content.append(put_row(buttons))
    
    popup(title=f"题目: {problem_title}", content=content)

async def delete_vote(problem_title, vote_data):
    if await check_and_notify_banned():
        return
    
    """删除指定的投票"""
    if not hasattr(local, 'current_user') or not local.current_user:
        toast("请先登录")
        return
    
    # 检查权限：管理员或投票所有者
    if local.current_user != vote_data['voter'] and not users[local.current_user]['is_admin']:
        toast("无权删除此投票")
        return
    
    with data_lock:
        if problem_title in votes:
            # 移除指定的投票
            votes[problem_title] = [v for v in votes[problem_title] 
                                   if not (v['voter'] == vote_data['voter'] and 
                                           v['thinking'] == vote_data['thinking'] and 
                                           v['implementing'] == vote_data['implementing'] and 
                                           v['quality'] == vote_data['quality'])]
    
    # 更新最后保存时间并立即保存
    global last_save_time
    last_save_time = time.time()
    save_votes()
    
    log_action(local.current_user, "删除投票", f"题目: {problem_title}, 投票者: {vote_data['voter']}")
    toast("投票已删除！")
    await show_problem_details(problem_title)

async def logout():
    """用户登出"""
    if hasattr(local, 'current_user'):
        log_action(local.current_user, "用户登出")
        del local.current_user
    
    # 清除cookie中的登录信息
    await set_cookie('username', '', max_age=-1)
    await set_cookie('password_hash', '', max_age=-1)
    
    toast("已登出")
    await refresh_page()

async def refresh_page():
    """刷新页面内容"""
    clear()
    await main()

async def sort_table(column):
    """按指定列排序表格"""
    # 如果点击的是当前排序列，则切换排序方向
    if local.sort_column == column:
        local.sort_ascending = not local.sort_ascending
    else:
        # 否则设置新的排序列，默认升序
        local.sort_column = column
        local.sort_ascending = True
    
    # 保存排序偏好到cookie
    await set_cookie('sort_column', local.sort_column, max_age=365*24*60*60)
    await set_cookie('sort_ascending', 'true' if local.sort_ascending else 'false', max_age=365*24*60*60)
    
    log_action(local.current_user if hasattr(local, 'current_user') else "anonymous", "排序表格", f"列: {column}, 升序: {local.sort_ascending}")
    await refresh_page()

def get_sort_indicator(column):
    """获取排序列的指示器"""
    if local.sort_column == column:
        return " ↑" if local.sort_ascending else " ↓"
    return ""

async def download_log_file():
    if await check_and_notify_banned():
        return
    
    """下载日志文件"""
    if not hasattr(local, 'current_user') or not local.current_user or not users[local.current_user]['is_admin']:
        toast("无权执行此操作")
        return
    
    try:
        with open('log.log', 'rb') as f:
            content = f.read()
        put_file('log.log', content, '下载日志文件')
        log_action(local.current_user, "下载日志文件")
    except FileNotFoundError:
        toast("日志文件不存在")
        log_action(local.current_user, "尝试下载日志文件但文件不存在")

async def check_cookie_login():
    """检查cookie中的登录信息"""
    username = await get_cookie('username')
    password_hash = await get_cookie('password_hash')
    # run_js("console.log(username, password_hash);", username=username, password_hash=password_hash)
    if username and password_hash and username in users:
        # 验证密码哈希
        if users[username]['password'] == password_hash:
            local.current_user = username
            
            # 从cookie加载排序偏好
            sort_column = await get_cookie('sort_column')
            sort_ascending = await get_cookie('sort_ascending')
            
            if sort_column:
                local.sort_column = sort_column
            else:
                local.sort_column = 'title'
                await set_cookie('sort_column', 'title', max_age=365*24*60*60)
            
            if sort_ascending:
                local.sort_ascending = sort_ascending == 'true'
            else:
                local.sort_ascending = True
                await set_cookie('sort_ascending', 'true', max_age=365*24*60*60)
            
            users[username]['last_login'] = time.time()
            save_users()
            log_action(username, "Cookie自动登录成功")
            toast(f"欢迎回来, {username}!")
            return True
    
    # 清除无效的cookie
    if username:
        await set_cookie('username', '', max_age=-1)
    if password_hash:
        await set_cookie('password_hash', '', max_age=-1)
    
    return False

async def main():
    if await check_and_notify_banned():
        return
    
    """主应用"""
    # 设置页面标题
    set_env(title="题目评分系统", output_max_width='95%')
    
    # 加载数据
    load_users()
    load_problems()
    load_votes()
    
    # 启动自动保存线程
    threading.Thread(target=auto_save, daemon=True).start()
    
    # 检查cookie中的登录信息
    if not hasattr(local, 'current_user') or not local.current_user:
        if not await check_cookie_login():
            await login()
    
    # 构建界面
    put_markdown("# 题目评分系统")
    
    # 用户信息栏
    user_info = f"当前用户: {local.current_user}"
    if users[local.current_user]['is_admin']:
        user_info += " (管理员)"
    
    user_row = [put_text(user_info), put_button("登出", onclick=lambda: run_async(logout()))]
    
    # 如果是管理员，添加下载日志按钮和命令执行按钮
    if users[local.current_user]['is_admin']:
        user_row.append(put_button("下载日志", onclick=lambda: run_async(download_log_file())))
        user_row.append(put_button("执行命令", onclick=lambda: run_async(execute_admin_command())))
    
    put_row(user_row)
    
    put_text("欢迎使用题目评分系统！您可以为以下题目的思维难度、实现难度和质量进行评分。")
    put_markdown("**注意**: 同一人多次对同一题目评分时，只保留最后一次评分。")
    put_markdown("## 题目顺序（上、下午/ABCDEF）以链接为准！")
    
    # 显示所有题目及其统计信息
    put_markdown("## 题目列表")
    
    # 创建排序按钮行
    sort_buttons = put_row([
        put_button(f"题目{get_sort_indicator('title')}", onclick=lambda: run_async(sort_table('title'))),
        put_button(f"知识点难度{get_sort_indicator('difficulty')}", onclick=lambda: run_async(sort_table('difficulty'))),
        put_button(f"投票数{get_sort_indicator('count')}", onclick=lambda: run_async(sort_table('count'))),
        put_button(f"思维难度{get_sort_indicator('thinking')}", onclick=lambda: run_async(sort_table('thinking'))),
        put_button(f"实现难度{get_sort_indicator('implementing')}", onclick=lambda: run_async(sort_table('implementing'))),
        put_button(f"综合评分{get_sort_indicator('overall')}", onclick=lambda: run_async(sort_table('overall'))),
        put_button(f"质量{get_sort_indicator('quality')}", onclick=lambda: run_async(sort_table('quality')))
    ])
    
    table_data = [['题目', '知识点难度', '标签', '投票数', '思维难度(平均±标准差)', '实现难度(平均±标准差)', '综合评分(平均±标准差)', '质量(平均±标准差)', '操作']]
    
    # 为每个题目计算统计信息
    problem_stats = []
    for problem in problems:
        stats = calculate_stats(problem['title'])
        meta = problem_metas.get(problem['title'], {})
        difficulty = meta.get('difficulty', '暂无评定')
        tags = meta.get('tags', '')
        
        problem_stats.append({
            'title': problem['title'],
            'link': problem['link'],
            'difficulty': difficulty,
            'tags': tags,
            'stats': stats
        })
    
    # 根据当前排序设置对题目进行排序
    if local.sort_column:
        def get_sort_key(item):
            if local.sort_column == 'title':
                return item['title']
            elif local.sort_column == 'difficulty':
                # 将难度级别映射为数字以便排序
                difficulty_order = {d: i for i, d in enumerate(DIFFICULTY_LEVELS.keys())}
                return difficulty_order.get(item['difficulty'], 99)
            elif local.sort_column == 'count':
                return item['stats']['count'] if item['stats'] else 0
            elif local.sort_column == 'thinking':
                return item['stats']['thinking']['mean'] if item['stats'] else 0
            elif local.sort_column == 'implementing':
                return item['stats']['implementing']['mean'] if item['stats'] else 0
            elif local.sort_column == 'overall':
                return item['stats']['overall']['mean'] if item['stats'] else 0
            elif local.sort_column == 'quality':
                return item['stats']['quality']['mean'] if item['stats'] else 0
            return 0
        
        problem_stats.sort(key=get_sort_key, reverse=not local.sort_ascending)
    else:
        # 默认按题目名称排序
        problem_stats.sort(key=lambda x: x['title'])
    
    # 构建表格数据
    for problem in problem_stats:
        stats = problem['stats']
        
        if stats:
            # 创建带颜色的平均分和标准差显示
            thinking_html = put_html(f'{format_rating_with_color(stats["thinking"]["mean"])}±{stats["thinking"]["std"]:.1f}')
            implementing_html = put_html(f'{format_rating_with_color(stats["implementing"]["mean"])}±{stats["implementing"]["std"]:.1f}')
            overall_html = put_html(f'{format_rating_with_color(stats["overall"]["mean"])}±{stats["overall"]["std"]:.1f}')
            quality_html = put_html(f'{format_quality_score(stats["quality"]["mean"])}±{stats["quality"]["std"]:.2f}')  # 修改这里
            count = stats['count']
        else:
            thinking_html = implementing_html = overall_html = quality_html = "暂无数据"  # 修改这里
            count = 0
            
        # 创建题目名称的超链接
        if problem['link']:
            problem_cell = put_link(problem['title'], url=problem['link'], new_window=True)
        else:
            problem_cell = problem['title']
            
        table_data.append([
            problem_cell,
            put_html(get_difficulty_html(problem['difficulty'])),
            problem['tags'],
            str(count),
            thinking_html,
            implementing_html,
            overall_html,
            quality_html,  # 修改这里
            put_row([
                put_button("查看", onclick=lambda p=problem['title']: run_async(show_problem_details(p))),
                put_button("评分", onclick=lambda p=problem['title']: run_async(vote_for_problem(p))) if hasattr(local, 'current_user') and local.current_user else put_text("请登录")
            ])
        ])
    
    # 显示排序按钮和表格
    put_row([sort_buttons])
    put_table(table_data)
    
    put_markdown("---")
    put_text(f"数据最后保存时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    put_text("系统每30秒自动保存数据")
    
    # 添加刷新按钮
    put_button("刷新页面", onclick=lambda: run_async(refresh_page()))

if __name__ == '__main__':
    # 启动服务器
    start_server(main, port=8999, debug=True, cdn=False)
