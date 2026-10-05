# -*- coding=UTF-8 -*-
# @Project      QL_TimingScript
# @fileName     好游快爆.py
# @desc         好游快爆「翻滚吧爆米花」玉米庄园 H5 自动任务
# @author           Echo
# @EditTime         2026/9/24
# cron: 0 0 13 * * *
# const $ = new Env('好游快爆');

"""
============ 一、要配哪些环境变量============
多账号：每个变量都用 @ 分隔，且顺序与 HYKB_COOKIE 一一对应。

  【必配】
    HYKB_COOKIE       活动登录凭据（即请求里的 scookie）。缺它无法登录
    HYKB_SMDEVICEID   数美设备号。缺它领取/签到类接口会被判风控（blacklist），领不到奖
    HYKB_UA           抓包原样 User-Agent。服务端按 UA 里的机型做真机白名单校验；
                      不配脚本直接拒绝运行（不内置默认 UA，避免统一 UA 冒充多台设备被判非真机）
  【可选】
    HYKB_DEVICE       设备识别码。留空时自动取 scookie 的第 5 段，一般无需单独配
    HYKB_WEB_COOKIE   网页登录态，仅「预约任务(mode=9)」需要（获取方式见下方「四」）

============ 二、怎么抓这些值（除 WEB_COOKIE 外都来自同一次抓包）============
  准备：
    1) 电脑装抓包工具（Reqable / Charles / Fiddler 任一），开启 HTTPS 解密
    2) 手机连上该工具的代理，并安装信任它的 CA 证书
    3) 打开好游快爆 App → 进入「翻滚吧爆米花」活动（玉米庄园）
       → 手动点一次「浇水 / 签到」（这一步才会发出带 smdeviceid 的领取请求）
  抓取：在抓包列表里按域名过滤  huodong3.3839.com ，打开活动下任意一个 POST 请求
        （ajax_sign.php / ajax.php / ajax_daily.php 等）后——
    · 请求体 Form 里的 scookie      → 整段复制，填 HYKB_COOKIE
                                       形如 4|0|<uid>|<base64昵称>|<device>|…（竖线分隔）
    · 请求体 Form 里的 smdeviceid   → 复制填 HYKB_SMDEVICEID（仅领取类请求带，故须先点签到）
    · 请求体 Form 里的 device       → 只有要单独配 HYKB_DEVICE 时才取它（通常 = scookie 第 5 段）
    · 请求头 Headers 里的 User-Agent → 整行原样复制，填 HYKB_UA
                                       须含 Androidkb/<版本>(android;<机型>;…) 这一段
  提示：scookie / smdeviceid 是「账号 + 设备」绑定的长期值，同一台手机抓一次即可长期复用；
        失效的表现是脚本报 loginStatus=103 或 blacklist，届时重抓一次即可。

============ 三、HYKB_WEB_COOKIE（预约任务专用，不用抓包）============
  预约走网页版正规接口，需网页登录态（Pauth / Uauth / accesstoken / nickname 四个 cookie）。
  在能开浏览器的电脑上运行：  python hykb_web_login.tool.py
  浏览器打开 http://127.0.0.1:8899/ → 用快爆 App「扫一扫」→ 手机点「确认登录」，
  终端随即打印一整行可直接粘贴到 HYKB_WEB_COOKIE 的 cookie 串（有效期约 1 年）。
  不配不影响其它任务，仅预约任务会跳过。接口原理详见 hykb_config.WEB_YUYUE。
"""


import argparse
import hashlib
import json
import random
import re
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

import log
from get_env import get_env
from hykb_config import (
    API_CONFIG,
    API_ENDPOINTS,
    AUTO_MODES,
    BLACKLIST_LEVEL_NAMES,
    BLACKLIST_LEVELS,
    CLIENT_VERSION,
    ERROR_CODES,
    RESPONSE_MESSAGES,
    RISK_KWS,
    SEED_SHOP_CORN_ID,
    SEED_SHOP_GOODS_ID,
    SHOP_BLACKLIST_CODE,
    SKIP_KWS,
    TASK_MODES,
    TASK_SWITCHES,
    THROTTLE,
    VERSION_CODE,
    WAIT_KWS,
    WEB_YUYUE,
)
from sendNotify import send_notification_message_collection

warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# 页面令牌有效期（服务端 MAX_PAGE_STAY_MS = 23 小时），留 10 分钟余量
PAGE_TOKEN_TTL_MS = 23 * 3600 * 1000 - 10 * 60 * 1000

# 玉米成熟度已满 / 需要先收获 —— 领奖接口会返回这两个码，处理后可重试一次
MATURITY_CODES = ("2004", "2005")
# 领奖时「田地状态不对」的提示关键词（服务端 info）：需要先收获或先播种，
# 命中后就地修复庄园（收获+补种 / 播种）再重试领奖。注意与下载/小游戏的
# 「试玩时间未到」区分——后者 info 讲的是时间，不含这些农事词，不会误命中。
FARM_BLOCK_KWS = ("还没有播种", "没有播种", "未播种", "先去播种", "先播种", "未种下",
                  "去种植", "请收割", "先收割", "去收割", "请收获", "先收获",
                  "成熟度已", "成熟度达", "达到100")


class RiskStop(Exception):
    """命中风控信号 —— 立即停止该账号，绝不硬重试"""


def now_ms() -> int:
    return int(time.time() * 1000)


def rand_param() -> str:
    """模拟 WebView 里 Math.random() 的字符串形式：0.xxxxxxxxxxxxxxxxxx"""
    return f"0.{random.randint(100000000000000000, 899999999999999999)}"


def risk_hit(text: str) -> bool:
    return any(k in text for k in RISK_KWS)


def is_skip(text: str) -> bool:
    return any(k in text for k in SKIP_KWS)


def is_wait(text: str) -> bool:
    """待条件（非故障）：预约领奖冷却等，服务端稍后/明日自然恢复"""
    return any(k in text for k in WAIT_KWS)


def farm_blocked(text: str) -> bool:
    """领奖被「田地状态」卡住（需先收获/先播种）——就地修复庄园再重试"""
    return any(k in text for k in FARM_BLOCK_KWS)


def blacklist_level(msg: Any) -> Optional[int]:
    """解析服务端「黑名单」等级，识别不到返回 None。

    三个真实响应形态（均由 H5 源码取证，不是推测）：
      · 活动域：  {"key": "blacklist", "level": 2|3|4, "dialog": true}
                  （login.js / act.js BlackVal 按 level 弹 dialog_blacklist_level{2,3,4}）
      · 登录：    {"loginStatus": "100", "config": {"blacklist": {"level": 3, "dialog": true}}}
      · 商店域：  {"code": -90006, "data": {"level": 2|3|4, "dialog": true}}
                  （commDetail.js: case -90006 → 同一套黑名单弹窗）

    注意：只认 level ∈ BLACKLIST_LEVELS（2/3/4）。level=1 或普通业务错误一律返回 None，
    避免把「经验不足(1002)」「商品售罄(-80003)」这类正常失败误判成风控。
    """
    if not isinstance(msg, dict):
        return None
    candidates: List[Any] = []
    if str(msg.get("key")) == "blacklist":
        candidates.append(msg.get("level"))
    code = msg.get("code")
    if code is not None:
        try:
            is_shop_block = int(code) == SHOP_BLACKLIST_CODE
        except (TypeError, ValueError):
            is_shop_block = False
        if is_shop_block:
            data = msg.get("data")
            if isinstance(data, dict):
                candidates.append(data.get("level"))
            if not candidates:
                candidates.append(msg.get("level"))
    cfg = msg.get("config")
    if isinstance(cfg, dict) and isinstance(cfg.get("blacklist"), dict):
        candidates.append(cfg["blacklist"].get("level"))
    for item in candidates:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value in BLACKLIST_LEVELS:
            return value
    return None


def blacklist_stop_text(level: int, label: str) -> str:
    """黑名单中断提示：说清「是什么、为什么必须停、怎么恢复」"""
    name = BLACKLIST_LEVEL_NAMES.get(level, "未知等级")
    return (f"账号已被服务端风控标记（黑名单等级 {level}·{name}），{label} 被拒。"
            "该状态下每日任务 / 赛季 / 兑换 / 商店类接口都会返回 blacklist，"
            "重复请求只会让等级继续升级（3 疑似恶意 → 4 恶意），故已立即停止本账号全部动作。"
            "官方恢复条件（活动弹窗原文）：在快爆 App 内绑定微信并关注「好游快爆」微信公众号后再参与；"
            "恢复前不要连跑本脚本。")


# ───────────────────────── 每日答题题库 ─────────────────────────
# 题目与正确答案都不在客户端（H5 只回传用户点的选项文本），答错还会消耗当日次数，
# 所以脚本只在题库命中时才作答；未命中的题目写进这个文件等人工补答案。
DATI_BANK_FILE = Path(__file__).with_name("hykb_dati_bank.json")


def load_dati_bank() -> Dict[str, str]:
    """读取本地答题库（结构 {"answers": {"题目": "正确选项文本"}}）"""
    try:
        raw = json.loads(DATI_BANK_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.log(f"⚠️答题库 {DATI_BANK_FILE.name} 读取失败：{e}")
        return {}
    answers = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(answers, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in answers.items() if str(v).strip()}


def remember_dati_question(title: str, options: List[str]) -> None:
    """把没见过的题目与选项落盘，供人工补答案（answers 里填正确选项文本即可自动作答）"""
    if not title:
        return
    try:
        raw = json.loads(DATI_BANK_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("answers", {})
    unknown = raw.setdefault("unknown", {})
    if title in raw["answers"] or title in unknown:
        return
    unknown[title] = options
    try:
        DATI_BANK_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.log(f"⚠️答题库写入失败：{e}")


def load_web_cookie() -> str:
    """网页登录态：优先环境变量（青龙），其次脚本同目录的本地文件（本地调试）"""
    for name in WEB_YUYUE["env_names"]:
        try:
            vals = [v for v in get_env(name, "@") if v]
        except Exception:
            vals = []
        if vals:
            return vals[0].strip().strip("'\"")
    path = Path(__file__).with_name(WEB_YUYUE["cookie_file"])
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore").strip().strip("'\"")
    return ""


class WebYuyue:
    """网页版预约：走 www.3839.com 游戏详情页「立即预约 → 无手机号预约」的正规前端接口

    为什么不走 App：预约动作在 App 侧是 api.3839app.com/kuaibao/android/api.php?a=add&c=gameappointment，
    报文 params_encryption=1（libne.so 里的 native AES）+ svid/SECRET-DEVICE 设备头 + native 签名 t，
    活动凭据直连只会拿到 token error；H5 活动域也没有预约能力（153 个 ac 里只有查询/领奖）。
    而网页版有官方预约入口，纯 HTTP（cookie 登录态），不依赖任何设备 —— 这正是青龙要的形态。

    接口（POST，form-urlencoded）：
        POST {site}/app/hykb_web/ajax_yuyue.php
            action=checkStatus   → {"key":"ok","yuyued":false}     查询预约状态
            action=checkLogin    → {"key":"ok","msg":"登录校验通过"} 强登录校验
            action=orderNoPhone  → {"key":"ok","msg":"预约成功"}     无手机号预约（gid + game_type）
    登录态：Pauth / Uauth / accesstoken / nickname 四个 cookie，快爆 App 扫码登录一次有效期约 1 年
    （登录页 → 扫码 → App 确认 → QRcodeAuthCallBack 下发 cookie）。
    """

    def __init__(self, cookie: str = "", cfg: Optional[Dict[str, Any]] = None):
        self.cfg: Dict[str, Any] = dict(WEB_YUYUE)
        if cfg:
            self.cfg.update(cfg)
        self.cookie = (cookie or "").strip().strip("'\"").strip()
        self.reason = "" if self.cookie else "未配置网页登录态（HYKB_WEB_COOKIE）"
        self.client = requests.Session()
        self.client.verify = False
        self.client.headers.update({
            "User-Agent": self.cfg["ua"],
            "Referer": self.cfg["site"] + "/",
            "Origin": self.cfg["site"],
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        if self.cookie:
            self.client.headers["Cookie"] = self.cookie

    @property
    def available(self) -> bool:
        return bool(self.cookie)

    def _post(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """带节流+重试的 POST（沿用 THROTTLE，保持人类节奏）"""
        url = self.cfg["site"] + self.cfg["endpoint"]
        for attempt in range(THROTTLE["max_retry"] + 1):
            time.sleep(random.uniform(THROTTLE["min_interval"], THROTTLE["max_interval"]))
            try:
                resp = self.client.post(url, data=data, timeout=API_CONFIG["timeout"])
                payload = resp.json()
            except Exception as e:
                if attempt >= THROTTLE["max_retry"]:
                    log.log(f"   ⚠️网页预约请求失败：{e}")
                    return None
                time.sleep(2 + attempt * 2)
                continue
            if isinstance(payload, dict):
                return payload
            return None
        return None

    def login_ok(self) -> bool:
        """强登录校验：预约弹窗的前置条件，失败即登录态过期"""
        data = self._post({"action": "checkLogin", "gid": "", "game_type": self.cfg["game_type"]})
        if data and data.get("key") == "ok":
            return True
        self.reason = (data or {}).get("msg") or "网页登录态失效"
        return False

    def is_reserved(self, gameid: str) -> Optional[bool]:
        """查询该游戏是否已预约；None 表示查询失败"""
        data = self._post({"action": "checkStatus", "gid": str(gameid), "game_type": self.cfg["game_type"]})
        if not data or data.get("key") != "ok":
            return None
        return bool(data.get("yuyued"))

    def reserve(self, gameid: str) -> bool:
        """无手机号预约（幂等：已预约再调也只返回成功）；真伪仍由玉米庄园 dailyInit 复核"""
        data = self._post({
            "action": "orderNoPhone",
            "gid": str(gameid),
            "game_type": self.cfg["game_type"],
            "relation_steam_id": self.cfg["relation_steam_id"],
        })
        if not data:
            return False
        if data.get("key") == "ok":
            log.log(f"   ✅网页预约成功：gameid={gameid}（{data.get('msg') or '预约成功'}）")
            return True
        log.log(f"   ❌网页预约失败：gameid={gameid} → {data.get('msg') or data}")
        return False


class HaoYouKuaiBao:
    """好游快爆玉米庄园任务执行器（单账号）"""

    def __init__(self, scookie: str, smdeviceid: str = "", device: str = "",
                 ua: str = "", probe: bool = False, web_cookie: str = ""):
        self.scookie = (scookie or "").strip().strip("'\"").strip()
        self.smdeviceid = (smdeviceid or "").strip().strip("'\"").strip()
        # cookie 约定：第 5 段即设备识别码（App 的 getUniqueDeviceIdNew 结果）
        parts = self.scookie.split("|")
        self.device = (device or "").strip() or (parts[4] if len(parts) > 4 else "")
        self.probe = probe

        headers = dict(API_CONFIG["headers"])
        headers["User-Agent"] = (ua or "").strip()   # UA 为必设项，已在 load_accounts 校验非空
        self.client = requests.Session()
        self.client.headers.update(headers)
        self.client.verify = False

        # 页面令牌与时钟校准
        self.page_token: str = ""
        self.random_str: str = ""
        self.enter_time: int = 0
        self.time_diff: int = 0
        self.server_time_info: Dict[str, int] = {}
        self.page_html: str = ""

        # 服务端下发的会话令牌
        self.token_name: str = "token"
        self.token_value: str = "default"
        self.user_name: str = ""
        self.user_level: int = 0

        self.stats: Dict[str, Any] = {"ok": 0, "skip": 0, "wait": 0, "fail": 0, "baomihua": 0}

        # 本轮是否已确认「无种子来源」（背包空 + 经验兑换失败 + 商店被拦/售罄）。
        # 同一个运行周期内种子库存不会自己恢复，标记后再遇到空地就直接跳过，
        # 避免每个任务都重跑一遍庄园闭环（旧日志里 6 个任务 × 3 次重试 = 上百次无效请求，
        # 这类高频重复正是把风控等级从 2 刷到 3 的原因）。
        self.seed_exhausted: bool = False

        # 每日任务上下文：dailyInit 下发的「已预约游戏」清单 / 本地答题库
        self.reserved_gameids: set = set()
        self.dati_bank: Dict[str, str] = load_dati_bank()

        # 网页版预约客户端（预约任务用；登录态缺失时自动跳过，不影响其它任务）
        self.yuyue = WebYuyue(web_cookie or load_web_cookie())

    # ───────────────────────── 基础层 ─────────────────────────

    def _pace(self) -> None:
        """请求节奏：真机不会 0.1 秒一个请求"""
        time.sleep(random.uniform(THROTTLE["min_interval"], THROTTLE["max_interval"]))

    def _server_now(self) -> int:
        return now_ms() + self.time_diff

    def _bootstrap(self) -> bool:
        """打开活动页：取 pageToken / pageRandomStr / 服务器时间（每次运行必须做）"""
        try:
            resp = self.client.get(API_ENDPOINTS["page"], timeout=API_CONFIG["timeout"])
        except Exception as e:
            log.log(f"❌打开活动页失败：{e}")
            return False
        if resp.status_code != 200:
            log.log(f"❌活动页返回 HTTP {resp.status_code}")
            return False

        html = resp.text
        self.page_html = html
        m_token = re.search(r"var\s+pageToken\s*=\s*['\"]([^'\"]+)['\"]", html)
        m_rand = re.search(r"var\s+pageRandomStr\s*=\s*['\"]([^'\"]+)['\"]", html)
        m_time = re.search(r"setACTServerTime\(\s*(\d{10,16})\s*\)", html)
        if not (m_token and m_rand and m_time):
            log.log("❌活动页未取到 pageToken/pageRandomStr/服务器时间（页面结构可能又变了）")
            return False

        self.page_token = m_token.group(1)
        self.random_str = m_rand.group(1)
        local = now_ms()
        self.enter_time = int(m_time.group(1))
        self.time_diff = self.enter_time - local
        self.server_time_info = {"enter_set_time": local, "time_diff": self.time_diff}
        log.log(f"🕒时间校准完成（服务器与本地相差 {self.time_diff} ms）")
        return True

    def _token_expired(self) -> bool:
        return self.enter_time > 0 and (self._server_now() - self.enter_time) >= PAGE_TOKEN_TTL_MS

    def _sign(self, param: Dict[str, Any]) -> Dict[str, Any]:
        """AddTokenInfo：每个请求必须注入的令牌与签名（对应页面里的 AddTokenInfo 函数）"""
        param[self.token_name] = self.token_value
        param["token_version"] = "v2"
        param["page_token"] = self.page_token
        param["enter_time"] = self.enter_time
        param["token_time"] = self._server_now()
        param["token_time2"] = now_ms()
        param["serverTimeInfo[enter_set_time]"] = self.server_time_info.get("enter_set_time", now_ms())
        param["serverTimeInfo[time_diff]"] = self.server_time_info.get("time_diff", 0)
        param["random_str"] = self.random_str
        sign_src = f"{self.page_token}|{self.random_str}|{param['token_time']}|visitV2"
        param["token_sign"] = hashlib.md5(sign_src.encode("utf-8")).hexdigest()[:10]
        param["scookie"] = self.scookie
        param["device"] = self.device
        return param

    def _check_risk(self, msg: Dict[str, Any], label: str) -> None:
        text = str(msg)
        if risk_hit(text):
            raise RiskStop(f"{label} 命中风控信号：{text[:160]}")
        # 服务端黑名单（活动域 key=blacklist / 登录 config.blacklist）——命中即熔断，
        # 绝不能再往下走：黑名单下所有领奖/兑换/商店都会失败，继续请求只会升级等级。
        level = blacklist_level(msg)
        if level is not None:
            raise RiskStop(blacklist_stop_text(level, label))

    def post(self, endpoint: str, param: Dict[str, Any], label: str = "") -> Optional[Dict[str, Any]]:
        """统一发请求：节流 + 令牌注入 + 重试 + 风控识别"""
        if self._token_expired():
            log.log("♻️页面令牌已过期（超过 23 小时），重新打开活动页换取新令牌")
            if not self._bootstrap():
                return None

        url = API_ENDPOINTS[endpoint]
        body = self._sign(dict(param))
        for attempt in range(THROTTLE["max_retry"] + 1):
            self._pace()
            try:
                resp = self.client.post(url, data=body, timeout=API_CONFIG["timeout"])
                msg = resp.json()
            except Exception as e:
                if attempt >= THROTTLE["max_retry"]:
                    log.log(f"❌{label}请求异常：{e}")
                    self.stats["fail"] += 1
                    return None
                time.sleep(THROTTLE["retry_backoff"] * (attempt + 1) + random.uniform(0, 1.5))
                body = self._sign(dict(param))   # 重试必须重新签名（token_time 要新鲜）
                continue

            if not isinstance(msg, dict):
                log.log(f"❌{label}返回非 JSON：{str(msg)[:160]}")
                self.stats["fail"] += 1
                return None

            self._check_risk(msg, label)
            if str(msg.get("loginStatus")) == ERROR_CODES["NO_LOGIN"]:
                raise RiskStop("凭据失效（loginStatus=103 / no_login），请重新抓包更新 HYKB_COOKIE")
            if str(msg.get("loginStatus")) == ERROR_CODES["OTHER_PLACE_LOGIN"]:
                raise RiskStop(
                    "会话被顶号（loginStatus=104 / otherPlaceLogin）：该账号在别处登录过"
                    "（最常见是在快爆 App 里进了本活动），服务端已作废脚本持有的会话。"
                    "这不是脚本 bug、也不是风控等级问题——请重新抓包更新 HYKB_COOKIE / "
                    "HYKB_SMDEVICEID / HYKB_UA（抓法见脚本头部「二、怎么抓这些值」）。"
                    "另：只要之后再用 App 打开本活动，就会再次顶号，需要再抓一次。")
            return msg
        return None

    # ───────────────────────── 业务层 ─────────────────────────

    def login(self) -> Optional[Dict[str, Any]]:
        msg = self.post("main", {"ac": "login", "r": rand_param()}, "登录")
        if not msg:
            return None
        cfg = msg.get("config") or {}
        if str(msg.get("loginStatus")) != "100" or not cfg:
            log.log(f"❌登录失败：{str(msg)[:200]}")
            return None
        self.token_name = cfg.get("token_name") or self.token_name
        self.token_value = cfg.get("token_value") or self.token_value
        self.user_name = cfg.get("name") or "未知用户"
        try:
            self.user_level = int(cfg.get("userlevel") or msg.get("userlevel") or 0)
        except (TypeError, ValueError):
            self.user_level = 0
        log.log(RESPONSE_MESSAGES["login_success"].format(self.user_name))
        return cfg

    def sign(self) -> None:
        """签到（浇水）—— 每日爆米花主要来源"""
        msg = self.post("sign", {
            "ac": "Sign",
            "smdeviceid": self.smdeviceid,
            "verison": CLIENT_VERSION,
            "OpenAutoSign": "close",
            "r": rand_param(),
        }, "浇水签到")
        if not msg:
            return
        if str(msg.get("key")) == ERROR_CODES["SUCCESS"]:
            add = msg.get("add_baomihua") or msg.get("baomihua") or "?"
            log.log(RESPONSE_MESSAGES["sign_success"].format(add))
            self._count_baomihua(msg)
            self.stats["ok"] += 1
        elif is_skip(str(msg)):
            log.log(RESPONSE_MESSAGES["sign_already"])
            self.stats["skip"] += 1
        else:
            log.log(f"⚠️浇水签到未成功：{str(msg)[:180]}")
            self.stats["fail"] += 1

    def manor(self, cfg: Optional[Dict[str, Any]] = None) -> bool:
        """庄园闭环：成熟→立即收获，空地→立即播种，无种子→用赛季经验兑换种子再播种。
        cfg 传入登录 config（run 首次调用）；cfg=None 时重新登录取实时状态
        （供领奖流程中途「成熟度已满」补救调用，确保收获后田里立刻补上成长中的玉米）。
        返回是否拿到了实时状态——拿不到（登录失败）时调用方不必再重试领奖。"""
        st = cfg if cfg is not None else self.login()
        if not st:
            return False
        self._farm_loop(st)
        return True

    def _farm_loop(self, st: Dict[str, Any]) -> None:
        grew = str(st.get("grew", ""))
        maturity = str(st.get("csd_jdt", ""))
        # 1) 成熟度已满 → 立即收获（HarvestAndPlant 会顺带补种，默认种子缺货时只收不种）
        if maturity == "100%" or grew == "100":
            msg = self.post("plant", {"ac": "HarvestAndPlant", "r": rand_param()}, "收获并播种")
            if not msg:
                return
            key = str(msg.get("key"))
            if key == ERROR_CODES["SUCCESS"]:
                self._count_baomihua(msg)
                self.stats["ok"] += 1
                if str(msg.get("grew")) == "-1":
                    # 只收割成功、未自动补种（默认选中的种子缺货）→ 立即手动播种把田种上
                    log.log("🌽收获成功，但默认种子缺货未自动补种，改手动播种")
                    self._plant(st)
                else:
                    log.log(f"🌽收获并播种成功（{self._reward_text(msg)}）")
                return
            if key in ("502", "503"):
                # 田里已无成长中/成熟的作物（可能刚被收走）→ 按空地处理去播种
                grew = "-1"
            else:
                log.log(f"⚠️收获未成功：{str(msg)[:150]}")
                return
        # 2) 空地 → 立即播种
        if grew in ("-1", "0", ""):
            self._plant(st)
        else:
            log.log("🌽庄园作物成长中，今日无需收获")

    def _plant(self, st: Dict[str, Any]) -> None:
        """播种：库存种子 → 赛季经验兑换（免费）→ 花爆米花买（兜底）→ 页面默认种子。
        取种子顺序按「先不花钱、再花钱」：背包 > 经验兑换 > 爆米花购买。
        本轮已确认无种子来源时直接跳过（避免同一轮内重复空跑，见 seed_exhausted）。"""
        if self.seed_exhausted:
            log.log("⏭️本轮已确认无种子来源（背包/兑换/商店都拿不到），跳过重复播种尝试")
            return
        bag = self.post("bag", {"ac": "BagInit", "r": rand_param()}, "背包")
        corn_id = self._pick_seed(bag)
        if corn_id is None:
            # 背包无库存种子 → 先用赛季经验兑换（站内闭环，不动用爆米花）
            corn_id = self._exchange_seed()
        if corn_id is None:
            # 经验也不够 → 花爆米花去商店买（用户认可的兜底，值得花）
            corn_id = self._buy_seed_shop()
        if corn_id is None:
            # 最后兜底：页面默认选中的种子 id（可能仍无库存，Plant 会自行报错）
            try:
                corn_id = int(st.get("next_seed_id") or 0) or None
            except (TypeError, ValueError):
                corn_id = None
        if corn_id is None:
            log.log("⏭️无库存种子，经验兑换与爆米花购买均失败，暂不播种（下次运行自动重试）")
            self.seed_exhausted = True
            self.stats["skip"] += 1
            return
        msg = self.post("plant", {"ac": "Plant", "corn_id": corn_id, "r": rand_param()},
                        f"播种(种子{corn_id})")
        if msg and str(msg.get("key")) == ERROR_CODES["SUCCESS"]:
            log.log(f"🌱播种成功（种子 corn_id={corn_id}）")
            self.stats["ok"] += 1
        elif msg:
            log.log(f"⚠️播种未成功：{str(msg)[:150]}")
            # 502/503 = 该种子无库存（服务端 info「种子不足」，线上实测就是 502）→ 库存 0 的
            # 事实本轮不会自己变，标记后停止重复尝试；其它错误（网络/未知码）不标记，留给下次运行。
            if str(msg.get("key")) in ("502", "503", str(ERROR_CODES["NO_SEEDS"])) \
                    or "种子不足" in str(msg):
                self.seed_exhausted = True

    def _exchange_seed(self) -> Optional[int]:
        """无库存种子时，用赛季经验兑换种子（纯接口、不花爆米花）。
        赛季兑换目录：prize_id=7 奇异种子(corn_id=3)、prize_id=8 糯玉米种子(corn_id=2)，
        各需 100 经验、每期限兑 20 次。经验不足则返回 None（交给爆米花购买兜底）。
        返回成功兑换到的 corn_id，供随后 Plant 使用。"""
        for prize_id, corn_id, name in ((7, 3, "奇异种子"), (8, 2, "糯玉米种子")):
            msg = self.post("season", {"ac": "userExchangePrize", "prize_id": prize_id,
                                        "r": rand_param()}, f"经验兑换{name}")
            if not msg:
                continue
            if str(msg.get("key")) == ERROR_CODES["SUCCESS"]:
                log.log(f"🎁已用赛季经验兑换 {name}（未花爆米花）")
                return corn_id
            log.log(f"ℹ️兑换{name}未成功（多为赛季经验不足）：{str(msg)[:100]}")
        return None

    def _shop_goods_id(self) -> str:
        """从活动页 CornList 动态解析普通玉米种子的商店商品 id（source_url 里的 id=XXXX），
        解析失败退回配置默认 SEED_SHOP_GOODS_ID。"""
        m = re.search(r'shop\.3839\.com[^"\']*?[?&]id=(\d+)', self.page_html or "")
        return m.group(1) if m else SEED_SHOP_GOODS_ID

    def _shop_post(self, action: str, goods_id: str) -> Optional[Dict[str, Any]]:
        """向爆米花商店（shop.3839.com）发请求：独立域、不走活动 token 签名，
        参数沿用真机抓包字段（id/smdeviceid/version/client/scookie/device），判定字段是 code。"""
        url = f"{API_ENDPOINTS['shop_order']}&a={action}"
        payload = {
            "id": goods_id,
            "smdeviceid": self.smdeviceid,
            "version": CLIENT_VERSION,
            "r": rand_param(),
            "client": "1",
            "scookie": self.scookie,
            "device": self.device,
            "order_flag": "1",     # createOrder 需要（页面 CommDetail.orderFlag=1）
        }
        self._pace()
        try:
            resp = self.client.post(url, data=payload, timeout=API_CONFIG["timeout"])
            data = resp.json()
        except Exception as e:
            log.log(f"❌商店{action}请求异常：{e}")
            return None
        # 黑名单判定必须在 try 之外：商店的 -90006 与活动域 key=blacklist 是同一个风控状态，
        # 命中即熔断整个账号，绝不能当成「余额不足」放过去继续下单。
        level = blacklist_level(data)
        if level is not None:
            raise RiskStop(blacklist_stop_text(level, f"商店{action}"))
        return data

    def _buy_seed_shop(self) -> Optional[int]:
        """花爆米花去商店买种子（兜底，用户认可值得花）。
        链路：checkOrder 校验+查余额 → createOrder 真下单扣爆米花。商店判定字段是 code==200。
        余额不足 / 未绑定微信 / 抢光等一律安全跳过，不硬重试。成功返回普通玉米 corn_id=1。"""
        goods_id = self._shop_goods_id()
        chk = self._shop_post("checkOrder", goods_id)
        if not chk:
            return None
        if int(chk.get("code") or 0) != 200:
            log.log(f"ℹ️商店购买种子受阻（checkOrder code={chk.get('code')}）："
                    f"{str(chk.get('msg') or chk)[:80]}")
            return None
        bmh = (chk.get("user") or {}).get("bmh")
        log.log(f"🛒尝试用爆米花购买种子（商品 {goods_id}，当前爆米花余额 {bmh}）")
        order = self._shop_post("createOrder", goods_id)
        if not order:
            return None
        if int(order.get("code") or 0) == 200:
            bal = (order.get("user") or {}).get("bmh")
            tail = f"，爆米花余额 {bal}" if bal is not None else ""
            log.log(f"✅已用爆米花购买种子成功{tail}")
            return SEED_SHOP_CORN_ID
        log.log(f"⚠️爆米花购买种子未成功（code={order.get('code')}）：{str(order.get('msg') or order)[:100]}")
        return None

    @staticmethod
    def _pick_seed(bag: Optional[Dict[str, Any]]) -> Optional[int]:
        """从背包 cornDetails 里挑一个仍有库存（seed>0）的种子 id。
        真实结构是 dict：{"1":{"corn_id":1,"seed":"5","corn":"0"}, ...}，
        种子数量字段名为 seed（旧代码按 list + num/count 取，恒取不到 → 误判无种子）。"""
        if not isinstance(bag, dict):
            return None
        details = bag.get("cornDetails")
        items = details.values() if isinstance(details, dict) else (details or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("seed") or item.get("num") or item.get("count") or 0) > 0:
                    return int(item.get("corn_id") or item.get("id"))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _reward_text(msg: Dict[str, Any]) -> str:
        """领奖响应里的奖励文案：签到用 add_baomihua，每日任务用 reward_bmh_num"""
        parts: List[str] = []
        gain = 0
        for key in ("reward_bmh_num", "add_baomihua"):
            try:
                gain = int(msg.get(key) or 0)
            except (TypeError, ValueError):
                gain = 0
            if gain:
                break
        if gain:
            parts.append(f"爆米花 +{gain}")
        for key, name in (("reward_csd_num", "成熟度"), ("add_csd", "成熟度"),
                          ("reward_score_num", "经验"), ("add_score", "经验")):
            try:
                value = int(msg.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                parts.append(f"{name} +{value}")
        for key in ("baomihua", "dialog_baomihua"):
            if msg.get(key) is not None:
                try:
                    bal = int(msg[key])
                except (TypeError, ValueError):
                    bal = 0
                if bal > 0:                     # 分享类不发爆米花，服务端回 baomihua=0，别渲染成「余额 0」
                    parts.append(f"余额 {bal}")
                break
        return "，".join(parts) or "已到账"

    def _count_baomihua(self, msg: Dict[str, Any]) -> None:
        for key in ("reward_bmh_num", "add_baomihua"):
            try:
                gain = int(msg.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if gain:
                self.stats["baomihua"] += gain
                return

    def season(self) -> None:
        """赛季：先取等级奖励状态，再逐个领取（线上服务端已不支持批量领取：
        调用 batchReceiveLevelPrize 返回 1001「不支持批量操作」）"""
        init = self.post("season", {"ac": "seasonManualInit", "r": rand_param()}, "赛季初始化")
        if not init:
            return
        prize_map = init.get("user_prize_data") or {}
        # prize_id 以页面内嵌的 onclick="Season.receiveLevelPrize(prize_id, level)" 为准
        page_prize_ids = {int(level): int(prize) for prize, level in
                          re.findall(r"receiveLevelPrize\(\s*(\d+)\s*,\s*(\d+)\s*\)", self.page_html)}
        claimable = sorted(int(lv) for lv, st in prize_map.items()
                           if str(st).lower() in ("completed", "1"))
        if not claimable:
            log.log(f"⏭️赛季暂无可领奖励（经验 {init.get('user_score', '?')}，"
                    f"下一档 Lv{init.get('user_next_span_level', '?')}，状态={prize_map or '空'}）")
            self.stats["skip"] += 1
            return
        for level in claimable:
            got = self.post("season", {"ac": "receiveLevelPrize",
                                       "prize_id": page_prize_ids.get(level, level),
                                       "level": level, "r": rand_param()}, f"赛季Lv{level}奖励")
            if got and str(got.get("key")) == ERROR_CODES["SUCCESS"]:
                log.log(f"🎉赛季Lv{level}奖励已领取（{self._reward_text(got)}）")
                self._count_baomihua(got)
                self.stats["ok"] += 1
            elif got and is_skip(str(got)):
                log.log(f"⏭️赛季Lv{level}已领取过")
                self.stats["skip"] += 1
            elif got and is_wait(str(got)):
                log.log(f"⚠️赛季Lv{level}待条件（稍后放行）：{str(got)[:120]}")
                self.stats["wait"] += 1
            else:
                log.log(f"⚠️赛季Lv{level}未领取：{str(got)[:140]}")
                self.stats["fail"] += 1

    # ───────────────────────── 每日任务 ─────────────────────────

    @staticmethod
    def parse_page_tasks(html: str) -> List[Dict[str, Any]]:
        """解析 index.php 里服务端直出的任务清单（data-id / data-mode / data-status）"""
        tasks: List[Dict[str, Any]] = []
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li.daily_li"):
            tid = li.get("data-id")
            if not tid:
                continue
            try:
                mode = int(li.get("data-mode") or 0)
                status = int(li.get("data-status") or -1)
            except ValueError:
                mode, status = 0, -1
            title_el = li.select_one("dt")
            tasks.append({
                "id": int(tid),
                "mode": mode,
                "status": status,
                "gameid": li.get("data-gameid") or "0",
                "xyxtype": str(li.get("data-xyxtype") or ""),   # 小游戏类型：2=应用宝/微信(可接口领)，3=中控(需真机)
                "title": (title_el.get_text(" ", strip=True) if title_el else "")[:60],
            })
        return tasks

    def _claim(self, action: str, task_id: int, label: str) -> bool:
        """统一走领奖接口（*Ling）；遇「田地状态」阻塞（成熟度满需收获 / 未播种）先就地修复庄园再领"""
        param: Dict[str, Any] = {
            "ac": action,
            "id": task_id,
            "smdeviceid": self.smdeviceid,
            "verison": CLIENT_VERSION,
            "r": rand_param(),
        }
        msg = self.post("daily", param, label)
        if not msg:
            return False
        key = str(msg.get("key"))
        # 成熟度满(2004/2005) 或 info 提示需先收获/先播种 → 就地跑一次庄园闭环（取实时状态）再重试一次
        if key in MATURITY_CODES or farm_blocked(str(msg)):
            log.log(f"🌽{label}：田地未就绪（{str(msg.get('info') or key)[:40]}），先跑庄园闭环再领")
            self.manor()          # 无参=重新登录取实时状态，收获/播种/兑种子一步到位
            msg = self.post("daily", dict(param, r=rand_param()), label + "-重试")
            if not msg:
                return False
            key = str(msg.get("key"))
        if key == ERROR_CODES["SUCCESS"]:
            log.log(f"✅{label}领取成功（{self._reward_text(msg)}）")
            self._count_baomihua(msg)
            self.stats["ok"] += 1
            return True
        if is_skip(str(msg)):
            log.log(f"⏭️{label}今日已领取")
            self.stats["skip"] += 1
            return True
        if is_wait(str(msg)):
            log.log(f"⚠️{label}待条件（预约领奖有冷却，服务端稍后放行）：{str(msg)[:110]}")
            self.stats["wait"] += 1
            return False
        log.log(f"⚠️{label}未领取：{str(msg)[:150]}")
        self.stats["fail"] += 1
        return False

    def task_share(self, task: Dict[str, Any]) -> None:
        """分享类任务：发起分享 → 分享回调 → 领奖（mode/source 取真机抓包值）"""
        label = f"分享任务[{task['id']}]"
        tid = task["id"]
        self.post("daily", {"ac": "DailyShare", "id": tid, "onlyc": "0", "r": rand_param()}, label + "-发起")
        self.post("daily", {"ac": "DailyShareCallback", "id": tid, "mode": "qq", "source": "ds",
                            "r": rand_param()}, label + "-回调")
        self._claim("DailyShareLing", tid, label)

    def task_dati(self, task: Dict[str, Any]) -> None:
        """每日答题：题目与正确答案都不在客户端（H5 只把用户点的选项文本回传），
        且答错会消耗当日答题次数 → 只在本地题库命中时才作答，
        未命中的题目记录下来交人工在 App 内作答（绝不猜答案）。"""
        label = f"每日答题[{task['id']}]"
        tid = task["id"]
        q = self.post("daily", {"ac": "DailyDati", "id": tid, "r": rand_param()}, label + "-取题")
        if not q:
            return
        options = [str(q.get(f"option{i}")) for i in range(1, 5) if q.get(f"option{i}")]
        if str(q.get("key")) != "2004" or not options:
            if is_skip(str(q)) or str(q.get("key")) == "2001":
                log.log(f"⏭️{label}今日已答/已无答题次数：{str(q)[:110]}")
                self.stats["skip"] += 1
            else:
                log.log(f"⚠️{label}取题失败：{str(q)[:130]}")
                self.stats["fail"] += 1
            return

        title = str(q.get("title") or "").strip()
        answer = self.dati_bank.get(title)
        if answer and answer in options:
            ans = self.post("daily", {"ac": "DailyDatiAnswer", "id": tid, "option": answer,
                                      "r": rand_param()}, label + "-作答")
            if ans and str(ans.get("key")) == ERROR_CODES["SUCCESS"]:
                log.log(f"✅{label}作答正确（题库命中）")
                self._claim("DailyDatiLing", tid, label)
                return
            log.log(f"⚠️{label}作答未通过：{str(ans)[:120]}")
            self.stats["fail"] += 1
            return

        remember_dati_question(title, options)
        log.log(f"❓{label}未知题目（题库未命中），不猜答案、不消耗次数：{title}"
                f"｜选项：{' / '.join(options)}")
        log.log(f"📝已写入 {DATI_BANK_FILE.name} 的 answers，填好正确选项后下次自动作答")
        self.stats["skip"] += 1

    def task_interactive(self, task: Dict[str, Any]) -> None:
        """好友互动：先帮 3 位好友处理事件（事件=好友家待帮收的玉米），再领奖。
        事件由真实好友产生，没有事件时服务端必拒 —— 直接跳过，不做无效请求。"""
        label = f"好友互动[{task['id']}]"
        init = self.post("interactive", {"ac": "iafInit", "r": rand_param()}, label + "-好友事件")
        uids: List[str] = []
        if init:
            ev = init.get("eventUids") or {}
            if isinstance(ev, dict):
                uids = [str(u) for u in (ev.get("uids") or []) if str(u).isdigit()]
        if len(uids) < 3:
            log.log(f"⏭️{label}好友事件不足（{len(uids)}/3），今日跳过"
                    f"（该任务需真实好友家出现待帮收玉米）")
            self.stats["skip"] += 1
            return
        for fuid in uids[:3]:
            self.post("friendhome", {"ac": "HelpReap", "fuid": fuid, "r": rand_param()},
                      label + f"-帮好友{fuid}")
            time.sleep(random.uniform(*THROTTLE["task_gap"]))
        self._claim("DailyInteractiveLing", task["id"], label)

    def yuyue_auto_reserve(self, tasks: List[Dict[str, Any]]) -> None:
        """预约任务全自动：走网页版正规接口（无手机号预约），再用 dailyInit 复核（服务端状态为权威）

        链路：dailyInit 给出「已预约游戏」清单 → 找出每日任务里未预约的预约类任务（mode=9）
        → POST ajax_yuyue.php?action=orderNoPhone → 重新拉 dailyInit 确认生效 → 之后按 task_yuyue 领奖。
        全程纯 HTTP，不依赖任何设备；登录态过期时只提示、不重试轰炸（避免风控）。
        """
        if not TASK_SWITCHES.get("daily_yuyue_auto", True):
            return
        pending = [t for t in tasks
                   if t["mode"] == 9
                   and str(t.get("gameid") or "0") not in ("", "0")
                   and str(t["gameid"]) not in self.reserved_gameids]
        if not pending:
            return

        pend_txt = "、".join(f"{t.get('title') or t['gameid']}({t['gameid']})" for t in pending)
        yuyue: Optional[WebYuyue] = getattr(self, "yuyue", None)
        if yuyue is None or not yuyue.available:
            log.log(f"⚠️预约任务跳过：{getattr(yuyue, 'reason', '未初始化')}"
                    f"；待预约 {len(pending)} 个：{pend_txt}"
                    "（需在环境变量 HYKB_WEB_COOKIE 配置网页登录态，获取方式见 hykb_config.WEB_YUYUE）")
            return
        if not yuyue.login_ok():
            log.log(f"⚠️预约任务跳过：网页登录态已失效（{yuyue.reason}）"
                    f"；待预约 {len(pending)} 个：{pend_txt}"
                    " —— 本机运行 python hykb_web_login.tool.py 用快爆 App 扫码重新登录，"
                    "把输出的 cookie 串填回 HYKB_WEB_COOKIE")
            return

        pending = pending[: int(yuyue.cfg["max_per_run"])]
        log.log(f"🌐预约全自动（网页版接口）：{len(pending)} 个游戏待预约")
        for task in pending:
            gid = str(task["gameid"])
            log.log(f"   ➡️预约《{task['title'] or gid}》gameid={gid}")
            state = yuyue.is_reserved(gid)
            if state is True:
                log.log("   ℹ️网页侧显示已预约，跳过下单")
            else:
                yuyue.reserve(gid)

        # 复核：服务端 user_yuyue_gameids 才是权威判据
        init = self.post("daily", {"ac": "dailyInit", "VersionCode": VERSION_CODE, "r": rand_param()},
                         "预约结果复核")
        if not init:
            return
        new_set = {str(g) for g in (init.get("user_yuyue_gameids") or []) if str(g)}
        added = sorted(new_set - self.reserved_gameids)
        if added:
            log.log(f"✅预约生效 {len(added)} 个：{', '.join(added)}")
        self.reserved_gameids = new_set
        still = [str(t["gameid"]) for t in pending if str(t["gameid"]) not in new_set]
        if still:
            log.log(f"⚠️仍未预约成功：{', '.join(still)}"
                    "（可能该游戏已停止预约 / 网页登录态权限不足，稍后重试即可）")

    def task_yuyue(self, task: Dict[str, Any]) -> None:
        """预约类任务：预约动作已由上一步的网页版接口完成（WebYuyue），
        这里只对「服务端确认已预约」的游戏领奖，避免无效失败请求。"""
        label = f"预约任务[{task['id']}]"
        gameid = str(task.get("gameid") or "")
        if gameid and gameid in self.reserved_gameids:
            self._claim("DailyYuyueLing", task["id"], label)
        else:
            log.log(f"⏭️{label}未预约（gameid={gameid or '?'}）：请在 App 内预约该游戏后才能领奖")
            self.stats["skip"] += 1

    def smallgame_launch(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """快爆小游戏（mode=15/20）· 启动阶段（流水线）：一次性启动所有可接口领取的小游戏，
        记录启动时刻并立即返回——不在此等待。300 秒试玩计时的等待窗口交给后续
        签到/庄园/赛季/预约/分享等任务自然吸收，末尾再由 smallgame_claim 统一领奖。

        实测服务端只校验「启动→领奖」的时间差（约 300 秒），不校验真机内真实游玩。
        每个 id 在服务端各自独立计时，所以先全部启动最省时。
        xyxtype=3（中控小游戏）领奖恒回 2002「请先体验游戏」= 需真机，接口领不了 → 排除。"""
        games = [t for t in tasks if t["mode"] in (15, 20)]
        if not games:
            return []
        doable = [t for t in games if t.get("xyxtype") != "3"]
        skipped = [t for t in games if t.get("xyxtype") == "3"]
        if skipped:
            log.log(f"⏭️{len(skipped)} 个小游戏为中控类型（xyxtype=3，需真机内真实游玩），跳过："
                    + "、".join(str(t["id"]) for t in skipped))
        if not doable:
            return []

        log.log(f"🎮快爆小游戏：先启动 {len(doable)} 个（计时期间穿插其它任务，末尾统一领奖）")
        started: List[Dict[str, Any]] = []
        for task in doable:
            r = self.post("daily", {"ac": "DailySmallGame", "id": task["id"], "r": rand_param()},
                          f"小游戏启动[{task['id']}]")
            if r and str(r.get("key")) in ("ok", "2001"):
                started.append(task)
            time.sleep(random.uniform(*THROTTLE["task_gap"]))
        self._sg_launch_ts = time.time()
        return started

    def smallgame_claim(self, started: List[Dict[str, Any]]) -> None:
        """快爆小游戏 · 领取阶段（流水线）：确保距启动已过 small_game_wait 秒
        （其它任务多半已覆盖该等待，不足才补等），再逐个领奖。"""
        if not started:
            return
        elapsed = time.time() - getattr(self, "_sg_launch_ts", 0.0)
        remain = THROTTLE["small_game_wait"] - elapsed
        if remain > 0:
            log.log(f"⏳小游戏试玩计时还差 {int(remain)}s（其它任务已覆盖 {int(elapsed)}s），补等后领奖")
            time.sleep(remain)
        else:
            log.log(f"🎮小游戏计时已满（其它任务已覆盖 {int(elapsed)}s 等待），开始领取 {len(started)} 个奖励")
        for task in started:
            self._claim_smallgame(task)
            time.sleep(random.uniform(*THROTTLE["task_gap"]))

    def download_launch(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """下载游玩任务（mode=3）· 启动阶段（流水线）：逐个 DailyGameDown（下载埋点）
        + DailyGamePlay（记开始游玩），记录启动时刻立即返回——领奖窗口约 60s，
        由后续常规任务的耗时吸收，末尾再由 download_claim 统一领奖。

        实测服务端只校验「开始游玩→领奖」的时间差（约 60 秒），不校验真机是否真装了游戏。
        7/7 下载任务均可接口领取（+2~66 爆米花不等），无需真机例外。"""
        games = [t for t in tasks if t["mode"] == 3]
        if not games:
            return []
        log.log(f"📥下载游玩：先启动 {len(games)} 个（记开始游玩，末尾统一领奖）")
        started: List[Dict[str, Any]] = []
        for task in games:
            self.post("daily", {"ac": "DailyGameDown", "rwid": task["id"], "r": rand_param()},
                      f"下载埋点[{task['id']}]")
            r = self.post("daily", {"ac": "DailyGamePlay", "id": task["id"], "r": rand_param()},
                          f"开始游玩[{task['id']}]")
            if r and str(r.get("key")) in ("ok", "2001"):
                started.append(task)
            time.sleep(random.uniform(*THROTTLE["task_gap"]))
        self._dl_launch_ts = time.time()
        return started

    def download_claim(self, started: List[Dict[str, Any]]) -> None:
        """下载游玩 · 领取阶段（流水线）：确保距开始游玩已过 download_wait 秒
        （常规任务多半已覆盖），再逐个领奖。"""
        if not started:
            return
        elapsed = time.time() - getattr(self, "_dl_launch_ts", 0.0)
        remain = THROTTLE["download_wait"] - elapsed
        if remain > 0:
            log.log(f"⏳下载任务试玩计时还差 {int(remain)}s（其它任务已覆盖 {int(elapsed)}s），补等后领奖")
            time.sleep(remain)
        else:
            log.log(f"📥下载任务计时已满（其它任务已覆盖 {int(elapsed)}s），开始领取 {len(started)} 个奖励")
        for task in started:
            self._claim_delayed("DailyDownGameLing", task, "下载任务")
            time.sleep(random.uniform(*THROTTLE["task_gap"]))

    def _claim_delayed(self, action: str, task: Dict[str, Any], kind: str) -> None:
        """领取「延时类」任务奖励（小游戏 / 下载游玩通用）：
        遇 2005 成熟度满 / info 提示需先收获或先播种 → 就地跑庄园闭环再重试（最多 3 次）。
        2002=请先体验/试玩、2003=时间未到 → 记 skip（多为需真机或计时未足，非脚本故障）。"""
        label = f"{kind}[{task['id']}]"
        for _ in range(3):
            msg = self.post("daily", {"ac": action, "id": task["id"], "VersionCode": VERSION_CODE,
                                      "smdeviceid": self.smdeviceid, "verison": CLIENT_VERSION,
                                      "r": rand_param()}, label)
            if not msg:
                return
            key = str(msg.get("key"))
            # 田地未就绪优先判（成熟度满 / info 提示需先收获或先播种）——farm 关键词与
            # 「试玩时间未到」互斥，故 2003 讲时间时不会误命中，只有真的田地问题才跑庄园。
            if key in MATURITY_CODES or farm_blocked(str(msg)):
                log.log(f"🌽{label}：田地未就绪（{str(msg.get('info') or key)[:40]}），先跑庄园闭环再领")
                fixed = self.manor()   # 无参=取实时状态收获/播种/兑种子，修复后重试领奖
                if self.seed_exhausted or not fixed:
                    # 庄园闭环没修好田地（本轮拿不到种子 / 取不到实时状态）→ 再重试结果一样，
                    # 直接判未领取；否则会像旧日志那样每个任务空转 3 轮，把请求量堆成风控特征。
                    reason = "本轮已无种子来源" if self.seed_exhausted else "庄园状态获取失败"
                    log.log(f"⚠️{label}田地未能就绪：{reason}（见上方庄园日志），本次放弃")
                    self.stats["fail"] += 1
                    return
                continue
            # 2002=请先体验(需真机)、2003=试玩时间未到(计时未足)——非田地问题，跳过
            if key in ("2002", "2003"):
                log.log(f"⏭️{label}需真机内真实游玩或计时未足（服务端回 {key}），跳过")
                self.stats["skip"] += 1
                return
            if key == ERROR_CODES["SUCCESS"]:
                log.log(f"✅{label}领取成功（{self._reward_text(msg)}）")
                self._count_baomihua(msg)
                self.stats["ok"] += 1
            elif key == "2001" or is_skip(str(msg)):
                log.log(f"⏭️{label}今日已领取")
                self.stats["skip"] += 1
            elif is_wait(str(msg)):
                log.log(f"⚠️{label}待条件：{str(msg)[:110]}")
                self.stats["wait"] += 1
            else:
                log.log(f"⚠️{label}未领取：{str(msg)[:130]}")
                self.stats["fail"] += 1
            return
        # 3 次都卡在「田地未就绪」——庄园闭环没能修好（如种子和赛季经验都耗尽），记失败别静默吞掉
        log.log(f"⚠️{label}多次尝试后田地仍未就绪，本次放弃（下次运行自动重试）")
        self.stats["fail"] += 1

    def _claim_smallgame(self, task: Dict[str, Any]) -> None:
        """领取单个小游戏奖励（复用 _claim_delayed）"""
        self._claim_delayed("DailySmallGameLing", task, "小游戏")

    def daily(self, sg_started: Optional[List[Dict[str, Any]]] = None,
              dl_started: Optional[List[Dict[str, Any]]] = None) -> None:
        """每日任务主流程。sg_started/dl_started：run() 已在最前启动的小游戏/下载任务列表——
        跑完常规任务后在此末尾统一领取，让计时被常规任务的耗时吸收。"""
        init = self.post("daily", {"ac": "dailyInit", "VersionCode": VERSION_CODE, "r": rand_param()},
                         "每日任务初始化")
        done_by_server: set = set()
        if init:
            # 已预约游戏清单：预约动作只能由 App 端发起，这里决定哪些预约任务可直接领奖
            self.reserved_gameids = {str(g) for g in (init.get("user_yuyue_gameids") or []) if str(g)}
            # 任务的真实状态来自 dailyInit（success=2 已领取）；页面 data-status 恒为 -1，不可用
            done_by_server = {str(t.get("rwid")) for t in (init.get("user_daily_Task") or [])
                              if str(t.get("success")) == "2"}
        tasks = self.parse_page_tasks(self.page_html)
        if not tasks:
            log.log("⚠️页面未解析到每日任务条目，跳过每日任务")
            self.download_claim(dl_started or [])    # 即便解析失败，已启动的下载/小游戏仍要领奖
            self.smallgame_claim(sg_started or [])
            return

        # 预约任务全自动：走网页版正规接口（无手机号预约）→ 纯 HTTP，无设备依赖
        self.yuyue_auto_reserve(tasks)

        todo: List[Dict[str, Any]] = []
        for task in tasks:
            if task["mode"] not in AUTO_MODES or not self._mode_enabled(task["mode"]):
                continue
            if task["status"] == 100 or str(task["id"]) in done_by_server:   # 已领取（以服务端状态为准）
                continue
            todo.append(task)

        limit = TASK_SWITCHES.get("daily_max") or 0
        if limit and len(todo) > limit:
            todo = todo[:limit]

        if todo:
            log.log(f"📋本次处理 {len(todo)} 个任务："
                    + "、".join(f"{TASK_MODES.get(t['mode'], t['mode'])}#{t['id']}" for t in todo))
            for index, task in enumerate(todo, 1):
                mode = task["mode"]
                try:
                    if mode == 1:
                        self.task_share(task)
                    elif mode == 2:
                        self.task_dati(task)
                    elif mode == 7:
                        self.task_interactive(task)
                    elif mode == 9:
                        self.task_yuyue(task)
                except RiskStop:
                    raise
                except Exception as e:
                    log.log(f"❌任务[{task['id']}]执行异常：{e}")
                    self.stats["fail"] += 1
                if index < len(todo):
                    time.sleep(random.uniform(*THROTTLE["task_gap"]))
        else:
            log.log("⏭️没有可自动完成的每日任务（其余任务需在真机上手动完成）")

        # 流水线收尾：领取最前启动的下载任务(约60s计时) + 小游戏(约300s计时)——
        # 两者计时都已被中间常规任务的耗时吸收大部分
        self.download_claim(dl_started or [])
        self.smallgame_claim(sg_started or [])

    @staticmethod
    def _mode_enabled(mode: int) -> bool:
        return {
            1: TASK_SWITCHES.get("daily_share", True),
            2: TASK_SWITCHES.get("daily_dati", True),
            7: TASK_SWITCHES.get("daily_interactive", True),
            9: TASK_SWITCHES.get("daily_yuyue", True),
        }.get(mode, False)

    def ycx(self) -> None:
        """一次性任务：老账号基本已领完，这里只做初始化让服务端刷新任务状态"""
        msg = self.post("ycx", {"ac": "ycxInit", "VersionCode": VERSION_CODE, "r": rand_param()},
                        "一次性任务初始化")
        if msg and str(msg.get("key")) == ERROR_CODES["SUCCESS"]:
            log.log("ℹ️一次性任务状态已刷新")
        elif msg:
            log.log(f"ℹ️一次性任务无可领项：{str(msg)[:120]}")

    # ───────────────────────── 编排 ─────────────────────────

    def run(self) -> None:
        log.log("=" * 12 + " 好游快爆 · 翻滚吧爆米花 " + "=" * 12)
        if not self.scookie:
            log.log("❌未配置 cookie，跳过")
            return
        if not self.smdeviceid:
            log.log("⚠️未配置 HYKB_SMDEVICEID：签到/领奖类接口可能被服务端拒绝")
        if not self._bootstrap():
            return

        try:
            cfg = self.login()
            if not cfg:
                return

            if self.probe:
                self._probe_report(cfg)
                return

            # 流水线起点：先启动快爆小游戏（mode=15/20，300s 计时）与下载游玩（mode=3，约60s 计时），
            # 让试玩计时被后续签到/庄园/赛季/预约/分享等任务的耗时吸收，末尾在 daily() 里统一领奖。
            page_tasks = self.parse_page_tasks(self.page_html)
            sg_started: List[Dict[str, Any]] = []
            dl_started: List[Dict[str, Any]] = []
            if TASK_SWITCHES.get("daily_small_game", False):
                sg_started = self.smallgame_launch(page_tasks)
                if sg_started:
                    time.sleep(random.uniform(*THROTTLE["group_gap"]))
            if TASK_SWITCHES.get("daily_download", False):
                dl_started = self.download_launch(page_tasks)
                if dl_started:
                    time.sleep(random.uniform(*THROTTLE["group_gap"]))

            if TASK_SWITCHES.get("sign", True):
                self.sign()
                time.sleep(random.uniform(*THROTTLE["group_gap"]))

            if TASK_SWITCHES.get("manor", True):
                self.manor(cfg)
                time.sleep(random.uniform(*THROTTLE["group_gap"]))

            if TASK_SWITCHES.get("season", True):
                self.season()
                time.sleep(random.uniform(*THROTTLE["group_gap"]))

            self.daily(sg_started, dl_started)

            if TASK_SWITCHES.get("ycx", True):
                time.sleep(random.uniform(*THROTTLE["group_gap"]))
                self.ycx()

            s = self.stats
            summary = (f"📊【{self.user_name}】完成：成功 {s['ok']} / 已领过 {s['skip']}"
                       f" / 待条件 {s['wait']} / 失败 {s['fail']}"
                       + (f" / 爆米花 +{s['baomihua']}" if s["baomihua"] else ""))
            log.log(summary)
            if s["wait"]:
                log.log("ℹ️「待条件」多为预约领奖冷却（同一天预约后需隔一段时间才放行），"
                        "非脚本故障，下次运行会自动补领")

        except RiskStop as e:
            log.log(f"🛑【{self.user_name or '当前账号'}】{e}")
            log.log("🛑已按防风控策略停止本账号后续动作（不要连续重试，隔天再跑更安全）")
        finally:
            self.client.close()

    def _probe_report(self, cfg: Dict[str, Any]) -> None:
        """--probe：只读体检，不发任何任务请求"""
        tasks = self.parse_page_tasks(self.page_html)
        log.log(f"🔎体检模式：{self.user_name}（uid={cfg.get('uid', '?')}）")
        for key in ("baomihua", "seed", "muck", "corn", "csd_jdt", "grew", "deviceid",
                    "next_seed_id", "blacklist", "DeviceBindUserStatus"):
            if key in cfg:
                log.log(f"    {key} = {cfg[key]}")
        log.log(f"    cookie 设备号 = {self.device[:6]}…（cookie 第 5 段）")
        log.log(f"    数美设备号 = {'已配置' if self.smdeviceid else '未配置'}")
        log.log(f"    页面任务 {len(tasks)} 条：")
        for task in tasks:
            flag = "可自动" if task["mode"] in AUTO_MODES else "需真机"
            log.log(f"      [{flag}] id={task['id']} mode={task['mode']}"
                    f"({TASK_MODES.get(task['mode'], '?')}) status={task['status']} {task['title']}")


def load_accounts() -> List[Dict[str, str]]:
    """读取环境变量；cookie 与数美设备号 / UA 按顺序一一对应。
    HYKB_UA 为必设项：缺失或数量少于账号数的账号会被拒绝（不再回退默认 UA），
    避免用统一默认 UA 冒充多台设备而被服务端判为非真机操作。"""
    cookies = [c for c in (get_env("HYKB_COOKIE", "@") or get_env("Hykb_cookie", "@")) if c]
    if not cookies:
        log.log("❌未找到 Hykb_cookie / HYKB_COOKIE 变量")
        return []
    smids = [s for s in get_env("HYKB_SMDEVICEID", "@") if s]
    web_cookies = [w for w in get_env("HYKB_WEB_COOKIE", "@") if w]
    devices = [d for d in get_env("HYKB_DEVICE", "@") if d]
    # 注意：HYKB_UA 的值本身含 "@4399_sykb_android_activity@"，绝不能沿用 "@" 做多账号分隔符
    # （会把单个 UA 从中间截断、丢掉服务端机型校验标记）——HYKB_UA 多账号一律用换行分隔
    # （get_env 会对每段 strip 掉首尾空白，顺带去掉行尾回车符）。
    uas = [u for u in get_env("HYKB_UA", "\n") if u]
    if not uas:
        log.log("❌未配置 HYKB_UA：该变量为必设项（服务端按 UA 机型做真机白名单校验），"
                "请抓包取真机原样 UA 后配置；脚本终止。")
        return []

    def pick(seq: List[str], index: int) -> str:
        if not seq:
            return ""
        return seq[index] if index < len(seq) else seq[0]

    accounts: List[Dict[str, str]] = []
    for i, cookie in enumerate(cookies):
        # UA 严格按序号一一对应，不走 pick 的“回退到第一个”——否则多账号会共用同一 UA，
        # 等于用统一 UA 冒充多台设备，正是要避免的非真机特征。
        ua = uas[i].strip() if i < len(uas) else ""
        if not ua:
            log.log(f"❌第 {i + 1} 个账号未配置对应的 HYKB_UA（多账号须与 HYKB_COOKIE 用 @ 同序对应），"
                    "跳过该账号——UA 为必设项，不回退默认值、也不复用其它账号的 UA。")
            continue
        accounts.append({
            "cookie": cookie,
            "smdeviceid": pick(smids, i),
            "device": pick(devices, i),
            "ua": ua,
            "web_cookie": pick(web_cookies, i),
        })
    return accounts


def main() -> None:
    parser = argparse.ArgumentParser(description="好游快爆 翻滚吧爆米花 自动任务")
    parser.add_argument("--probe", action="store_true", help="只登录并打印任务清单，不执行任务")
    args = parser.parse_args()

    accounts = load_accounts()
    if not accounts:
        return
    log.log(f"🔐共读取到 {len(accounts)} 个账号")
    for index, account in enumerate(accounts):
        if index:
            time.sleep(random.uniform(20, 45))   # 账号之间也留出人类节奏
        bot = HaoYouKuaiBao(
            scookie=account["cookie"],
            smdeviceid=account["smdeviceid"],
            device=account["device"],
            ua=account["ua"],
            probe=args.probe,
            web_cookie=account["web_cookie"],
        )
        bot.run()


if __name__ == '__main__':
    main()
    send_notification_message_collection("好游快爆活动奖励领取通知 - {}".format(datetime.now().strftime("%Y/%m/%d")))
