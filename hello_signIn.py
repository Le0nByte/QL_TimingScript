# -*- coding=UTF-8 -*-
# @Project          QL_TimingScript
# @fileName         hello_signIn.py
# @author           Echo
# @EditTime         2026/10/25
# cron: 0 10 * * *
# const $ = new Env('哈啰出行');
"""
哈啰出行 · 福利中心（奖励金 / 哈啰币）自动签到 + 状态巡检

变量格式：hl_token=token1@token2
  token  ：App 打开「我的 → 福利中心 / 奖励金」，抓任意一条 https://api.hellobike.com/api?... 请求，
           复制请求头 token（32 位小写十六进制），与请求体里的 "token" 字段相同。
  可选备注：主号#<token>@小号#<token>，备注只用于日志区分，不参与请求。

可选开关：
  hl_sign=0            关闭自动签到（默认 1）
  hl_task=0            关闭任务/奖励巡检（默认 1，全程只读）
  hl_award_detail=0    关闭「单单返」明细（默认 1，只读）
  hl_treasure=0        关闭宝箱状态巡检（默认 1，只读）
  hl_box=0             关闭自动开普通宝箱（默认 1）
  hl_box_opens=N       单次运行最多开几个箱（默认 1；>1 会等 20 分钟冷却）
  hl_mall=1            额外打印积分商城可兑商品（默认 0；脚本永不自动兑换，避免消耗币）
  hl_exchange_code=XXX 兑换码（默认空；多账号用 @ 一一对应，只填一个则仅第一个账号用）
  hl_city=028          城市码（影响任务清单，默认 028）
  hl_adcode=510108     行政区码（默认 510108）
  hl_version=7.0.60    请求体 version（跟随 App 版本，默认 7.0.60）
  hl_ua=...            自定义 User-Agent（默认用 7.0.60 App 内 WebView 的 UA）

能做什么：每日签到、自动开普通宝箱（每 20 分钟 1 个、每天上限 72 个）、币余额与到期提醒、
任务清单与单单返巡检（只读）、宝箱状态、积分商城门槛对比（只读）、可选兑换码兑换。

做不到什么：看视频、领任务、领奖励这几类奖励必须在 App 内真实完成，纯接口拿不到，
脚本不做，也不去猜这类接口名（猜了也只会拿到条件不满足的错误）。

两个入口：
  python hello_signIn.py              每日全量（签到 + 巡检 + 开 1 个箱）
  python hello_signIn.py --box-only   只开箱（配 20 分钟一次的高频 cron 吃满 72 个/天）
  python hello_signIn.py --check      只读自检（不签到、不开箱、不兑换）
"""
import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime

import httpx

import log
from get_env import get_env
from sendNotify import send_notification_message_collection

try:  # 面板/终端编码兜底，避免 GBK 控制台炸中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

hl_tokens = get_env("hl_token", "@") or get_env("hello_token", "@")
_CHECK_MODE = any(a in ("--check", "--probe", "-c") for a in sys.argv[1:])   # 只读自检：跳过一切写操作
_BOX_ONLY = any(a in ("--box-only", "--box") for a in sys.argv[1:])          # 只开宝箱（给高频 cron 用）
try_sign = (not _CHECK_MODE) and str(os.environ.get("hl_sign") or "1").strip() in ("1", "true", "True")
do_task = str(os.environ.get("hl_task") or "1").strip() in ("1", "true", "True")
do_award = str(os.environ.get("hl_award_detail") or "1").strip() in ("1", "true", "True")
do_treasure = str(os.environ.get("hl_treasure") or "1").strip() in ("1", "true", "True")
show_mall = str(os.environ.get("hl_mall") or "0").strip() in ("1", "true", "True")
do_box = (not _CHECK_MODE) and str(os.environ.get("hl_box") or "1").strip() in ("1", "true", "True")
try:
    box_opens = int(str(os.environ.get("hl_box_opens") or "1").strip() or 1)
except Exception:
    box_opens = 1


def _env_list(name):
    """读取可选的多值环境变量；不存在时静默返回空列表（不像 get_env 那样打日志）。"""
    raw = os.environ.get(name)
    if raw is None:
        return []
    return [s.strip() for s in re.split("@", raw) if s.strip()]


exchange_codes = _env_list("hl_exchange_code")
city_code = str(os.environ.get("hl_city") or "028").strip()
ad_code = str(os.environ.get("hl_adcode") or "510108").strip()

# ── 常量：换域名/换版本只改这里 ──
_API_HOST = "https://api.hellobike.com"
_API_VERSION = str(os.environ.get("hl_version") or "7.0.60").strip()
_MANUFACTURER = "OnePlus"
_UA = str(os.environ.get("hl_ua") or (
    "Mozilla/5.0 (Linux; Android 13; Pixel 6 Build/TQ3A.230805.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/105.0.5195.148 MYWeb/0.11.0.25052318"
)).strip()
_SYSTEM_CODE = 62          # 哈啰平台固定值
_PLATFORM = 4              # H5 平台标识
_A = {
    "sign_info": "common.welfare.new.signInfo",     # 签到状态
    "sign": "common.welfare.sign",                  # 签到（写）
    "points": "user.taurus.pointInfo",              # 哈啰币余额
    "tasks": "common.welfare.taurus.task",          # 福利中心任务清单
    "award_detail": "common.welfare.taskAwardDetail",   # 单单返任务明细
    "treasure": "common.welfare.query.treasure.box",    # 宝箱状态
    "banner": "welfare.scene.banner",               # 签到弹窗广告任务（66 币）
    "mall": "coupon.virtual.search",                # 积分商城（只读）
    "exchange": "user.account.exchange",            # 兑换码兑换（写）
    "box_open": "common.welfare.open.treasure.box",  # 开宝箱（写，纯接口真到账）
}
_CODE_OK = 0
_CODE_NO_LOGIN = (-10003, 103)   # -10003=客户端本地判失效；103=服务端「登录信息已失效, 请重新登录」
_SLEEP_STEP = (1.2, 2.2)   # 单个账号内部各接口之间的随机间隔（秒）
_SLEEP_ACCOUNT = (3, 6)    # 账号之间随机间隔（秒）
_MAX_RETRY = 2
_TASK_STATUS_ZH = {
    "INIT": "未开始", "RUNNING": "进行中", "FINISH": "已完成",
    "REWARD": "已领奖", "REWARDED": "已领奖", "EXPIRED": "已过期", "OFFLINE": "已下线",
}


def _rid():
    """H5 侧每条请求都带一个随机 requestid，仅用于链路追踪，服务端不校验。"""
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                   for _ in range(15))


def _mask(token):
    token = token or ""
    return token if len(token) <= 10 else f"{token[:6]}…{token[-4:]}"


def _now_ms():
    return int(datetime.now().timestamp() * 1000)


def _ts(ms):
    """毫秒时间戳 → 'MM-DD HH:MM'，0/None 原样返回 '-'"""
    try:
        ms = int(ms)
    except Exception:
        return "-"
    if ms <= 0:
        return "-"
    if ms > 10_000_000_000:      # 毫秒
        ms = ms / 1000
    return datetime.fromtimestamp(ms).strftime("%m-%d %H:%M")


class HelloWelfare:
    def __init__(self, raw):
        alias, token = self._parse(raw)
        self.alias = alias
        self.token = token
        self.tag = f"【{alias}】" if alias else f"【{_mask(token)}】"
        self.client = httpx.AsyncClient(
            base_url=_API_HOST,
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "user-agent": _UA,
                "token": token,
                "origin": "https://m.hellobike.com",
                "referer": "https://m.hellobike.com/",
                "x-requested-with": "com.jingyao.easybike",
                "accept-language": "zh-CN,zh;q=0.9",
            },
            verify=False,
            timeout=30,
            trust_env=False,
        )
        self.earned = 0        # 本轮到账哈啰币
        self.points = None     # 当前余额
        self.warn = 0
        self.err = 0
        self.signed_now = False
        self.box_opened = 0
        self.token_dead = False

    # ── 基础设施 ─────────────────────────────────────────────
    @staticmethod
    def _parse(raw):
        raw = (raw or "").strip()
        if "#" in raw:
            alias, token = raw.split("#", 1)
            return alias.strip(), token.strip()
        return "", raw

    def log(self, msg, decorate=True):
        log.log(log.decorate(f"{self.tag} {msg}") if decorate else f"{self.tag} {msg}")

    async def _post(self, key, extra=None, quiet=False):
        """统一请求层：返回 (code, msg, data)；网络/解析异常返回 (None, 原因, None)"""
        action = _A[key]
        body = {
            "from": "h5",
            "systemCode": _SYSTEM_CODE,
            "platform": _PLATFORM,
            "version": _API_VERSION,
            "action": action,
            "token": self.token,
        }
        if extra:
            body.update(extra)
        last = None
        for attempt in range(_MAX_RETRY):
            try:
                resp = await self.client.post(f"/api?{action}", json=body,
                                             headers={"requestid": _rid()})
                try:
                    payload = resp.json()
                except Exception:
                    text = " ".join(resp.text.split())[:120]
                    if not quiet:
                        self.log(f"接口 {key} 返回非 JSON（HTTP {resp.status_code}）：{text}")
                    return None, f"非JSON:{text}", None
                code = payload.get("code")
                if code in _CODE_NO_LOGIN:
                    self.token_dead = True
                return code, payload.get("msg") or payload.get("message"), payload.get("data")
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                if attempt < _MAX_RETRY - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        if not quiet:
            self.log(f"接口 {key} 请求异常：{last}")
        return None, last, None

    # ── 签到 ─────────────────────────────────────────────────
    async def fetch_sign_info(self):
        code, msg, data = await self._post("sign_info",
                                           {"newWelfare": True, "manufacturer": _MANUFACTURER})
        if self.token_dead:
            detail = msg or "登录信息已失效"
            self.log(f"token 已失效（code={code}，{detail}），请重新抓包替换 hl_token")
            self.err += 1
            return None
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"签到状态获取失败（code={code} msg={msg}），token 可能已失效，请重新抓包")
            self.err += 1
            return None
        return data

    def report_sign_info(self, d):
        btn = d.get("signBtnText") or "-"
        today = d.get("bountyCountToday") or "-"
        self.log(f"签到状态：{btn}｜今日可领 {today} 币｜连签信息：{d.get('title') or '-'}")
        modules = d.get("moduleInfo") or []
        extra = d.get("extraRewardBtnText")
        if extra:
            self.log(f"额外奖励：{extra}（需在 App 内看广告，纯接口领不到）")
        for m in modules:
            name = m.get("moduleName")
            sub = m.get("subTitle")
            if name:
                self.log(f"  活动位：{name}" + (f"｜{sub}" if sub else ""))

    async def do_sign(self, sign_info):
        """签到 + 结果汇报。返回 True=本次真正签到到账，False=未签到/已签/失败。"""
        if not try_sign:
            self.log("已按 hl_sign=0 关闭自动签到")
            return False
        btn = str((sign_info or {}).get("signBtnText") or "").strip()
        if btn and btn != "签到":
            self.log(f"签到按钮为「{btn}」，判定今日已签到，跳过签到请求")
            return False
        code, msg, data = await self._post("sign", {"newWelfare": True})
        data = data if isinstance(data, dict) else {}
        if code is None:
            self.log(f"签到请求异常：{msg}")
            self.err += 1
            return False
        if code != _CODE_OK:
            if code in _CODE_NO_LOGIN:
                self.log("签到失败：token 已失效，请重新抓包")
            else:
                self.log(f"签到失败：{msg or '未知错误'}（code={code}）")
            self.err += 1
            return False

        did_today = bool(data.get("didSignToday"))
        did_now = bool(data.get("doSignThisTime"))
        today_bonus = data.get("bountyCountToday")
        tomorrow_bonus = data.get("bountyCountTomorrow")
        chain = data.get("title") or "-"
        self.report_bonus_list(data)
        if did_now:
            try:
                self.earned += int(today_bonus or 0)
            except Exception:
                pass
            self.signed_now = True
            self.log(f"签到成功 ✅ +{today_bonus} 币（{chain}）｜明天可领 {tomorrow_bonus} 币")
            return True
        if did_today:
            self.log(f"今日已签到 ⏭️（今日档 +{today_bonus} 币，{chain}）｜明天可领 {tomorrow_bonus} 币")
            return False
        self.log(f"签到未生效 ⚠️：服务端未标记今日已签（{chain}），稍后重试或检查账号状态")
        self.warn += 1
        return False

    def report_bonus_list(self, data_from_sign):
        """签到返回的 7 日奖励表：标出哪几天已领、哪几档奖励更高。"""
        bonus = (data_from_sign or {}).get("bonusList") or []
        if not bonus:
            return
        plan, picked, got = [], [], 0
        for idx, b in enumerate(bonus, 1):
            cnt = b.get("bonusCount") or "-"
            plan.append(f"D{idx}:{cnt}{'✔' if b.get('didSign') else ''}")
            if b.get("didSign"):
                picked.append(f"D{idx}")
                try:
                    got += int(cnt)
                except Exception:
                    pass
        self.log("7 日签到档位：" + "  ".join(plan))
        if picked:
            self.log(f"已到手档位：{'、'.join(picked)}（共 {got} 币，断签会从 D1 重算）")

    # ── 余额 ─────────────────────────────────────────────────
    async def fetch_points(self, quiet=False):
        code, msg, data = await self._post("points", {"pointType": 1}, quiet=quiet)
        if code != _CODE_OK or not isinstance(data, dict):
            if not quiet:
                self.log(f"余额获取失败（code={code} msg={msg}）")
                self.err += 1
            return None
        self.points = data.get("points")
        amount = data.get("amount")
        expiring = data.get("expiring")
        self.log(f"哈啰币余额：{self.points} 币 ≈ {amount} 元"
                 + (f"｜即将过期 {expiring} 币" if expiring else ""))
        return data

    # ── 任务巡检（只读） ──────────────────────────────────────
    @staticmethod
    def _flatten_tasks(data):
        """把 taskGroupInfoDTOS 各分组摊平成 [(分组名, 任务 dict), ...]，兼容三种嵌套。"""
        out = []
        for g in (data or {}).get("taskGroupInfoDTOS") or []:
            gname = g.get("mainTitle") or g.get("moduleKey") or "任务"
            for key in ("taskCouponList", "taskShowDTOS"):
                for t in g.get(key) or []:
                    if isinstance(t, dict):
                        out.append((gname, t))
            newcomer = g.get("newComerGuideDTO")
            if isinstance(newcomer, dict):
                for t in newcomer.get("taskShowDTOS") or []:
                    if isinstance(t, dict):
                        out.append((gname, t))
        return out

    @staticmethod
    def _task_line(t):
        name = (t.get("taskName") or t.get("mainTitle") or "未命名任务")[:22]
        btn = t.get("btnName") or "-"
        st = _TASK_STATUS_ZH.get(str(t.get("taskStatus") or ""), t.get("taskStatus") or "-")
        cur, total = t.get("currentProgress"), t.get("totalProgress")
        prog = f"{cur}/{total}" if cur is not None and total not in (None, 0) else "-"
        worth = t.get("offerWorth")
        try:
            worth = f"{int(float(worth))}币" if worth not in (None, "") else "-"
        except Exception:
            worth = "-"
        return f"{name}｜{st}｜进度 {prog}｜奖励 {worth}｜按钮「{btn}」"

    async def report_tasks(self):
        code, msg, data = await self._post("tasks", {
            "channelId": 4,
            "sceneNameEn": "platform_points",
            "subSceneNameEn": "platform_points_order_return",
            "taskStatusList": ["INIT", "RUNNING"],
            "queryUnrewardedTasks": True,
            "newWelfare": True,
            "cityCode": city_code,
            "adCode": ad_code,
        })
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"任务清单获取失败（code={code} msg={msg}）")
            self.err += 1
            return
        tasks = self._flatten_tasks(data)
        if not tasks:
            self.log("任务清单为空（当前账号无可用任务）")
            return

        groups = {}
        for gname, t in tasks:
            groups.setdefault(gname, []).append(t)

        def _prog_done(t):
            cur, total = t.get("currentProgress"), t.get("totalProgress")
            try:
                return total not in (None, 0) and float(cur or 0) >= float(total)
            except Exception:
                return False

        reached = [t for _, t in tasks if _prog_done(t)]                       # 进度已满：已达标可领
        claim_btn = [t for _, t in tasks if str(t.get("btnName") or "") in ("去领取", "领奖励")]  # 壳子任务按钮即领
        need_app = [t for _, t in tasks if not _prog_done(t)
                    and str(t.get("btnName") or "").startswith(("去", "立即", "领任务", "玩游戏", "返币"))]
        self.log(f"任务共 {len(tasks)} 个（{len(groups)} 组）：已达标 {len(reached)}｜按钮可领 {len(claim_btn)}"
                 f"｜需 App 内完成 {len(need_app)}")
        for gname, items in groups.items():
            self.log(f"▸ {gname}（{len(items)} 个）")
            for t in items[:6]:
                self.log("   " + self._task_line(t))
            if len(items) > 6:
                self.log(f"    …另有 {len(items) - 6} 个同类任务未展开")
        can_claim = reached + [t for t in claim_btn if t not in reached]
        if can_claim:
            names = "、".join((t.get("taskName") or "")[:14] for t in can_claim[:6])
            self.log(f"⚠️ 有 {len(can_claim)} 个任务可领/待领：{names} —— 领奖链路要客户端或广告回调，"
                     f"纯接口领不到，需在 App 内点一下")
            self.warn += 1
        else:
            self.log("当前没有「可领」任务；其余任务需在 App 内完成（看广告/下单/下载/授权等）")

    async def report_award_detail(self):
        code, msg, data = await self._post("award_detail",
                                           {"cityCode": city_code, "adCode": ad_code})
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"单单返明细获取失败（code={code} msg={msg}）")
            self.err += 1
            return
        items = [t for t in (data.get("taskCouponList") or []) if isinstance(t, dict)]
        if not items:
            self.log("单单返：暂无可参与任务")
            return
        self.log(f"单单返任务 {len(items)} 个：")
        for t in items[:8]:
            self.log("   " + self._task_line(t))

    async def report_banner_reward(self):
        code, msg, data = await self._post("banner",
                                           {"bannerScene": "welfare_sign_banner", "videoIndex": 1},
                                           quiet=True)
        if code != _CODE_OK or not isinstance(data, dict):
            return
        info = data.get("bannerTaskInfo") or {}
        bonuses = data.get("perFinishBonusList") or []
        if not info and not bonuses:
            return
        done = data.get("finishCount")
        self.log(f"签到弹窗广告任务：已看 {done} 次｜每次 {bonuses or '-'} 币"
                 f"｜任务「{(info.get('mainTitle') or '-')[:16]}」状态 "
                 f"{_TASK_STATUS_ZH.get(str(info.get('taskStatus')), info.get('taskStatus') or '-')}"
                 f"（需 App 内看广告）")

    # ── 宝箱 ─────────────────────────────────────────────────
    async def report_treasure(self):
        code, msg, data = await self._post("treasure", {"cityCode": city_code})
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"宝箱状态获取失败（code={code} msg={msg}）")
            self.err += 1
            return
        if not data.get("show"):
            self.log("宝箱：当前不可见（未开放/已下线）")
            return
        status = str(data.get("status") or "-")
        opened = data.get("todayOpenCount")
        max_daily = data.get("maxDailyCount")
        cur_idx = data.get("currentIndex")
        left = data.get("remainingVideoCount")
        normal = data.get("normalBoxReward")
        video = data.get("videoBoxReward")
        nxt = _ts(data.get("nextAvailableTime"))
        box_status = {"AVAILABLE": "可开启", "LOCKED": "已锁定", "FINISH": "已完成"}.get(status, status)
        self.log(f"宝箱：{box_status}｜今日开启 {opened}/{max_daily} 个｜当前第 {cur_idx} 格"
                 f"｜普通 {normal} 币 / 看视频 {video} 币｜剩余视频 {left} 次"
                 + (f"｜下次可开 {nxt}" if nxt != "-" else ""), decorate=False)
        treasures = [t for t in (data.get("treasureList") or []) if isinstance(t, dict)]
        if treasures:
            self.log("   格子：" + " ".join(
                f"#{t.get('index')}({t.get('amount')}币,{t.get('status')})" for t in treasures[:6]))
        if status == "AVAILABLE":
            self.log(f"宝箱可开：普通箱 {normal} 币脚本会直接开（hl_box）；"
                     f"看视频箱 {video} 币需 App 内看广告，纯接口不可达", decorate=False)

    # ── 积分商城（只读，永不自动兑换） ─────────────────────────
    async def report_mall(self):
        code, msg, data = await self._post("mall", {
            "businessId": "N10", "pageSize": 10, "pageNum": 1, "showChannel": 1,
        }, quiet=True)
        if code != _CODE_OK or not isinstance(data, list) or not data:
            self.log(f"积分商城获取失败（code={code} msg={msg}）")
            return
        rows = []
        for it in data:
            if not isinstance(it, dict):
                continue
            try:
                need = float(it.get("pointsDeduction") or 0)
            except Exception:
                need = 0.0
            rows.append((need, it.get("itemName") or it.get("skuName") or "-", it.get("salePrice")))
        rows.sort(key=lambda x: x[0])
        self.log(f"积分商城在售 {len(rows)} 件（脚本不自动兑换，避免消耗币）：")
        for need, name, price in rows[:5]:
            afford = ""
            if isinstance(self.points, (int, float)) and need:
                afford = "｜可兑 ✅" if self.points >= need else f"｜还差 {int(need - self.points)} 币"
            self.log(f"   {name[:18]}｜{int(need)} 币（原价 {price} 元）{afford}")

    # ── 兑换码（写，默认不执行） ───────────────────────────────
    async def redeem(self, code_str):
        code_str = (code_str or "").strip()
        if not code_str:
            return
        code, msg, data = await self._post("exchange",
                                           {"code": code_str, "cityCode": city_code, "adCode": ad_code})
        if code is None:
            self.log(f"兑换码请求异常：{msg}")
            self.err += 1
            return
        if code != _CODE_OK:
            self.log(f"兑换码兑换失败：{msg or '未知错误'}（code={code}）")
            self.warn += 1
            return
        # 注意：兑换接口即使失败也返回 code=0，真实结果在 data.exchangeFailCode / exchangeResultText
        data = data if isinstance(data, dict) else {}
        text = str(data.get("exchangeResultText") or "").strip()
        fail_code = data.get("exchangeFailCode")
        bad_words = ("失败", "无效", "不正确", "正确", "过期", "已使用", "已兑换", "不存在", "库存")
        if fail_code in (0, "0", None) and not any(w in text for w in bad_words):
            extra = json.dumps({k: v for k, v in data.items() if v}, ensure_ascii=False)[:120]
            self.log(f"兑换码兑换成功 ✅ {text or '已兑换'}" + (f"｜{extra}" if extra else ""))
        else:
            self.log(f"兑换码未成功 ⚠️ {text or '未知原因'}"
                     + (f"（exchangeFailCode={fail_code}）" if fail_code not in (None, 0) else ""))
            self.warn += 1

    # ── 宝箱开箱（纯接口唯一能真到账的发奖动作） ────────────────
    async def _query_box(self):
        code, msg, data = await self._post("treasure", {"cityCode": city_code}, quiet=True)
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"宝箱状态获取失败（code={code} msg={msg}）")
            self.err += 1
            return None
        return data

    async def open_boxes(self, max_opens=1):
        """开普通宝箱（boxType=1）：实测 20 分钟 1 个、每天上限 72 个。

        只有这个动作是纯接口能真到账的（实测 rewardAmount 直接进余额）；
        看视频箱（videoBoxReward 33 币）要广告 SDK 回调，不在这里做。
        """
        if not do_box:
            self.log("已按 hl_box=0 关闭开宝箱")
            return 0
        opened = 0
        for idx in range(max(1, max_opens)):
            d = await self._query_box()
            if d is None:
                break
            if not d.get("show"):
                self.log("宝箱：当前不可见，跳过")
                break
            status = str(d.get("status") or "-")
            nxt = d.get("nextAvailableTime")
            today_open = d.get("todayOpenCount")
            max_daily = d.get("maxDailyCount")
            try:
                nxt = int(nxt or 0)
            except Exception:
                nxt = 0
            if nxt > 0 and _now_ms() < nxt:
                left = (_now_ms() - nxt) / 1000.0
                self.log(f"宝箱冷却中（{status}）：今日 {today_open}/{max_daily} 个"
                         f"｜{_ts(nxt)} 后可再开（约 {abs(left) / 60:.0f} 分钟）", decorate=False)
                break
            if status != "AVAILABLE":
                self.log(f"宝箱状态 {status}（今日 {today_open}/{max_daily}），本轮不处理", decorate=False)
                break
            code, msg, data = await self._post("box_open", {"cityCode": city_code, "boxType": 1})
            data = data if isinstance(data, dict) else {}
            if code is None:
                self.log(f"开宝箱请求异常：{msg}")
                self.err += 1
                break
            if code != _CODE_OK:
                if code in _CODE_NO_LOGIN:
                    self.log("开宝箱失败：token 已失效，请重新抓包")
                else:
                    self.log(f"开宝箱未通过：{msg or '未知原因'}（code={code}）")
                self.warn += 1
                break
            if not data.get("success"):
                self.log(f"开宝箱未成功（success=false，{json.dumps(data, ensure_ascii=False)[:100]}）")
                self.warn += 1
                break
            reward = data.get("rewardAmount")
            try:
                self.earned += int(reward or 0)
            except Exception:
                pass
            opened += 1
            nxt2 = data.get("nextAvailableTime")
            self.log(f"开宝箱成功 ✅ +{reward} 币（第 {data.get('currentIndex')} 格）"
                     f"｜今日 {data.get('todayOpenCount')}/{data.get('maxDailyCount')} 个"
                     f"｜累计 {data.get('todayTotalReward')} 币"
                     + (f"｜下次 {_ts(nxt2)}" if nxt2 else ""))
            if idx + 1 < max(1, max_opens):
                try:
                    wait_ms = int(nxt2 or 0) - _now_ms()
                except Exception:
                    wait_ms = 0
                if wait_ms <= 0:
                    await asyncio.sleep(random.uniform(*_SLEEP_STEP))
                    continue
                self.log(f"等待冷却 {wait_ms / 1000:.0f}s 后开下一个（hl_box_opens={max_opens}）")
                await asyncio.sleep(wait_ms / 1000.0 + random.uniform(2, 5))
        return opened

    # ── 主流程 ───────────────────────────────────────────────
    async def run(self, exchange_code=""):
        try:
            if _BOX_ONLY:
                # 高频 cron 入口：只查余额 + 开宝箱，不打其它接口
                await self.fetch_points(quiet=True)
                if self.token_dead:
                    self.log("token 已失效，请重新抓包替换 hl_token")
                    return
                before = self.points
                self.box_opened = await self.open_boxes(box_opens)
                if self.box_opened:
                    await asyncio.sleep(1.5)
                    await self.fetch_points(quiet=True)
                    self.log(f"开箱到账复核：{before} → {self.points} 币（本轮 +{self.earned}）",
                             decorate=False)
                self.log(f"开箱汇总 ▶ {self.box_opened} 个 ｜ +{self.earned} 币"
                         f" ｜ 余额 {self.points if self.points is not None else '-'} 币"
                         f" ｜ ⚠️{self.warn} ❌{self.err}", decorate=False)
                return
            info = await self.fetch_sign_info()
            if self.token_dead:
                return      # 失效原因已在 fetch_sign_info 里打印，避免重复
            if info:
                self.report_sign_info(info)
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

            await self.do_sign(info)
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

            await self.fetch_points()
            if self.signed_now:   # 此刻只做过签到，earned 只含签到那一笔
                self.log(f"签到到账复核：{self.points} 币（本轮签到 +{self.earned}）")
            await asyncio.sleep(random.uniform(*_SLEEP_STEP))

            if do_task:
                await self.report_tasks()
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
                await self.report_banner_reward()
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if do_award:
                await self.report_award_detail()
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if do_treasure:
                await self.report_treasure()
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if do_box:
                self.box_opened = await self.open_boxes(box_opens)
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if show_mall:
                await self.report_mall()
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))
            if exchange_code:
                await self.redeem(exchange_code)
                await asyncio.sleep(random.uniform(*_SLEEP_STEP))

            self.log(f"汇总 ▶ {'✅已签到' if self.signed_now else '⏭️未签到'} ｜ +{self.earned} 币"
                     f" ｜ 宝箱 {self.box_opened} 个"
                     f" ｜ 余额 {self.points if self.points is not None else '-'} 币"
                     f" ｜ ⚠️待条件{self.warn} ｜ ❌异常{self.err}", decorate=False)
        finally:
            await self.client.aclose()


async def main():
    if _CHECK_MODE:
        log.log("只读自检模式（--check）：跳过签到与兑换码，仅打印状态")
    if not hl_tokens or not hl_tokens[0]:
        log.log("未配置 hl_token（token，多账号用 @ 分隔），退出")
        return
    for idx, raw in enumerate(hl_tokens):
        if not raw or not raw.strip():
            continue
        code_str = ""
        if exchange_codes and not _CHECK_MODE:
            if len(exchange_codes) == 1:
                code_str = exchange_codes[0] if idx == 0 else ""
            elif idx < len(exchange_codes):
                code_str = exchange_codes[idx]
        await HelloWelfare(raw).run(code_str)
        if idx < len(hl_tokens) - 1:
            await asyncio.sleep(random.uniform(*_SLEEP_ACCOUNT))


if __name__ == '__main__':
    asyncio.run(main())
    send_notification_message_collection(f"哈啰出行福利 - {datetime.now().strftime('%Y/%m/%d')}")
