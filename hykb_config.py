# -*- coding=UTF-8 -*-
# @Project      QL_TimingScript
# @fileName     hykb_config.py
# @desc         好游快爆「翻滚吧爆米花」玉米庄园 —— 配置与协议常量
#               2026-09 依据线上 H5(v_v20260921) 与 APP(1.5.8.204) 逆向重写
"""
协议要点：

1. 页面令牌：每次打开 index.php，服务端随机下发
      var pageToken = "xxxxxx";        // 6 位
      var pageRandomStr = "xxxxxxxx";  // 8~10 位
      ACT.setACTServerTime(<毫秒时间戳>);   // 服务器时间，用于校准本地时钟
2. 签名：token_sign = md5(page_token|random_str|token_time|visitV2) 的前 10 位十六进制，
   其中 token_time = 本地毫秒时间戳 + (服务器时间 - 本地时间)
3. 每个请求都要经 AddTokenInfo 注入下列参数（缺任一即被判为非法请求）：
      <token_name>=<token_value>  token_version=v2  page_token  enter_time
      token_time  token_time2  serverTimeInfo[enter_set_time]  serverTimeInfo[time_diff]
      random_str  token_sign
      客户端模式下再补 scookie + device
4. 领奖类动作（Sign / *Ling）还需 smdeviceid（数美设备号）+ verison（客户端版本号）
5. 页面令牌有效期 23 小时（MAX_PAGE_STAY_MS），过期需重新打开页面取新令牌
6. 每日答题（mode=2）：题目与正确答案都不在客户端，答错消耗当日次数（2001=次数已用完），
   因此脚本只在 hykb_dati_bank.json 命中时作答，未命中只记录、绝不猜答案
7. 预约类任务（mode=9）：App 侧预约接口是加密私有接口（params_encryption=1 的 native AES 体
   + svid/SECRET-DEVICE 设备头 + native 签名 t），活动凭据直连必被 token error 拒绝；H5 活动域
   只有查询/领奖能力。脚本改走网页版正规接口：POST www.3839.com/app/hykb_web/ajax_yuyue.php
   action=orderNoPhone（无手机号预约），登录态用 Pauth/Uauth cookie（快爆 App 扫码登录一次，约 1 年），
   预约结果以 dailyInit.user_yuyue_gameids 复核（已实测生效；详见 WEB_YUYUE 与 WebYuyue）
8. 好友互动（mode=7）：需好友家存在待帮收玉米（iafInit.eventUids.uids），不足 3 个即跳过
9. 赛季等级奖励：批量接口 batchReceiveLevelPrize 已被服务端禁用（1001 不支持批量操作），
   改为 seasonManualInit 取 user_prize_data → 逐个 receiveLevelPrize(prize_id, level)，
   prize_id 以页面内嵌 onclick="Season.receiveLevelPrize(...)" 为准
"""

# ─────────────────────────── 域名与端点 ───────────────────────────

BASE_URL = "https://huodong3.3839.com"
SHOP_URL = "https://shop.3839.com"      # 爆米花商店（种子兜底购买；独立域，不走活动 token）
ACTIVITY_PATH = "/n/hykb/cornfarm"
PAGE_URL = f"{BASE_URL}{ACTIVITY_PATH}/index.php?imm=0"

API_ENDPOINTS = {
    "page": PAGE_URL,                                      # 活动首页（取 pageToken / 服务器时间 / 任务清单）
    "main": f"{BASE_URL}{ACTIVITY_PATH}/ajax.php",          # 登录 / 设备绑定 / 验证码
    "sign": f"{BASE_URL}{ACTIVITY_PATH}/ajax_sign.php",     # 签到（浇水）/ 补签道具
    "plant": f"{BASE_URL}{ACTIVITY_PATH}/ajax_plant.php",   # 播种 / 收获 / 好友偷菜
    "bag": f"{BASE_URL}{ACTIVITY_PATH}/ajax_bag.php",       # 背包 / 种子 / 肥料 / 装扮
    "daily": f"{BASE_URL}{ACTIVITY_PATH}/ajax_daily.php",   # 每日任务
    "ycx": f"{BASE_URL}{ACTIVITY_PATH}/ajax_ycx.php",       # 一次性任务
    "season": f"{BASE_URL}{ACTIVITY_PATH}/ajax_season.php",  # 赛季等级 / 阶段奖励
    "interactive": f"{BASE_URL}{ACTIVITY_PATH}/ajax_interactive.php",  # 好友互动
    "friendhome": f"{BASE_URL}{ACTIVITY_PATH}/ajax_friendhome.php",    # 好友主页
    "more": f"{BASE_URL}{ACTIVITY_PATH}/ajax_more.php",     # 成就 / 口令 / 额外分享
    "birthday": f"{BASE_URL}{ACTIVITY_PATH}/ajax_birthday.php",  # 生日礼
    # 爆米花商店（独立系统 shop.3839.com）：种子兜底购买用。判定字段是 code==200（非活动域的 key==ok），
    # 且不走活动域的 token 签名。checkOrder 校验+拿余额，createOrder 真扣爆米花下单（须带 order_flag=1）。
    "shop_order": f"{SHOP_URL}/index.php?c=OrderVirtual",
}

# 爆米花商店种子商品：普通玉米种子(corn_id=1) 对应 shop 商品 id=8220，约 5 爆米花/颗。
# 运行时优先从活动页 CornList 的 source_url 动态解析，这里只作解析失败时的兜底默认值。
SEED_SHOP_GOODS_ID = "8220"
SEED_SHOP_CORN_ID = 1

# APP 原生爆米花接口（仅用于余额/明细查询，mall 域名走 HTTP）
NATIVE_API = {
    "balance": "http://mall.3839.com/api/baomihua/appnum",
    "log": "http://mall.3839.com/api/baomihua/log",
    "app_balance": "http://mall.3839.com/api/baomihua/appBalance",
    "super_balance": "http://mall.3839.com/api/xbaomihua/appnum",
}

# ─────────────────────────── 请求头 ───────────────────────────
# UA 说明：App 侧 UAHelper.b() 的拼装格式为
#     Androidkb/<app版本>(android;<机型>;<安卓版本>;<宽>x<高>;<网络>)
# WebView 再追加 ";@4399_sykb_android_activity@"
# UA（环境变量 HYKB_UA）为「必设」变量：活动服务端会截取 UA 中 "Androidkb" 到
# "@4399_sykb_android_activity@" 之间的机型字段做模拟器白名单校验
# （Verify.ValEmulatorOrVirtualAPKWhiteList），UA 必须与账号真实机型一致。
# 不内置默认 UA —— 缺失时脚本直接拒绝运行（见 好游快爆.py 的 load_accounts），
# 避免用统一默认 UA 冒充多台设备而被判为非真机操作。

API_CONFIG = {
    "headers": {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": PAGE_URL,
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua": '"Not=A?Brand";v="99", "Android WebView";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?1",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        # 真机 WebView 会带上页面自身写的两个标记 cookie（非账号凭据）
        "Cookie": "cornfarm_iback_v5=ok; cornfarm_iback_mark_v2=ok",
        "Connection": "keep-alive",
    },
    "timeout": 20,
}

# 客户端版本号（Sign/*Ling 的 verison 参数，与 App 版本对齐）
CLIENT_VERSION = "1.5.8.204"
# VersionCode（dailyInit 及部分 *Ling 参数，App versionCode）
VERSION_CODE = "416"

# ─────────────────────────── 节流（防风控核心）───────────────────────────
THROTTLE = {
    "min_interval": 1.2,        # 相邻请求最小间隔（秒）
    "max_interval": 3.6,        # 相邻请求最大间隔（秒）
    "task_gap": (4.0, 11.0),    # 两个任务之间的停顿区间（秒）
    "group_gap": (12.0, 26.0),  # 任务分组（签到 / 庄园 / 每日 / 赛季）之间的停顿
    "max_retry": 2,             # 单请求最大重试次数
    "retry_backoff": 2.5,       # 重试退避基数（秒）
    "small_game_wait": 330,     # 小游戏任务需游玩 5 分钟，脚本等待秒数
    "download_wait": 95,        # 下载游玩任务（mode=3）领奖窗口约 60s，留余量
}

# ─────────────── 网页版预约）───────────────
# 为什么用网页版：预约动作在 App 侧是加密私有接口（params_encryption=1 的 native AES 体
# + svid/SECRET-DEVICE 设备头 + native 签名 t），活动凭据直连必被 token error 拒绝；
# H5 活动域也没有预约能力（153 个 ac 里只有查询/领奖）。
# 而 www.3839.com 游戏详情页的「立即预约 → 无手机号预约」是正规前端接口，纯 HTTP：
#
#   POST {site}/app/hykb_web/ajax_yuyue.php   (form-urlencoded)
#     action=checkStatus   → {"key":"ok","yuyued":false}      查询预约状态
#     action=checkLogin    → {"key":"ok","msg":"登录校验通过"} 强登录校验
#     action=orderNoPhone  → {"key":"ok","msg":"预约成功"}     无手机号预约（gid + game_type）
#
# 登录态：Pauth / Uauth / accesstoken / nickname 四个 cookie —— 用快爆 App 扫码登录
# www.3839.com 一次即可（QRcodeCreate → App 扫一扫确认 → QRcodeAuthCallBack 下发 cookie），
# 实测有效期约 1 年。放进环境变量 HYKB_WEB_COOKIE（多账号用 @ 分隔，与 cookie 顺序对应）；
# 本地调试也可把 cookie 字符串写进同目录 hykb_web_cookie.txt。
# 真伪判据：预约后玉米庄园 dailyInit 的 user_yuyue_gameids 会包含该 gameid（已实测）。
WEB_YUYUE = {
    "enabled": True,
    "site": "https://www.3839.com",
    "endpoint": "/app/hykb_web/ajax_yuyue.php",
    "game_type": "0",            # 页面 _focus_info_type（0=手游详情页）
    "relation_steam_id": "",     # 页面 _relation_steam_id，Steam 关联游戏才需要
    "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "env_names": ("HYKB_WEB_COOKIE", "Hykb_web_cookie", "hykb_web_cookie"),
    "cookie_file": "hykb_web_cookie.txt",
    "max_per_run": 8,            # 单次运行最多预约几个游戏（正常一天 0-2 个）
}

# ─────────────────────────── 任务开关 ───────────────────────────
# 只做「接口层能真实完成」的任务；需真人操作（下载安装、云游戏、逛帖子）的不碰
TASK_SWITCHES = {
    "sign": True,                # 签到 / 浇水（每日爆米花主要来源）
    "manor": True,               # 庄园：收获 / 播种
    "daily_share": True,         # mode=1  分享类（分享福利 / 分享资讯）
    "daily_dati": True,          # mode=2  每日答题（仅本地题库命中时作答，见 hykb_dati_bank.json）
    "daily_interactive": True,   # mode=7  好友互动（需好友家有待帮收玉米，没有则跳过）
    "daily_yuyue": True,         # mode=9  预约领奖（已预约的游戏自动领奖）
    "daily_yuyue_auto": True,    # mode=9  预约全自动：走网页版正规接口预约（见 WEB_YUYUE）
    "daily_small_game": True,    # mode=15/20 快爆小游戏：纯接口领奖（启动→等 5 分钟→领，实测每个 +3 爆米花）
    "daily_download": True,      # mode=3  下载游玩：纯接口领奖（下载埋点→记开始→等 60s→领，实测每个 +2~66 爆米花）
    "season": True,              # 赛季等级奖励（线上已禁用批量接口，改为逐个等级领取）
    "ycx": True,                 # 一次性任务（分享 / 答题类）
    "daily_max": 0,              # 每日任务单次运行处理上限，0=不限制
}

# ─────────────────────────── 任务类型（页面 data-mode → 业务类型）───────────────────────────
#   1=分享福利/分享资讯  2=每日答题  3=下载体验  4=种草好友  5=云游戏  7=好友互动
#   9=预约游戏  11=热玩推荐  15=快爆小游戏  17=爆友热议  20=小游戏(模板)
TASK_MODES = {
    1: "分享",
    2: "答题",
    3: "下载体验",
    4: "种草好友",
    5: "云游戏",
    7: "好友互动",
    9: "预约游戏",
    11: "热玩推荐",
    15: "快爆小游戏",
    17: "爆友热议",
    20: "快爆小游戏",
}

AUTO_MODES = (1, 2, 7, 9)

ERROR_CODES = {
    "SUCCESS": "ok",
    "NO_LOGIN": "103",
    # 104 = 顶号（H5 源码 login.js 注释「顶号」→ 弹 dialog_other_place_login）：
    # 账号在别处（通常是快爆 App）登录过，本脚本持有的 scookie 会话已被服务端作废。
    # 与 103 同属「凭据层失效」，但成因不同：103=没登录/凭据过期，104=被更新的登录顶掉。
    "OTHER_PLACE_LOGIN": "104",
    "ALREADY_DONE": "1001",
    "PLANTING_FAILURE": 501,
    "NO_SEEDS": "503",
    "TASK_DONE": "2001",
    "TASK_READY": "2002",
    "DONE_TODAY": "2004",       # 玉米成熟度 100% / 答题出题
    "NEED_HARVEST": "2005",
}

# 风控关键字
RISK_KWS = (
    "验证码", "异常", "频繁", "非法", "黑名单", "风控", "操作过快",
    "请稍后再试", "环境异常", "请使用手机参与", "重新登录",
)
# 幂等关键字：正常跳过，不是错误
SKIP_KWS = (
    "已领取", "已经领取", "已领过", "今日已", "今天已", "已经完成",
    "已完成", "已签到", "已经签到",
)

# 待条件关键字：非脚本故障，明日/稍后服务端自然恢复（预约领奖冷却等），不计失败
WAIT_KWS = (
    "暂时无法领取", "暂时无法", "冷却", "稍后再领", "尚未到", "未到时间",
)

# App 侧黑名单等级（页面 ACT.userlevel），>=2 表示已被风控处置。
# 等级说明来自 H5 源码注释（login.js / act.js BlackVal）：
#   2 = 轻微异常用户 / 3 = 疑似恶意用户 / 4 = 恶意用户
BLACKLIST_LEVELS = (2, 3, 4)
BLACKLIST_LEVEL_NAMES = {2: "轻微异常用户", 3: "疑似恶意用户", 4: "恶意用户"}

# 爆米花商店域（shop.3839.com）的黑名单错误码：commDetail.js case -90006 走的是
# 同一套黑名单弹窗（按 data.level 显示 dialog_blacklist_level{2,3,4}），
# 即「商店购买被拦」与活动域 key=blacklist 是同一个风控状态，不是余额/等级问题。
SHOP_BLACKLIST_CODE = -90006

# ─────────────────────────── 文案 ───────────────────────────
RESPONSE_MESSAGES = {
    "login_success": "【{}】登录成功",
    "sign_success": "💧浇水签到成功，爆米花 +{}",
    "sign_already": "⏭️今日已浇水签到",
    "harvest_success": "🌽收获成功，爆米花 +{}",
    "plant_success": "🌾播种成功",
    "daily_ready": "任务-{}-可以领奖了🎉",
    "daily_done": "⏭️任务-{}-已领过",
    "daily_failed": "❌任务-{}-失败：{}",
}
