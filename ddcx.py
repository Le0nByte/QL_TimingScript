# -*- coding=UTF-8 -*-
# @Project          QL_TimingScript
# @fileName         ddcx.py
# @author           Echo
# @EditTime         2026/10/27
# const $ = new Env('滴滴出行')
# cron: "0 8,14,17 * * *"
"""
滴滴出行 · 福利中心（福利金 / 券 / 任务）自动化

变量格式：DD_TOKENS=token1&token2
  token  ：App 打开「我的 → 福利中心」，抓任意一条 https://ut.xiaojukeji.com/ut/welfare/... 请求，
           复制请求体里的 token 字段（约 218 字符，以 = 结尾）。
  可选备注：主号#<token>&小号#<token>，备注只用于日志区分，不参与请求。

可选开关（默认值即推荐值）：
  dd_city=17              城市 id（默认 17=成都，影响券批次）
  dd_lat / dd_lng         经纬度（默认成都，需与城市一致，否则券批次对不上）
  dd_dchn=O01eJ8v         福利中心页面渠道号（一般不用改）
  dd_sign=1               每日签到
  dd_task=1               福利中心任务：接任务 + 领任务奖励 + 领阶梯奖励
  dd_task_enroll=1        是否接任务（接任务只是占位，需在 App 内真实完成才算数；
                          只想领已完成任务的奖励时设为 0）
  dd_task_complex=0       是否也接「需真实完单」的复杂任务（默认只接浏览类简单任务）
  dd_max_task=20          单次最多接几个任务（避免请求过密）
  dd_divide=1             瓜分福利金：报名未来场次 + 领今日奖励
  dd_legacy=1             会员权益：月月领券 / 行程意外险 / 周三折上折
  dd_janitor=1            天天神券：签到 + 抽奖
  dd_skyfall=0            天降券弹窗直领（默认关，渠道号需按页面调整）
  dd_daily_pick=1         每日精选 / 限时抢
  dd_record=1             打印福利金今日获得明细（只读）
  dd_proxy=               代理地址，如 http://127.0.0.1:7897（默认直连，不读系统代理）
  dd_ua=                  自定义 User-Agent

两个入口：
  python ddcx.py              全量（签到 + 任务 + 瓜分 + 会员权益 + 神券 + 每日精选）
  python ddcx.py --check      只读自检（只查询状态，不签到、不接任务、不领奖、不报名）

能做什么：
  福利中心：每日签到、气泡领奖、接任务、领任务奖励、领阶梯奖励、报名瓜分、领瓜分奖励；
  会员权益：等级礼券（周周/月月）、行程意外险、周三折上折；
  天天神券：签到、抽奖（可选天降券）；
  每日精选 / 限时抢：自动领券。

做不到什么：
  浏览类任务（打开 QQ 音乐、浏览酒店列表等）必须在 App 内真实浏览才算完成，
  脚本只能「接任务」，完不成也领不到奖励；日志会如实标注，不伪造完成。
  直通类权益（华住会/长隆/复游会）需要第三方绑定跳转，不做。
  支付优惠类券不在本脚本范围内。
"""
import asyncio
import datetime
import json
import os
import random
import secrets
import string
import sys

import httpx

import log
from get_env import get_env
from sendNotify import send_notification_message_collection

try:  # 面板/终端编码兜底，避免 GBK 控制台炸中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ────────────────────────────────────────────────────────────
# 环境变量
# ────────────────────────────────────────────────────────────
dd_tokens = get_env("DD_TOKENS", "&")
_CHECK_MODE = any(a in ("--check", "--probe", "-c") for a in sys.argv[1:])


def _flag(name, default="1"):
    """读布尔开关；--check 模式下所有写操作开关强制关闭。"""
    if _CHECK_MODE:
        return False
    return str(os.environ.get(name) or default).strip().lower() in ("1", "true", "yes", "on")


def _int_env(name, default):
    try:
        return int(str(os.environ.get(name) or default).strip() or default)
    except Exception:
        return default


do_sign = _flag("dd_sign")
do_task = _flag("dd_task")
do_task_enroll = _flag("dd_task_enroll")
do_task_complex = _flag("dd_task_complex", "0")
do_divide = _flag("dd_divide")
do_legacy = _flag("dd_legacy")
do_janitor = _flag("dd_janitor")
do_skyfall = _flag("dd_skyfall", "0")
do_daily_pick = _flag("dd_daily_pick")
do_record = str(os.environ.get("dd_record") or "1").strip().lower() in ("1", "true", "yes", "on")
max_task = max(1, _int_env("dd_max_task", 20))

city_id = _int_env("dd_city", 17)
lat = str(os.environ.get("dd_lat") or "30.707130422009786").strip()
lng = str(os.environ.get("dd_lng") or "104.09652654810503").strip()
dchn = str(os.environ.get("dd_dchn") or "O01eJ8v").strip()
proxy = str(os.environ.get("dd_proxy") or "").strip()

# ────────────────────────────────────────────────────────────
# 常量：换域名/换版本只改这里
# ────────────────────────────────────────────────────────────
_HOST_UT = "https://ut.xiaojukeji.com"
_HOST_MEMBER = "https://member.xiaojukeji.com"
_HOST_COMMON = "https://common.diditaxi.com.cn"
_HOST_REWARDS = "https://rewards.xiaojukeji.com"
_HOST_API = "https://api.didi.cn"

_UA = str(os.environ.get("dd_ua") or (
    "Mozilla/5.0 (Linux; Android 15; V2364A Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/110.0.5481.154 Safari/537.36 "
    "didi.passenger/8.0.16 FusionKit/2.0.0  OffMode/0"
)).strip()
_APP_VERSION = "8.0.16"
_JANITOR_DCHN = "AE6kvgY"      # 天天神券页面渠道号
_APPID = "30004"

# 福利中心接口（路径相对 https://ut.xiaojukeji.com）
_P = {
    "bubble": "/ut/welfare/api/home/getBubble",
    "divide_data": "/ut/welfare/api/home/divideData",
    "divide_history": "/ut/welfare/api/home/divideHistory",
    "task_list": "/ut/welfare/api/home/getTaskList/v3",
    "subscribe_query": "/ut/welfare/api/action/subscribe/query",
    "sign": "/ut/welfare/api/action/dailySign",
    "receive_task": "/ut/welfare/api/action/receiveTask/v2",
    "task_reward": "/ut/welfare/api/action/taskReward",
    "task_progress_reward": "/ut/welfare/api/action/taskProgressReward",
    "coupon_bind": "/ut/welfare/api/action/coupon/bind",
    "join_divide": "/ut/welfare/api/action/joinDivide",
    "divide_reward": "/ut/welfare/api/action/divideReward",
    "click_bubble": "/ut/welfare/api/action/clickBubble",
    "janitor_sign": "/ut/janitor/api/action/sign/do",
    "janitor_bind": "/ut/janitor/api/action/coupon/bind",
    "janitor_skyfall": "/ut/janitor/api/action/coupon/skyfall",
    "janitor_lottery": "/ut/janitor/api/action/lottery/doLottery",
}
# 会员权益接口（路径相对 https://member.xiaojukeji.com）
_M = {
    "privileges": "/pios/mlMember/h5/privilegeLists",       # 权益列表（新路径）
    "level_gift": "/dmember/h5/receiveLevelGift",           # 等级礼券（周周/月月）
    "bind_privilege": "/dmember/h5/bindPrivilege",          # 行程意外险
    "member_day": "/dmember/h5/receiveMemberDayDiscount",   # 周三折上折
    "receive_privilege": "/pios/mlMember/h5/receivePrivilege",  # 通用领权益
}

# 任务状态
_TASK_STATUS_ZH = {
    "not_enroll": "未接任务", "ing": "进行中", "complete": "待领奖",
    "finish": "已领奖", "end_unfinish": "已结束未完成", "under_review": "审核中",
}
# 瓜分状态
_DIVIDE_STATUS_ZH = {1: "未参与", 2: "进行中", 3: "待瓜分"}
# 券状态：0 待领取 / 1 已使用 / 2 未使用 / 3 冻结 / 5 过期 / 6 未解锁
_COUPON_STATUS_ZH = {0: "待领取", 1: "已使用", 2: "未使用", 3: "已冻结", 5: "已过期", 6: "未解锁"}
_COUPON_WAIT = 0
# 气泡类型
_BUBBLE_TYPES = {"yangliu_sign", "yangliu_order_finish", "wyc_order_finish"}
# 等级礼券标识（car_package = 月月领券，周周/月月已合并）
_LEVEL_GIFT_PRIV_IDS = {"car_package"}
_LEVEL_GIFT_NAMES = {"月月领券", "周周领券"}

# 登录态失效错误码（各域名不一致：ut=6001 / common=101 / member=1001 / rewards=20001）
_TOKEN_DEAD_CODES = {6001, 101, 1001, 20001, -10003, 103}
_TOKEN_DEAD_KWS = ("登录信息失效", "登录已过期", "用户未登录", "token error", "请重新登录")

_MAX_RETRY = 2              # 只读请求重试次数（写操作不重试，避免重复领取）
_SLEEP_STEP = (0.8, 1.8)    # 同一账号内各接口之间的随机间隔（秒）
_SLEEP_ACCOUNT = (3, 6)     # 账号之间的随机间隔（秒）
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _mask(phone):
    """手机号脱敏：前 3 + **** + 后 4。"""
    s = str(phone or "")
    return (s[:3] + "****" + s[-4:]) if len(s) >= 7 else s


def _rand_hex(n=32):
    return secrets.token_hex(n // 2)


def _rand_b64(n=22):
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _now():
    return datetime.datetime.now()


def _as_reward_list(item):
    """priv_reward 可能是 dict（直通类）也可能是 list（券类），统一成 list。"""
    reward = item.get("priv_reward")
    if isinstance(reward, dict):
        return [reward]
    if isinstance(reward, list):
        return [r for r in reward if isinstance(r, dict)]
    return []


class DiDiWelfare:
    """单个滴滴账号的福利中心自动化。任何一步失败只影响本步骤，不中断账号。"""

    def __init__(self, raw_token):
        # 支持「备注#token」，备注只用于日志
        self.alias, self.token = "", str(raw_token or "").strip()
        if "#" in self.token:
            head, tail = self.token.split("#", 1)
            if tail.strip():
                self.alias, self.token = head.strip(), tail.strip()
        self.phone = ""
        self.token_dead = False
        # 统计
        self.ok = 0
        self.skip = 0
        self.warn = 0
        self.err = 0
        self.bonus_before = None
        self.bonus_after = None
        self.signed = False
        self.task_enrolled = 0
        self.task_rewarded = 0
        self.progress_rewarded = 0
        self.coupon_got = 0
        self.divide_joined = 0
        self.divide_rewarded = 0
        self._janitor_info = None
        self._claimed = set()
        self.pending_tasks = []
        # 会话标识：每次运行随机生成，服务端仅用于串联埋点，不参与鉴权
        self.xpsid = _rand_hex()
        self.xpsid_root = _rand_hex()
        self.xpsid_from = _rand_hex()
        self.xoid = _rand_b64()
        self.device_id = _rand_hex()
        self.client = httpx.AsyncClient(
            verify=False,
            timeout=_TIMEOUT,
            follow_redirects=True,
            trust_env=False,          # 不读系统代理：本机 HTTP_PROXY 会把请求拦成 502
            proxy=proxy or None,
            headers={"User-Agent": _UA},
        )

    # ── 日志 ────────────────────────────────────────────────
    def _who(self):
        tag = self.alias or self.phone or "账号"
        return f"【{tag}】"

    def _log(self, mark, msg):
        log.log(f"{mark} {self._who()} {msg}")

    def _ok(self, msg):
        self.ok += 1
        self._log("✅", msg)

    def _skip_msg(self, msg):
        self.skip += 1
        self._log("⏭️", msg)

    def _warn_msg(self, msg):
        self.warn += 1
        self._log("⚠️", msg)

    def _err_msg(self, msg):
        self.err += 1
        self._log("❌", msg)

    # ── 请求层 ──────────────────────────────────────────────
    def _common_body(self):
        """福利中心公共请求参数。"""
        env = {
            "cityId": str(city_id),
            "token": self.token,
            "longitude": lng,
            "latitude": lat,
            "appid": _APPID,
            "fromChannel": "1",
            "deviceId": self.device_id,
            "ddfp": self.device_id,
            "appVersion": _APP_VERSION,
            "userAgent": _UA,
        }
        return {
            "xbiz": "",
            "prod_key": "welfare-center",
            "xpsid": self.xpsid,
            "dchn": dchn,
            "xoid": self.xoid,
            "xenv": "passenger",
            "xspm_from": "",
            "xpsid_root": self.xpsid_root,
            "xpsid_from": self.xpsid_from,
            "xpsid_share": "",
            "token": self.token,
            "lat": lat,
            "lng": lng,
            "platform": "na",
            "env": json.dumps(env, ensure_ascii=False),
        }

    def _welfare_headers(self):
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "source-type": "5",
            "Origin": "https://prod.didi.cn",
            "Referer": "https://prod.didi.cn/",
            "X-Requested-With": "com.sdu.didi.psnger",
        }

    def _janitor_headers(self):
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Didi-Ticket": self.token,
        }

    async def _request(self, method, url, *, retries=_MAX_RETRY, **kwargs):
        """统一请求：返回 (errno, errmsg, data)；errno=None 表示网络/HTTP/解析层失败。"""
        last = ""
        for attempt in range(retries + 1):
            try:
                resp = await self.client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                return None, f"网络异常（{last[:80]}）", None
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}", None
            try:
                payload = resp.json()
            except ValueError:
                return None, f"响应非 JSON：{resp.text[:100]}", None
            if not isinstance(payload, dict):
                return None, f"响应结构异常：{str(payload)[:100]}", None
            errno = payload.get("errno")
            errmsg = str(payload.get("errmsg") or "")
            if errno in _TOKEN_DEAD_CODES or any(k in errmsg for k in _TOKEN_DEAD_KWS):
                self.token_dead = True
            return errno, errmsg, payload.get("data")
        return None, f"网络异常（{last[:80]}）", None

    async def _post_welfare(self, path, extra=None, *, retries=_MAX_RETRY):
        body = self._common_body()
        if extra:
            body.update(extra)
        return await self._request(
            "POST", f"{_HOST_UT}{path}", json=body,
            headers=self._welfare_headers(), retries=retries,
        )

    async def _get_json(self, url, params=None, headers=None):
        return await self._request("GET", url, params=params, headers=headers or {"User-Agent": _UA})

    # ── 查询 ────────────────────────────────────────────────
    async def fetch_profile(self):
        errno, msg, data = await self._get_json(
            f"{_HOST_COMMON}/passenger/getprofile", {"token": self.token})
        if errno == 0 and isinstance(data, dict):
            self.phone = _mask(data.get("phone"))
        return self.phone

    async def fetch_bonus(self):
        """福利金余额（rewards 域，与福利中心 bonus_info.total_amount 同源）。"""
        errno, msg, data = await self._get_json(
            f"{_HOST_REWARDS}/loyalty_credit/bonus/getWelfareUsage4Wallet",
            {"token": self.token, "city_id": city_id})
        if errno == 0 and isinstance(data, dict):
            try:
                return int(data.get("balance") or 0)
            except Exception:
                return None
        return None

    async def report_bonus_record(self):
        """福利金今日获得明细（只读，用于核对到账）。"""
        errno, msg, data = await self._get_json(
            f"{_HOST_REWARDS}/loyalty_credit/bonus/getGottenWelfareRecord",
            {"token": self.token, "city_id": city_id, "page": 1})
        if errno != 0:
            return
        records = (data or {}).get("records") or (data or {}).get("list") or []
        today = _now().strftime("%Y-%m-%d")
        got = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            ts = str(rec.get("create_time") or rec.get("time") or "")
            if ts[:10] != today:
                continue
            got.append(f"{rec.get('title') or rec.get('desc') or '福利金'}+{rec.get('amount') or rec.get('value')}")
        if got:
            self._log("ℹ️", f"今日福利金明细：{'；'.join(got[:6])}")

    async def fetch_bubble(self):
        errno, msg, data = await self._post_welfare(_P["bubble"])
        if errno != 0:
            self._warn_msg(f"气泡查询失败：{msg or errno}")
            return []
        return (data or {}).get("bubble_list") or []

    async def fetch_task_list(self):
        """返回 (activity_id, task_list, progress_conf, current_progress, total_progress)。"""
        errno, msg, data = await self._post_welfare(_P["task_list"], {"show_type": "all"})
        if errno != 0:
            self._warn_msg(f"任务列表查询失败：{msg or errno}")
            return None
        data = data or {}
        return (
            data.get("activity_id"),
            data.get("task_list") or [],
            data.get("progress_conf") or {},
            data.get("current_progress") or 0,
            data.get("total_progress") or 0,
        )

    async def fetch_divide_data(self):
        errno, msg, data = await self._post_welfare(_P["divide_data"])
        if errno != 0:
            self._warn_msg(f"瓜分数据查询失败：{msg or errno}")
            return None
        return data or {}

    # ── 领取：签到 / 气泡 ───────────────────────────────────
    async def sign_in(self):
        errno, msg, data = await self._post_welfare(
            _P["sign"], {"share_id": "", "dchn": dchn}, retries=0)
        data = data if isinstance(data, dict) else {}
        if errno == 0:
            self.signed = True
            amount = (data.get("subsidy_state") or {}).get("subsidy_amount")
            self._ok(f"签到成功，获得 {amount} 福利金")
            rec = data.get("recommend_coupon")
            if isinstance(rec, dict) and rec:
                self._log("ℹ️", f"签到推荐券：{rec.get('name') or rec.get('coupon_name') or '（无名称）'}")
                await self.bind_coupon(rec, source="签到推荐券")
            return True
        if errno == 40009:
            self._skip_msg("今日已签到")
            return False
        self._warn_msg(f"签到未通过：{msg or '未知原因'}（errno={errno}）")
        return False

    async def click_bubbles(self):
        """气泡领奖：完单返 / 扬柳签到等。签到气泡由 sign_in 覆盖，这里跳过。"""
        bubbles = await self.fetch_bubble()
        if not bubbles:
            return
        for bubble in bubbles:
            btype = str(bubble.get("bubble_type") or "")
            if btype not in _BUBBLE_TYPES:
                continue
            reward = bubble.get("reward_count") or 0
            label = bubble.get("pre_content") or btype
            if not reward:
                self._skip_msg(f"气泡「{label}」暂无可领奖励")
                continue
            errno, msg, _ = await self._post_welfare(
                _P["click_bubble"],
                {"cycle_id": bubble.get("cycle_id") or "", "bubble_type": btype},
                retries=0,
            )
            if errno == 0:
                self._ok(f"气泡「{label}」领到 {reward} 福利金")
            else:
                self._warn_msg(f"气泡「{label}」领取未通过：{msg or errno}")

    # ── 领取：任务 ──────────────────────────────────────────
    async def enroll_tasks(self):
        """接任务：not_enroll → ing。复杂任务（需真实完单）默认不接。"""
        result = await self.fetch_task_list()
        if not result:
            return
        activity_id, task_list, _, _, _ = result
        pending = [t for t in task_list if t.get("status") == "not_enroll"]
        if not pending:
            self._skip_msg("没有可接的任务")
            return
        done = 0
        for task in pending:
            if done >= max_task:
                self._warn_msg(f"已达单次接任务上限 {max_task}，剩余 {len(pending) - done} 个下次再试")
                break
            tag = str(task.get("task_tag") or "")
            title = task.get("title") or task.get("type") or "未命名任务"
            if tag == "complex" and not do_task_complex:
                self._skip_msg(f"跳过复杂任务「{title}」（需真实完单，接口做不了）")
                continue
            payload = {
                "activity_id": task.get("activity_id") or activity_id,
                "task_id": task.get("task_id"),
                "type": task.get("type"),
                "type_origin": task.get("type_origin"),
                "share_id": "",
                "divide_share_id": "",
            }
            errno, msg, data = await self._post_welfare(_P["receive_task"], payload, retries=0)
            data = data if isinstance(data, dict) else {}
            if errno == 0 and data.get("result") is not False:
                done += 1
                self.task_enrolled += 1
                self._log("ℹ️", f"已接任务「{title}」（需在 App 内完成才算数）")
            elif errno == 0:
                self._warn_msg(f"接任务未生效「{title}」：{json.dumps(data, ensure_ascii=False)[:120]}")
            else:
                self._warn_msg(f"接任务失败「{title}」：{msg or errno}")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    async def claim_task_rewards(self):
        """领任务奖励：complete → finish。"""
        result = await self.fetch_task_list()
        if not result:
            return
        activity_id, task_list, _, _, _ = result
        ready = [t for t in task_list if t.get("status") == "complete"]
        self.pending_tasks = [
            str(t.get("title") or t.get("type"))
            for t in task_list if t.get("status") == "ing"
        ]
        if not ready:
            self._skip_msg("没有待领取的任务奖励（进行中的任务需在 App 内完成后才可领）")
            return
        for task in ready:
            title = task.get("title") or task.get("type") or "未命名任务"
            errno, msg, _ = await self._post_welfare(
                _P["task_reward"],
                {"activity_id": task.get("activity_id") or activity_id, "task_id": task.get("task_id")},
                retries=0,
            )
            if errno == 0:
                self.task_rewarded += 1
                self._ok(f"领取任务奖励成功「{title}」")
            elif errno == 1003:
                self._skip_msg(f"任务奖励已领过「{title}」")
            else:
                self._warn_msg(f"领取任务奖励失败「{title}」：{msg or errno}")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    async def claim_progress_rewards(self):
        """领阶梯奖励：入参是 instance_id（node_reward_info.instance_id_str），不是 node_id。"""
        result = await self.fetch_task_list()
        if not result:
            return
        _, _, progress_conf, current, total = result
        nodes = progress_conf.get("nodes") or []
        if not nodes:
            self._skip_msg("当前没有阶梯奖励配置")
            return
        self._log("ℹ️", f"任务进度 {current}/{total}，阶梯节点 {len(nodes)} 个")
        for node in nodes:
            name = node.get("node_name") or f"节点{node.get('node_id')}"
            need = node.get("task_count") or 0
            info = node.get("node_reward_info") or {}
            instance_id = info.get("instance_id_str") or info.get("instance_id")
            if not instance_id:
                if current >= need:
                    self._warn_msg(f"阶梯奖励「{name}」已达标但服务端未下发 instance_id，稍后重试")
                else:
                    self._skip_msg(f"阶梯奖励「{name}」未达标（{current}/{need}）")
                continue
            errno, msg, _ = await self._post_welfare(
                _P["task_progress_reward"], {"instance_id": instance_id}, retries=0)
            if errno == 0:
                self.progress_rewarded += 1
                self._ok(f"领取阶梯奖励成功「{name}」+{node.get('reward_amount')}")
            else:
                self._warn_msg(f"领取阶梯奖励失败「{name}」：{msg or errno}")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    # ── 领取：券 ────────────────────────────────────────────
    async def bind_coupon(self, card, source=""):
        """领券。入参 {activity_id, group_id, coupon_conf_id, group_date}。

        福利中心与天天神券是两个同构接口、参数一致；先打福利中心，失败回落天天神券通道，
        避免任一侧下线导致整体失效。
        """
        if not isinstance(card, dict):
            return False
        act = card.get("activity_id")
        if act is None or card.get("coupon_conf_id") is None:
            return False
        try:
            act = int(act)          # activity_id 需为数字
        except Exception:
            pass
        payload = {
            "activity_id": act,
            "group_id": card.get("group_id"),
            "coupon_conf_id": card.get("coupon_conf_id"),
            "group_date": card.get("group_date"),
        }
        # 同一张券会在多个活动分组里重复出现（同一批次可能对应不同 conf_id），本次运行内只尝试一次
        key = (str(act), str(card.get("batch_id") or card.get("coupon_conf_id")))
        if key in self._claimed:
            return False
        self._claimed.add(key)
        name = card.get("name") or card.get("coupon_title") or card.get("remark") or "优惠券"
        tag = f"{source}·{name}" if source else name
        errno, msg, _ = await self._post_welfare(_P["coupon_bind"], payload, retries=0)
        if errno == 0:
            self.coupon_got += 1
            self._ok(f"领券成功「{tag}」")
            return True
        if errno in (1003, 40009):
            self._skip_msg(f"券已领过「{tag}」")
            return False
        errno2, msg2, _ = await self._request(
            "POST", f"{_HOST_UT}{_P['janitor_bind']}", json=payload,
            headers=self._janitor_headers(), retries=0)
        if errno2 == 0:
            self.coupon_got += 1
            self._ok(f"领券成功「{tag}」（janitor 通道）")
            return True
        self._warn_msg(f"领券失败「{tag}」：{msg or errno} / janitor：{msg2 or errno2}")
        return False

    # ── 瓜分福利金 ──────────────────────────────────────────
    async def run_divide(self):
        """瓜分：未来场次 status=1 报名；今日场次 status=2 领奖励。

        数据结构：
          data.divide_data = {system: 秒级时间戳,
                              divide: {"YYYY-MM-DD": {activity_id, task_id, status,
                                       button:{count}, reward:{total_count}}},
                              invite: {...}}
          status: 1=未参与 2=进行中 3=待瓜分
        """
        data = await self.fetch_divide_data()
        if data is None:
            return
        divide_data = data.get("divide_data") or {}
        divide_map = divide_data.get("divide")
        if not isinstance(divide_map, dict) or not divide_map:
            self._skip_msg("当前没有进行中的瓜分活动")
            return
        # 服务端时间优先（客户端时区可能偏）
        try:
            server_now = datetime.datetime.fromtimestamp(int(divide_data.get("system") or 0))
            if server_now.year < 2020:
                server_now = _now()
        except Exception:
            server_now = _now()
        today = server_now.strftime("%Y-%m-%d")
        balance = self.bonus_before

        for date_key, item in divide_map.items():
            if not isinstance(item, dict) or not item.get("activity_id"):
                continue
            status = item.get("status")
            status_zh = _DIVIDE_STATUS_ZH.get(status, f"未知({status})")
            if str(date_key) == today:
                if status == 2:
                    errno, msg, res = await self._post_welfare(
                        _P["divide_reward"],
                        {"activity_id": item.get("activity_id"), "task_id": item.get("task_id")},
                        retries=0,
                    )
                    if errno == 0 and (res or {}).get("result") is not False:
                        self.divide_rewarded += 1
                        self._ok(f"领取今日瓜分奖励成功（{date_key}）")
                    elif errno == 1003:
                        self._skip_msg(f"今日瓜分奖励已领过（{date_key}）")
                    else:
                        self._warn_msg(f"领取今日瓜分奖励失败（{date_key}）：{msg or errno}")
                elif status == 3:
                    self._skip_msg(f"今日瓜分已结算待开奖（{date_key}）")
                else:
                    self._skip_msg(f"今日未参与瓜分（{date_key}，昨日未报名）")
                continue
            # 未来场次：报名
            if status != 1:
                self._skip_msg(f"场次 {date_key} 已报名（{status_zh}）")
                continue
            count = ((item.get("button") or {}).get("count")) or 0
            if balance is not None and count and balance < count:
                self._warn_msg(f"场次 {date_key} 需 {count} 福利金，余额 {balance} 不足，跳过报名")
                continue
            errno, msg, res = await self._post_welfare(
                _P["join_divide"],
                {"activity_id": item.get("activity_id"), "count": count, "type": "ut_bonus"},
                retries=0,
            )
            if errno == 0 and (res or {}).get("result") is not False:
                self.divide_joined += 1
                self._ok(f"报名瓜分成功（{date_key}，投入 {count} 福利金）")
            elif errno == 1003:
                self._skip_msg(f"场次 {date_key} 已报名")
            else:
                self._warn_msg(f"报名瓜分失败（{date_key}）：{msg or errno}")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    # ── 会员权益 ───────────────────────────────────────────
    async def fetch_privileges(self):
        """权益列表。注意：路径与响应结构都变过，见模块 docstring 的「本次修复」。"""
        errno, msg, data = await self._get_json(
            f"{_HOST_MEMBER}{_M['privileges']}",
            {"token": self.token, "city_id": city_id})
        if errno != 0:
            self._warn_msg(f"权益列表查询失败：{msg or errno}")
            return []
        data = data or {}
        items = data.get("priv_info_list")
        if items is None:      # 兼容旧字段名，避免接口回滚时拿不到数据
            items = data.get("privileges") or []
            for it in items:
                if isinstance(it, dict) and "priv_name" not in it:
                    it["priv_name"] = it.get("name")
        if not items:
            self._warn_msg("权益列表为空（接口返回结构可能已调整）")
        else:
            self._log(
                "ℹ️",
                f"{data.get('title') or '会员权益'}（V{data.get('current_level')}，{len(items)} 项）",
            )
        return [it for it in items if isinstance(it, dict)]

    @staticmethod
    def _find_priv(items, names, priv_ids):
        for it in items:
            if it.get("priv_id") in priv_ids or it.get("priv_name") in names:
                return it
        return None

    async def receive_level_gift(self):
        """等级礼券（周周/月月领券）。

        返回结构：priv_info_list[].priv_id=car_package / priv_name=月月领券
                → priv_reward[].coupons[] = {batch_id, coupon_title, status}
        请求：POST {batch_id, gift_type:1}，失败时回落 GET 查询参数写法
        """
        items = await self.fetch_privileges()
        if not items:
            return
        item = self._find_priv(items, _LEVEL_GIFT_NAMES, _LEVEL_GIFT_PRIV_IDS)
        if not item:
            self._skip_msg("当前等级没有等级礼券权益")
            return
        coupons = []
        for reward in _as_reward_list(item):
            coupons.extend([c for c in (reward.get("coupons") or []) if isinstance(c, dict)])
        if not coupons:
            self._skip_msg(f"「{item.get('priv_name')}」暂无可领券")
            return
        for coupon in coupons:
            status = coupon.get("status")
            name = f"{coupon.get('remark') or ''}{coupon.get('coupon_title') or coupon.get('name') or ''}"
            if status != _COUPON_WAIT:
                status_zh = _COUPON_STATUS_ZH.get(status, status) if isinstance(status, int) else status
                self._log("ℹ️", f"  · {name}（{status_zh}）")
                continue
            batch_id = coupon.get("batch_id")
            if not batch_id:
                self._warn_msg(f"「{name}」缺少 batch_id，跳过")
                continue
            errno, msg, data = await self._request(
                "POST", f"{_HOST_MEMBER}{_M['level_gift']}",
                json={"batch_id": batch_id, "gift_type": 1, "token": self.token, "city_id": city_id},
                headers={"User-Agent": _UA, "Content-Type": "application/json"},
                retries=0,
            )
            if errno != 0:
                # 回落 GET 查询参数写法
                errno2, msg2, data2 = await self._get_json(
                    f"{_HOST_MEMBER}{_M['level_gift']}",
                    {
                        "xbiz": "", "prod_key": "wyc-vip-level", "xpsid": "", "dchn": "", "xoid": "",
                        "xenv": "passenger", "xspm_from": "", "xpsid_root": "", "xpsid_from": "",
                        "xpsid_share": "", "token": self.token, "batch_id": batch_id,
                        "env": "{}", "gift_type": 1, "city_id": city_id,
                    },
                )
                if errno2 == 0:
                    self.coupon_got += 1
                    self._ok(f"领取等级礼券成功「{name}」（GET 通道）")
                    await asyncio.sleep(random.uniform(*_SLEEP_STEP))
                    continue
                self._warn_msg(f"领取等级礼券失败「{name}」：{msg or errno} / GET：{msg2 or errno2}")
                continue
            self.coupon_got += 1
            self._ok(f"领取等级礼券成功「{name}」")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    async def receive_privilege(self, priv_name, priv_ids, path, payload, ok_text):
        """通用权益领取：need_received / is_enable 判定后 POST。

        行程意外险走 /dmember/h5/bindPrivilege {type:3}；
        其余走 /pios/mlMember/h5/receivePrivilege {privilege_type:N}。
        """
        items = await self.fetch_privileges()
        if not items:
            return
        item = self._find_priv(items, {priv_name}, set(priv_ids))
        if not item:
            self._skip_msg(f"当前等级没有「{priv_name}」权益")
            return
        if item.get("is_enable") is False:
            self._skip_msg(f"「{priv_name}」当前不可用")
            return
        rewards = _as_reward_list(item)
        if rewards and rewards[0].get("used_info_desc"):
            self._log("ℹ️", f"「{priv_name}」{rewards[0].get('used_info_desc')}")
        errno, msg, _ = await self._request(
            "POST", f"{_HOST_MEMBER}{path}", json=payload,
            headers={"User-Agent": _UA, "Content-Type": "application/json"}, retries=0)
        if errno == 0:
            self._ok(ok_text)
        elif errno in (1003, 40009):
            self._skip_msg(f"「{priv_name}」已领取")
        else:
            self._warn_msg(f"「{priv_name}」领取失败：{msg or errno}")

    # ── 天天神券 ───────────────────────────────────────────
    async def janitor_page(self):
        """天天神券页面数据：签到进度、抽奖次数、可领券列表。结果缓存，一次运行只查一次。"""
        if self._janitor_info is not None:
            return self._janitor_info
        xpsid = _rand_hex()
        body = {
            "xbiz": "", "prod_key": "ut-coupon-center", "xpsid": xpsid, "dchn": _JANITOR_DCHN,
            "xoid": self.xoid, "xenv": "passenger", "xspm_from": "",
            "xpsid_root": xpsid, "xpsid_from": xpsid, "xpsid_share": "",
            "root_xpsid": xpsid, "f_xpsid": xpsid,
            "args": [{"dchn": _JANITOR_DCHN, "prod_key": "ut-coupon-center",
                      "runtime_args": {"token": self.token, "lat": lat, "lng": lng,
                                       "env": {}, "Didi-Ticket": self.token}}],
        }
        errno, msg, data = await self._request(
            "POST", f"{_HOST_API}/webx/chapter/page/batch/config", json=body,
            headers=self._janitor_headers(), retries=0)
        if errno != 0:
            self._warn_msg(f"天天神券页面查询失败：{msg or errno}")
            self._janitor_info = {}
            return self._janitor_info
        info = {}
        for conf in (data or {}).get("conf") or []:
            if conf.get("dchn") != _JANITOR_DCHN:
                continue
            sign_info = ((conf.get("strategy_data") or {}).get("data") or {}).get("sign_info")
            if isinstance(sign_info, dict):
                info = sign_info
                break
        self._janitor_info = info
        return info

    async def janitor_sign(self):
        info = await self.janitor_page()
        if not info:
            self._skip_msg("天天神券页面没有签到数据")
            return
        cur = info.get("current_progress") or 0
        total = info.get("total_progress") or 0
        activity_id = info.get("activity_id")
        instance_id = info.get("instance_id")
        if not activity_id or not instance_id:
            self._warn_msg("天天神券签到参数缺失（activity_id / instance_id）")
            return
        body = {
            "xbiz": "", "prod_key": "ut-coupon-center", "xpsid": "", "dchn": _JANITOR_DCHN,
            "xoid": self.xoid, "xenv": "passenger", "xspm_from": "",
            "xpsid_root": self.xpsid_root, "xpsid_from": self.xpsid_from, "xpsid_share": "",
            "activity_id": activity_id, "instance_id": instance_id, "city_id": city_id,
            "env": {
                "isHitButton": True, "newAppid": 10000, "userAgent": _UA, "openId": "",
                "ddfp": self.device_id, "appVersion": _APP_VERSION,
                "deviceId": self.device_id, "wifi": 2,
            },
        }
        errno, msg, data = await self._request(
            "POST", f"{_HOST_UT}{_P['janitor_sign']}", json=body,
            headers=self._janitor_headers(), retries=0)
        if errno == 0:
            self._ok(f"天天神券签到成功（连签 {cur}/{total}）")
        elif errno in (1003, 40009) or "已签" in (msg or ""):
            self._skip_msg(f"天天神券今日已签到（连签 {cur}/{total}）")
        else:
            self._warn_msg(f"天天神券签到失败：{msg or errno}")

    async def janitor_lottery(self):
        info = await self.janitor_page()
        if not info:
            self._skip_msg("天天神券页面没有抽奖数据")
            return
        chance = int(info.get("lottery_chance") or 0)
        act_id = info.get("activity_id")
        if not chance:
            self._skip_msg("今日没有抽奖次数")
            return
        if not act_id:
            self._warn_msg("抽奖活动 id 缺失")
            return
        self._log("ℹ️", f"今日抽奖次数 {chance}")
        for i in range(chance):
            errno, msg, data = await self._request(
                "POST", f"{_HOST_UT}{_P['janitor_lottery']}",
                json={"act_id": act_id, "activity_id": act_id, "city_id": city_id},
                headers=self._janitor_headers(), retries=0)
            if errno != 0:
                self._warn_msg(f"第 {i + 1} 次抽奖失败：{msg or errno}"
                               f"（{json.dumps(data, ensure_ascii=False)[:120]}）")
                return
            prize = ((data or {}).get("prize_data") or [{}])[0].get("name") or "未知奖品"
            self._ok(f"抽奖成功，获得「{prize}」")
            if i + 1 < chance:
                await asyncio.sleep(5)

    async def janitor_skyfall(self):
        """天降券弹窗直领（默认关）。

        dchn 随页面变化（常见 PxJanq9 / AE6kvgY / 3pak58P），
        这里用每日精选页的 PxJanq9；若返回空 coupons 属正常（该页当天没有天降券）。
        """
        body = {
            "xbiz": "", "prod_key": "ut-coupon-center", "xpsid": "", "dchn": "PxJanq9",
            "xoid": self.xoid, "xenv": "passenger", "xspm_from": "",
            "xpsid_root": self.xpsid_root, "xpsid_from": self.xpsid_from, "xpsid_share": "",
            "limit": True, "channel": 10000, "platform": "na", "type": 2, "delay": True,
            "city_id": city_id,
            "env": {"isHitButton": True, "newAppid": 10000, "userAgent": _UA},
        }
        errno, msg, data = await self._request(
            "POST", f"{_HOST_UT}{_P['janitor_skyfall']}", json=body,
            headers=self._janitor_headers(), retries=0)
        if errno != 0:
            self._warn_msg(f"天降券查询失败：{msg or errno}")
            return
        coupons = (data or {}).get("coupons") or []
        if not coupons:
            self._skip_msg("当前没有天降券")
            return
        for coupon in coupons:
            await self.bind_coupon(coupon, source="天降券")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    # ── 每日精选 / 限时抢 ───────────────────────────────────
    async def daily_pick(self):
        errno, msg, data = await self._request(
            "POST", f"{_HOST_API}/webx/chapter/page/batch/config",
            json={
                "dchn": "PxJanq9",
                "args": [
                    {"dchn": "kkXgpzO", "prod_key": "ut-limited-seckill",
                     "runtime_args": {"token": self.token, "lat": lat, "lng": lng,
                                      "env": {}, "Didi-Ticket": self.token}},
                    {"dchn": "gL3E8qZ", "prod_key": "ut-support-coupon",
                     "runtime_args": {"token": self.token, "lat": lat, "lng": lng,
                                      "env": {}, "Didi-Ticket": self.token}},
                ],
            },
            headers=self._janitor_headers(), retries=0,
        )
        if errno != 0:
            self._warn_msg(f"每日精选查询失败：{msg or errno}")
            return
        status_zh = {1: "可领取", 2: "已领取", 4: "已抢光/抽奖", 5: "未到时间", 6: "待前置条件"}
        for activity in (data or {}).get("conf") or []:
            strategy = ((activity.get("strategy_data") or {}).get("data") or {})
            if activity.get("dchn") == "gL3E8qZ":
                coupons = ((strategy.get("daily_coupon") or {}).get("coupons")) or []
                if coupons:
                    self._log("ℹ️", f"每日精选 {len(coupons)} 张券")
                for coupon in coupons:
                    name = coupon.get("name") or "优惠券"
                    st = coupon.get("status")
                    self._log("ℹ️", f"  · {name}（{status_zh.get(st, st)}）")
                    if st != 1:
                        continue
                    if name == "打车5元券":
                        self._skip_msg(f"「{name}」需分享助力，不支持自动领取")
                        continue
                    if str(coupon.get("activity_id")) == "10010":
                        self._skip_msg(f"「{name}」需在目的地栏搜「领券」，不支持自动领取")
                        continue
                    await self.bind_coupon(coupon, source="每日精选")
                    await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            elif activity.get("dchn") == "kkXgpzO":
                for seckill in strategy.get("seckill") or []:
                    if int(seckill.get("status") or 0) not in (1, 3):
                        continue
                    for coupon in seckill.get("coupons") or []:
                        if coupon.get("status") != 1:
                            continue
                        await self.bind_coupon(coupon, source="限时抢")
                        await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    # ── 主流程 ──────────────────────────────────────────────
    async def _safe(self, name, coro):
        """单步隔离：任何一步异常只记一次错，不影响后续步骤。"""
        try:
            await coro
        except Exception as exc:
            self._err_msg(f"{name} 异常：{type(exc).__name__}: {exc}")

    async def _run_tasks(self):
        """签到 + 气泡 + 福利中心任务。"""
        if do_sign:
            await self._safe("签到", self.sign_in())
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        await self._safe("气泡领奖", self.click_bubbles())
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        if not do_task:
            return
        if do_task_enroll:
            await self._safe("接任务", self.enroll_tasks())
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        await self._safe("任务奖励", self.claim_task_rewards())
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        await self._safe("阶梯奖励", self.claim_progress_rewards())
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    async def _run_divide_and_legacy(self):
        """瓜分福利金 + 会员权益。"""
        if do_divide:
            await self._safe("瓜分福利金", self.run_divide())
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        if not do_legacy:
            return
        await self._safe("等级礼券", self.receive_level_gift())
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        await self._safe("行程意外险", self.receive_privilege(
            "行程意外险", ("travel_insurance",), _M["bind_privilege"],
            {"token": self.token, "type": 3}, "领取行程意外险成功"))
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        await self._safe("周三折上折", self.receive_privilege(
            "周三折上折", ("member_day_discount",), _M["member_day"],
            {"token": self.token, "privilege_type": 1}, "领取周三折上折成功"))
        await asyncio.sleep(random.uniform(*_SLEEP_STEP))

    async def _run_janitor_and_pick(self):
        """天天神券 + 每日精选。"""
        if do_janitor:
            await self._safe("天天神券签到", self.janitor_sign())
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            await self._safe("天天神券抽奖", self.janitor_lottery())
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if do_skyfall:
                await self._safe("天降券", self.janitor_skyfall())
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
        if do_daily_pick:
            await self._safe("每日精选", self.daily_pick())

    async def report_status(self):
        """只读巡检：把各体系当前状态打出来，不做任何写操作（--check 用）。"""
        bubbles = await self.fetch_bubble()
        for bubble in bubbles:
            self._log(
                "ℹ️",
                f"气泡「{bubble.get('pre_content') or bubble.get('bubble_type')}」"
                f"类型 {bubble.get('bubble_type')}，可领 {bubble.get('reward_count') or 0}",
            )
        result = await self.fetch_task_list()
        if result:
            _, tasks, _, cur, total = result
            dist = {}
            for task in tasks:
                key = task.get("status")
                dist[key] = dist.get(key, 0) + 1
            desc = "，".join(f"{_TASK_STATUS_ZH.get(k, k)} {v}" for k, v in dist.items())
            self._log("ℹ️", f"任务 {len(tasks)} 个（进度 {cur}/{total}）：{desc}")
            self.pending_tasks = [
                str(t.get("title") or t.get("type")) for t in tasks if t.get("status") == "ing"
            ]
        data = await self.fetch_divide_data()
        if data is not None:
            divide_map = (data.get("divide_data") or {}).get("divide")
            if isinstance(divide_map, dict) and divide_map:
                for date_key, item in divide_map.items():
                    status = (item or {}).get("status")
                    status_zh = _DIVIDE_STATUS_ZH.get(status) if isinstance(status, int) else None
                    self._log("ℹ️", f"瓜分场次 {date_key}：{status_zh or f'未知({status})'}")
            else:
                self._log("ℹ️", "瓜分：当前无进行中场次")
        items = await self.fetch_privileges()
        if items:
            names = [str(it.get("priv_name") or it.get("priv_id")) for it in items]
            self._log("ℹ️", f"会员权益 {len(items)} 项：{'、'.join(names)}")
            gift = self._find_priv(items, _LEVEL_GIFT_NAMES, _LEVEL_GIFT_PRIV_IDS)
            for reward in _as_reward_list(gift or {}):
                for coupon in reward.get("coupons") or []:
                    if not isinstance(coupon, dict):
                        continue
                    status = coupon.get("status")
                    status_zh = _COUPON_STATUS_ZH.get(status) if isinstance(status, int) else None
                    self._log(
                        "ℹ️",
                        f"  · 等级礼券 {coupon.get('coupon_title') or coupon.get('name') or '优惠券'}"
                        f"（{status_zh or f'状态{status}'}）",
                    )
        info = await self.janitor_page()
        if info:
            self._log(
                "ℹ️",
                f"天天神券：连签 {info.get('current_progress') or 0}/{info.get('total_progress') or 0}"
                f"，今日抽奖 {info.get('lottery_chance') or 0} 次",
            )

    async def run(self):
        try:
            await self._safe("账号信息", self.fetch_profile())
            self.bonus_before = await self.fetch_bonus()
            self._log(
                "▶️",
                f"开始执行（福利金 {self.bonus_before if self.bonus_before is not None else '-'}，城市 {city_id}）",
            )
            if self.token_dead:
                self._err_msg("token 已失效，请更新 DD_TOKENS")
                return

            if _CHECK_MODE:
                await self._safe("状态巡检", self.report_status())
            else:
                await self._run_tasks()
                await self._run_divide_and_legacy()
                await self._run_janitor_and_pick()

            if self.pending_tasks:
                self._log(
                    "ℹ️",
                    f"待完成 {len(self.pending_tasks)} 个任务（在 App 内操作后才会变成可领奖）："
                    f"{'、'.join(self.pending_tasks[:6])}"
                    + ("…" if len(self.pending_tasks) > 6 else ""),
                )
            self.bonus_after = await self.fetch_bonus()
            if do_record:
                await self._safe("福利金明细", self.report_bonus_record())
            delta = "-"
            if self.bonus_before is not None and self.bonus_after is not None:
                delta = f"+{self.bonus_after - self.bonus_before}"
            self._log(
                "📊",
                f"汇总 ▶ 签到{'✅' if self.signed else '⏭️'}"
                f" ｜ 接任务 {self.task_enrolled}（待完成 {len(self.pending_tasks)}）"
                f" ｜ 任务奖励 {self.task_rewarded} ｜ 阶梯奖励 {self.progress_rewarded}"
                f" ｜ 券 {self.coupon_got} ｜ 瓜分报名 {self.divide_joined}"
                f" ｜ 瓜分领奖 {self.divide_rewarded}"
                f" ｜ 福利金 {self.bonus_before}→{self.bonus_after}（{delta}）"
                f" ｜ ⚠️{self.warn} ❌{self.err}",
            )
        finally:
            await self.client.aclose()


async def main():
    if _CHECK_MODE:
        log.log("只读自检模式（--check）：只查询状态，不签到、不接任务、不领奖、不报名")
    tokens = [t for t in (dd_tokens or []) if t and t.strip()]
    if not tokens:
        log.log("未配置 DD_TOKENS（多账号用 & 分隔），退出")
        return
    log.log(f"共 {len(tokens)} 个账号，城市 {city_id}，坐标 {lat},{lng}")
    for idx, raw in enumerate(tokens):
        await DiDiWelfare(raw).run()
        if idx < len(tokens) - 1:
            await asyncio.sleep(random.uniform(*_SLEEP_ACCOUNT))


if __name__ == "__main__":
    asyncio.run(main())
    send_notification_message_collection(f"滴滴出行 - {_now().strftime('%Y/%m/%d')}")
