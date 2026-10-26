# -*- coding=UTF-8 -*-
# @Project          QL_TimingScript
# @fileName         tclx.py
# @author           Echo
# @EditTime         2026/10/25
# cron: 0 0 9 * * *
# const $ = new Env('同程旅游');
"""
同程旅行 · 福利中心（里程）自动签到 + 任务领取

变量格式：phone#apptoken#device[#sec-token]，多账号用 @ 隔开
  phone     ：账号手机号（仅用于日志区分）
  apptoken  ：App 内打开「领福利」，抓任意一条请求，复制请求头 apptoken
  device    ：同上，复制请求头 device
  sec-token ：App 内点一次「签到」，抓签到那条请求，复制请求头 sec-token
              （约 110 字符、以 * 结尾；不填则只做任务、不签到）

可选开关：
  tc_try_sign=0     关闭自动签到（默认 1 = 开启）
  tc_try_resign=0   关闭自动补签（默认 1 = 有补签卡且当月有漏签就自动补）
  tc_claim_reward=0 关闭连签档位奖励自动领取（默认 1 = 可领就领）
  tc_task_limit=N   每个账号最多做 N 个任务（默认 0 = 全部可做任务）
  tc_exchange=1     额外打印里程商城商品（脚本永不自动兑换，避免消耗里程）

apptoken 失效的表现：接口仍回 2200 但 data 为空 —— 重新抓包替换即可。
"""
import asyncio
import gzip
import hashlib
import json
import os
import random
import struct
import sys
import uuid
from datetime import datetime

import httpx

import log
from get_env import get_env
from sendNotify import send_notification_message_collection

try:  # 面板/终端编码兜底，避免 GBK 控制台炸中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:  # 签到用到加解密库；缺失时自动降级（只做任务）
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric import padding as _pad
    from cryptography.hazmat.primitives.ciphers import Cipher as _Cipher, algorithms as _alg, modes as _modes
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False

tc_cookies = get_env("tc_cookie", "@")
try_sign = str(os.environ.get("tc_try_sign") or "1").strip() in ("1", "true", "True")
try_resign = str(os.environ.get("tc_try_resign") or "1").strip() in ("1", "true", "True")
claim_reward = str(os.environ.get("tc_claim_reward") or "1").strip() in ("1", "true", "True")
task_limit = int(str(os.environ.get("tc_task_limit") or "0").strip() or 0)
show_exchange = str(os.environ.get("tc_exchange") or "0").strip() in ("1", "true", "True")

# ── 常量：换域名/换版本只改这里 ──
_API_HOST = "https://app.17u.cn"
_API_VERSION = "11.5.1"
_USER_AGENT = "okhttp/3.12.13"
_P = {
    "sign_index": "/welfarecenter/index/signIndex",
    "week_sign": "/welfarecenter/index/weekSignInfo",
    "sign_calender": "/welfarecenter/index/signCalender",
    "resign_list": "/welfarecenter/index/resignList",
    "resign_cards": "/welfarecenter/index/resignCards",
    "sign_day_reward": "/welfarecenter/index/signDayReward",
    "static_info": "/welfarecenter/static/staticInfo",
    "feeds": "/welfarecenter/index/feeds",
    "task_list": "/welfarecenter/task/taskList",
    "task_start": "/welfarecenter/task/start",
    "task_start_v2": "/welfarecenter/task/startV2",
    "task_finish": "/welfarecenter/task/finish",
    "task_receive": "/welfarecenter/task/receive",
}

# ── 签到相关常量 ──
_BRIDGE_URL = "https://tcmobileapi.17usoft.com/apprainbowbridge/native/forward"
_SIGN_SERVICE_URL = "https://app.17u.cn/welfarecenter/index/signNew"
# 签名盐与账号 ID 的取法见 README；盐由环境变量注入，不入库。
try:  # 模块级常量也要能读到 .env（get_env 只在变量缺失时才加载 dotenv，不足以覆盖这里）
    from dotenv import find_dotenv as _find_dotenv, load_dotenv as _load_dotenv
    _load_dotenv(_find_dotenv())
except Exception:
    pass
_SALT_REQ = str(os.environ.get("tc_salt_req") or "").strip()
_SALT_DS = str(os.environ.get("tc_salt_ds") or "").strip()
_SALT_OK = bool(_SALT_REQ and _SALT_DS)
_NF_VER = "20111128102912"
_NF_ACC = "c26b007f-c89e-431a-b8cc-493becbdd8a2"
_MAGIC = bytes([132, 103, 128, 130, 121, 132, 121])
_RSA_PUB_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyW+lJJd1Iog0QNCIJnHN8YajxYj4odMBVBaXIEk4ES0VcrhS"
    "jZ3LpzXDQk9mTeh1om54sLrluduOxIxJU6G3g53UnKEl7Ylce9KrAJUxkbSqx+7GlOxUsXdBQQpS6Zn9AlSGMQF+GFsn"
    "6T7g7wlnyLWuwSCfmrNuZyYcVAyTdKEbCKWBZejKCDDCOZR5tYkuS2aYjT4CrED6gmGfyCSP9eBNgq2VnKR6MgT+fLv5R6m"
    "KPaUsbJHHx1OvYhXl5fxA2yNQSZMtfhoNEOtivZ8B6f971viU4d9fjSDl+OhRFMpp5ZjS1PzRwD49YHLRnGSU8udEivQzJ63"
    "O64pQB/W/qQIDAQAB"
)
# 客户端信息模板（与账号无关，可长期复用）
_CLIENT_INFO = {
    "versionType": "android",
    "area": "||",
    "clientId": "5dbf6cad8d96646e5b7a786671d447bd2060b3b78011",
    "hwPreInstall": "",
    "isGuest": "false",
    "deviceId": "029ee7158e7832d4",
    "versionNumber": _API_VERSION,
    "touristId": "2e7fe10abe5d4826afb0f90c46789070",
    "manufacturer": "vivo",
    "extend": ("4^15,5^V2364A,isGuest^false,6^-1,os_v^35,app_v^11.5.1,devicetoken^,"
               "tencentDeviceToken^random_tencent_device_token,memberidnew^,systemCode^tc,"
               "clientIp^127.0.0.1,refId^16818681,networkType^wifi,"
               "device^029ee7158e7832d4|x86_64|1092*1920*273|V2364A|unknown,"
               "pushInfo^84014434f042723be1b3e1036a477917"),
    "systemCode": "tc",
    "clientIp": "127.0.0.1",
    "refId": "16818681",
    "networkType": "wifi",
    "device": "029ee7158e7832d4|x86_64|1092*1920*273|V2364A|unknown",
    "pushInfo": "84014434f042723be1b3e1036a477917",
}

_pub_key = None
if _HAS_CRYPTO:
    try:
        _pem = ("-----BEGIN PUBLIC KEY-----\n"
                + "\n".join(_RSA_PUB_B64[i:i + 64] for i in range(0, len(_RSA_PUB_B64), 64))
                + "\n-----END PUBLIC KEY-----\n")
        _pub_key = _ser.load_pem_public_key(_pem.encode())
    except Exception:
        _pub_key = None

# 任务状态语义（实测）
_ST_ACTIONABLE = 1            # 可做
_ST_RECEIVED = 3              # 已领取
_BTN_GO_FINISH = "GO_FINISH"
_BTN_RECEIVED = "RECEIVED"

_SLEEP_TASK = (2, 4)          # 任务之间随机间隔（秒）
_SLEEP_ACCOUNT = (3, 6)       # 账号之间随机间隔（秒）
_MAX_RETRY = 2                # finish / receive 单步重试次数
_CODE_OK = 2200
_SIGN_CODES = {2200: "成功", 50001: "今日已签到", 5000: "sec-token 失效",
               5001: "参数错误", 5004: "点太快被限流", 4010: "今日不是签到日",
               50005: "超出5个自然日窗口", 50006: "没有可用补签卡"}


def _digital_sign(service_name, req_time):
    """请求签名串"""
    arr = sorted([f"Version={_NF_VER}", f"AccountID={_NF_ACC}",
                  f"ServiceName={service_name}", f"ReqTime={req_time}"])
    return hashlib.md5(("&".join(arr) + _SALT_DS).encode()).hexdigest()


def _sv3_frame(plain: bytes):
    """把明文打包成请求信封"""
    comp = gzip.compress(plain, 6, mtime=0)
    key = uuid.uuid4().hex[:8] + "-" + uuid.uuid4().hex[:7]      # 16 字符，形似 UUID 片段
    kb = key.encode()
    pad = 16 - len(comp) % 16
    enc = _Cipher(_alg.AES(kb), _modes.ECB()).encryptor()
    ct = enc.update(comp + bytes([pad]) * pad) + enc.finalize()
    rk = _pub_key.encrypt(kb, _pad.PKCS1v15())
    return (_MAGIC + b"\x01" + hashlib.md5(rk + ct).digest() + struct.pack(">I", len(rk))
            + rk + struct.pack(">I", len(ct)) + ct)


def _build_sign_plain(day, sign_type=1):
    """构造签到/补签请求体（sign_type：1=签到 2=补签）"""
    req_time = str(int(datetime.now().timestamp() * 1000))
    body = {
        "removeClient": "0",
        "serviceUrl": _SIGN_SERVICE_URL,
        "envSync": "0",
        "clientInfo": _CLIENT_INFO,
        "extendParam": {"type": sign_type, "day": day},
    }
    header = {"accountID": _NF_ACC, "digitalSign": _digital_sign("forward", req_time),
              "reqTime": req_time, "serviceName": "forward", "version": _NF_VER}
    return json.dumps({"request": {"body": body, "header": header}},
                      separators=(",", ":"), ensure_ascii=False).encode()


def _decode_reply(raw, denc):
    """解析响应内容"""
    if not raw:
        return {}
    if raw[:1] in (b"{", b"["):
        return json.loads(raw.decode("utf-8", "replace"))
    sig_len = struct.unpack(">I", raw[24:28])[0]
    p = 28 + sig_len
    ct_len = struct.unpack(">I", raw[p:p + 4])[0]
    blob = raw[p + 4:p + 4 + ct_len]
    if (denc or "gzip").lower() == "br":
        import brotli  # 仅当服务端坚持 brotli 时才需要
        plain = brotli.decompress(blob)
    else:
        plain = gzip.decompress(blob)
    return json.loads(plain.decode("utf-8", "replace"))


class Tclx:
    def __init__(self, cookie):
        self.client = httpx.AsyncClient(base_url=_API_HOST, verify=False, timeout=60, trust_env=False)
        parts = cookie.split("#")
        _ph = parts[0] if parts else ""
        self.phone = (_ph[:3] + "****" + _ph[-4:]) if len(_ph) >= 7 else _ph  # 日志脱敏：前3+****+后4
        self.apptoken = parts[1] if len(parts) > 1 else ""
        self.device = parts[2] if len(parts) > 2 else ""
        self.sec_token = parts[3].strip() if len(parts) > 3 else ""
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "channel": "1",
            "os-type": "2",
            "apptoken": self.apptoken,
            "device": self.device,
            "content-type": "application/json;charset=utf-8",
            "user-agent": _USER_AGENT,
            "accept-encoding": "gzip",
        }
        self.earned_mileage = 0   # 本轮到账里程
        self.done_tasks = 0
        self.warn_tasks = 0
        self.err_tasks = 0
        self.signed_today = False
        self.resigned_days = 0   # 本轮自动补签天数
        self.claimed_reward = 0  # 本轮领到的连签档位奖励里程

    # ── 基础设施 ─────────────────────────────────────────────
    def log(self, msg):
        log.log(log.decorate(f"用户【{self.phone}】 - {msg}"))

    def _url(self, key):
        path = _P[key]
        # 只有这三个接口带 version（服务端按版本返回不同分区数据，必须与 App 一致）
        if path.endswith(("signIndex", "weekSignInfo", "taskList")):
            return f"{path}?version={_API_VERSION}"
        return path

    async def _post(self, key, body=None, quiet=False):
        """统一请求层：返回 (code, message, data)；网络/解析异常返回 (None, 原因, None)"""
        url = self._url(key)
        try:
            resp = await self.client.post(url, headers=self.headers, json=body if body is not None else {})
            try:
                payload = resp.json()
            except Exception:
                text = " ".join(resp.text.split())[:100]
                if not quiet:
                    self.log(f"接口 {key} 返回非 JSON（HTTP {resp.status_code}）：{text}")
                return None, f"非JSON:{text}", None
            return payload.get("code"), payload.get("message"), payload.get("data")
        except Exception as e:
            if not quiet:
                self.log(f"接口 {key} 请求异常：{type(e).__name__}: {e}")
            return None, type(e).__name__, None

    @staticmethod
    def _fmt_balance(bal):
        if not isinstance(bal, dict):
            return "-"
        return f"{bal.get('mileage', '-')}里程/{bal.get('money', '-')}元"

    # ── 状态查询 ─────────────────────────────────────────────
    async def fetch_index(self):
        code, msg, data = await self._post("sign_index")
        if code != _CODE_OK or not isinstance(data, dict):
            self.log(f"签到页数据获取失败（code={code} msg={msg}），apptoken 可能已失效，请重新抓包")
            return None
        if not data.get("mileageBalance") and data.get("todaySign") is None:
            self.log("签到页返回空数据（apptoken 无效或已过期），请重新抓包 apptoken/device")
            return None
        return data

    def report_index(self, d):
        bal = self._fmt_balance(d.get("mileageBalance"))
        self.signed_today = bool(d.get("todaySign"))
        signed = "已签到" if self.signed_today else "未签到"
        today_mileage = (d.get("mileageBalance") or {}).get("todayMileage", 0)
        self.log(f"{signed}｜本月签到{d.get('cycleSighNum')}天｜连续{d.get('continuousHistory')}天｜"
                 f"今日累计{today_mileage}里程｜余额{bal}")
        stage = d.get("signStageInfo") or {}
        if stage.get("stageText"):
            self.log(f"连签进度：{stage.get('stageText')}")
        special = (d.get("specialContinuousInfo") or {}).get("list") or []
        claimable = [s.get("dateNo") for s in special if s.get("todayCanReceive")]
        if claimable:
            self.log(f"有{len(claimable)}档连签奖励可领（{claimable}天档），本轮自动领取")

    async def report_resign(self):
        """只读汇报补签状态（补签动作本身在 do_resign）"""
        code, _, cards = await self._post("resign_cards", quiet=True)
        usable = []
        if code == _CODE_OK and isinstance(cards, list):
            usable = [c.get("name") for c in cards if not c.get("used")]
        code2, _, days = await self._post("resign_list", quiet=True)
        can, missed = [], []
        if code2 == _CODE_OK and isinstance(days, list):
            can = sorted(x.get("day") for x in days
                         if x.get("canResign") and not x.get("sign") and x.get("day"))
            missed = sorted(x.get("day") for x in days
                            if not x.get("sign") and not x.get("today") and x.get("day"))
        if self.resigned_days:
            self.log(f"本轮已自动补签 {self.resigned_days} 天")
        if can and usable:
            self.log(f"仍可补签 {len(can)} 天（{can[0]} ~ {can[-1]}），补签卡剩 {len(usable)} 张（{usable}）")
        elif can:
            self.log(f"仍可补签 {len(can)} 天（{can[0]} ~ {can[-1]}），但没有可用补签卡")
        elif missed:
            self.log(f"漏签 {len(missed)} 天（{missed[0]} ~ {missed[-1]}），补签卡已用完，无法补签")
        elif usable:
            self.log(f"本月无漏签，补签卡 {len(usable)} 张未用（{usable}）")
        else:
            self.log("本月无漏签，也没有可用补签卡")

    async def report_exchange(self):
        code, _, feeds = await self._post("feeds", quiet=True)
        if code != _CODE_OK or not isinstance(feeds, list):
            return
        priced = sorted([f for f in feeds if isinstance(f.get("mileage"), int)], key=lambda x: x["mileage"])
        lowest = priced[0].get("mileage") if priced else "-"
        self.log(f"里程商城在售 {len(feeds)} 件，最低 {lowest} 里程起（脚本不自动兑换，避免消耗里程）")

    # ── 签到 / 补签 ───────────────────────────────────────────
    async def _submit_sign(self, day, sign_type=1):
        """提交签到或补签，返回 (code:int, body:dict)；网络异常返回 (None, 原因)"""
        plain = _build_sign_plain(day, sign_type)
        headers = {
            "sv": "3", "aenc": "gzip", "denc": "gzip", "secver": "4",
            "sec-token": self.sec_token,
            "reqdata": hashlib.md5(plain + _SALT_REQ.encode()).hexdigest(),
            "content-type": "application/json;charset=utf-8",
            "user-agent": _USER_AGENT, "accept-encoding": "gzip",
        }
        try:
            async with httpx.AsyncClient(verify=False, timeout=30, trust_env=False) as c:
                resp = await c.post(_BRIDGE_URL, headers=headers, content=_sv3_frame(plain))
            payload = _decode_reply(resp.content, resp.headers.get("denc"))
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
        body = ((payload.get("response") or {}).get("body") or {})
        try:
            return int(body.get("code")), body
        except Exception:
            return -1, body

    async def do_resign(self):
        """自动补签：有补签卡且服务端标记可补的日子就补，返回本轮补签天数"""
        if not try_resign:
            return 0
        if not _HAS_CRYPTO or _pub_key is None:
            self.log("⚠️ 缺少 cryptography 库，跳过自动补签")
            self.warn_tasks += 1
            return 0
        if not self.sec_token:
            self.log("⚠️ 未配置 sec-token，跳过自动补签")
            self.warn_tasks += 1
            return 0
        if not _SALT_OK:
            self.log("⚠️ 未配置 tc_salt_req / tc_salt_ds，跳过自动补签（见 README 取值说明）")
            self.warn_tasks += 1
            return 0

        code, _, cards = await self._post("resign_cards", quiet=True)
        avail, names = 0, []
        if code == _CODE_OK and isinstance(cards, list):
            for c in cards:
                if not c.get("used"):
                    avail += 1
                    names.append(c.get("name"))
        code2, _, days = await self._post("resign_list", quiet=True)
        todo = []
        if code2 == _CODE_OK and isinstance(days, list):
            todo = [x.get("day") for x in days if x.get("canResign") and not x.get("sign")]
        # 从晚到早补：紧邻已有连签的那天优先，才能把连续天数往上推
        todo = sorted((d for d in todo if d), reverse=True)
        if not todo:
            return 0
        if avail <= 0:
            self.log(f"可补签 {len(todo)} 天（{todo[0]} ~ {todo[-1]}），但补签卡已用完，跳过")
            return 0

        self.log(f"可补签 {len(todo)} 天（{todo[0]} ~ {todo[-1]}），补签卡 {avail} 张（{names}），开始自动补签")
        done = 0
        for day in todo:
            if done >= avail:
                self.log(f"补签卡已用尽，剩余 {len(todo) - done} 天未补")
                break
            code_i, body = await self._submit_sign(day, 2)
            if code_i is None:
                self.log(f"❌ 补签 {day} 请求异常：{body}")
                self.err_tasks += 1
                break
            if code_i == 5004:      # 限流：等一会重试一次
                await asyncio.sleep(5)
                code_i, body = await self._submit_sign(day, 2)
            if code_i == _CODE_OK:
                data = body.get("data") or {}
                got = data.get("total") or data.get("common") or 0
                try:
                    self.earned_mileage += int(got)
                except Exception:
                    pass
                done += 1
                self.log(f"补签 {day} 成功 ✅ +{got}里程（连续{data.get('continuousCycle', '-')}天）")
            elif code_i == 50005:
                self.log(f"⏭️ {day} 超出 5 个自然日窗口，更早的日期不再尝试")
                break
            elif code_i == 50006:
                self.log("⏭️ 补签卡已用完")
                break
            else:
                desc = _SIGN_CODES.get(code_i, (body.get("message") or str(body))[:60])
                self.log(f"❌ 补签 {day} 失败：{desc}（code={code_i}）")
                self.err_tasks += 1
            await asyncio.sleep(random.uniform(*_SLEEP_TASK))
        return done

    async def claim_stage_reward(self):
        """领取连签档位奖励（1/3/7/16/30 日档，服务端判断当下哪些可领），返回到账里程"""
        if not claim_reward:
            return 0
        code, msg, data = await self._post("sign_day_reward", quiet=True)
        if code == _CODE_OK:
            got = 0
            if isinstance(data, dict):
                for k in ("total", "common", "special", "mileage", "maxMileage"):
                    try:
                        got = max(got, int(data.get(k) or 0))
                    except Exception:
                        pass
            try:
                self.earned_mileage += int(got)
            except Exception:
                pass
            self.claimed_reward = got
            detail = json.dumps(data, ensure_ascii=False)[:120] if data else ""
            self.log(f"连签档位奖励已领取 ✅ +{got}里程" + (f"｜{detail}" if detail else ""))
            return got
        if code == 4010:
            return 0          # 常态：当下没有可领的档位
        self.log(f"⚠️ 连签档位奖励领取未通过：{msg}（code={code}）")
        self.warn_tasks += 1
        return 0

    async def do_sign(self):
        """返回 True=本次签到到账；False=未签到（已签/缺条件/失败）"""
        if not try_sign:
            if not self.signed_today:
                self.log("已按 tc_try_sign=0 关闭自动签到")
            return False
        if self.signed_today:
            return False
        if not _HAS_CRYPTO or _pub_key is None:
            self.log("⚠️ 缺少 cryptography 库，跳过自动签到（pip install cryptography）")
            self.warn_tasks += 1
            return False
        if not self.sec_token:
            self.log("⚠️ 未配置 sec-token，跳过自动签到（变量写 phone#apptoken#device#sec-token）")
            self.warn_tasks += 1
            return False
        if not _SALT_OK:
            self.log("⚠️ 未配置 tc_salt_req / tc_salt_ds，跳过自动签到（见 README 取值说明）")
            self.warn_tasks += 1
            return False

        day = datetime.now().strftime("%Y-%m-%d")
        code, body = await self._submit_sign(day, 1)
        if code is None:
            self.log(f"签到请求异常：{body}")
            self.err_tasks += 1
            return False
        try:
            code_i = int(code)
        except Exception:
            code_i = -1
        desc = _SIGN_CODES.get(code_i, str(body.get("message") or code))

        if code_i == _CODE_OK:
            data = body.get("data") or {}
            got = data.get("total") or data.get("common") or 0
            try:
                self.earned_mileage += int(got)
            except Exception:
                pass
            self.signed_today = True
            self.log(f"签到成功 ✅ +{got}里程（连续{data.get('continuousCycle', '-')}天）")
            return True
        if code_i == 50001:
            self.signed_today = True
            self.log("今日已签到（服务端返回 HAS SIGN）")
            return False
        if code_i == 5000:
            self.log("❌ 签到失败：sec-token 失效，请在 App 内重新抓一次 sec-token")
            self.err_tasks += 1
            return False
        self.log(f"❌ 签到失败：{desc}（code={code}）")
        self.err_tasks += 1
        return False

    # ── 任务链路 ─────────────────────────────────────────────
    async def fetch_tasks(self):
        code, msg, data = await self._post("task_list")
        if code != _CODE_OK or not isinstance(data, list):
            self.log(f"任务列表获取失败（code={code} msg={msg}）")
            return []
        return data

    @staticmethod
    def pick_actionable(tasks):
        ready, resumable, received, other = [], [], [], []
        for t in tasks:
            if t.get("state") == _ST_RECEIVED or t.get("buttonEnum") == _BTN_RECEIVED:
                received.append(t)
            elif t.get("state") == _ST_ACTIONABLE and t.get("buttonEnum") == _BTN_GO_FINISH:
                (resumable if t.get("started") else ready).append(t)
            else:
                other.append(t)
        ready.sort(key=lambda t: (t.get("browserTime") or 0))
        return ready, resumable, received, other

    async def _start(self, task):
        """开始任务，返回任务 id；startV2 返回 dict，start 返回 int"""
        for key in ("task_start_v2", "task_start"):
            code, msg, data = await self._post(key, {"taskCode": task.get("taskCode")})
            if code == _CODE_OK and data:
                tid = data.get("id") if isinstance(data, dict) else data
                if tid:
                    return tid
            self.log(f"任务【{task.get('title')}】{key} 未成功（code={code} msg={msg}），换下一个接口")
            await asyncio.sleep(1.2)
        return None

    async def _step(self, key, body, title, what):
        for attempt in range(_MAX_RETRY):
            code, msg, data = await self._post(key, body)
            if code == _CODE_OK:
                return True, data
            if attempt < _MAX_RETRY - 1:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            self.log(f"任务【{title}】{what}失败（code={code} msg={msg}）")
        return False, None

    async def _claim(self, tid, title):
        """finish + receive 的公共尾段，成功返回 True"""
        ok, _ = await self._step("task_finish", {"id": tid}, title, "完成")
        if not ok:
            return False
        ok2, data = await self._step("task_receive", {"id": tid}, title, "领取")
        if not ok2:
            return False
        got = (data or {}).get("mileage") if isinstance(data, dict) else None
        if got:
            self.earned_mileage += int(got)
        self.done_tasks += 1
        self.log(f"任务【{title}】✅ 完成并领取 {got or 0} 里程")
        return True

    async def do_task(self, task):
        title = task.get("title")
        wait_s = int(task.get("browserTime") or 0)
        tid = await self._start(task)
        if not tid:
            self.warn_tasks += 1
            return
        if wait_s > 0:  # browserTime=0 的任务（奖励往往最高）立即完成
            await asyncio.sleep(wait_s + random.uniform(1.0, 2.5))
        if not await self._claim(tid, title):
            self.err_tasks += 1

    async def resume_task(self, task):
        """上次中断留下的 started 任务：重新 start 拿回 id 再走 finish/receive"""
        title = task.get("title")
        tid = await self._start(task)
        if not tid:
            self.warn_tasks += 1
            self.log(f"任务【{title}】处于已开始状态但拿不回 id，跳过（次日重置）")
            return
        if not await self._claim(tid, title):
            self.warn_tasks += 1

    # ── 主流程 ───────────────────────────────────────────────
    async def run(self):
        d = await self.fetch_index()
        if d is None:
            await self.client.aclose()
            return
        self.report_index(d)

        if not self.signed_today:
            if await self.do_sign():
                await asyncio.sleep(1.5)
                fresh = await self.fetch_index()
                if fresh:
                    self.report_index(fresh)
        await asyncio.sleep(1.5)

        self.resigned_days = await self.do_resign()
        if self.resigned_days:
            await asyncio.sleep(1.5)
            after_resign = await self.fetch_index()
            if after_resign:
                self.report_index(after_resign)
        await asyncio.sleep(1.0)

        # 签到/补签可能刚好凑满档位，这里把可领的连签奖励领掉
        if await self.claim_stage_reward():
            await asyncio.sleep(1.5)
            after_reward = await self.fetch_index()
            if after_reward:
                self.report_index(after_reward)
            await asyncio.sleep(1.5)

        tasks = await self.fetch_tasks()
        if tasks:
            ready, resumable, received, other = self.pick_actionable(tasks)
            self.log(f"任务共{len(tasks)}个：可做{len(ready)}｜中断可补{len(resumable)}｜已领{len(received)}｜其他{len(other)}")
            for t in resumable:
                await self.resume_task(t)
                await asyncio.sleep(random.uniform(*_SLEEP_TASK))
            todo = ready if task_limit <= 0 else ready[:task_limit]
            for idx, t in enumerate(todo, 1):
                self.log(f"[{idx}/{len(todo)}] 开始任务【{t.get('title')}】"
                         f"(浏览{int(t.get('browserTime') or 0)}秒，奖励{t.get('mileage')}里程)")
                await self.do_task(t)
                if idx < len(todo):
                    await asyncio.sleep(random.uniform(*_SLEEP_TASK))
            if task_limit > 0 and len(ready) > len(todo):
                self.log(f"受 tc_task_limit={task_limit} 限制，剩余{len(ready) - len(todo)}个可做任务未执行")
            if received:
                self.log(f"今日已领取任务{len(received)}个")

        await asyncio.sleep(1.5)
        final = await self.fetch_index()
        if final:
            self.report_index(final)
        await self.report_resign()
        if show_exchange:
            await self.report_exchange()
        self.log(f"汇总 ▶ ✅完成任务{self.done_tasks} ｜ +{self.earned_mileage}里程 ｜ "
                 f"⚠️待条件{self.warn_tasks} ｜ ❌错误{self.err_tasks}")
        await self.client.aclose()


async def main():
    if not tc_cookies or not tc_cookies[0]:
        log.log("未配置 tc_cookie（手机号#apptoken#device[#sec-token]），退出")
        return
    for idx, cookie in enumerate(tc_cookies):
        if not cookie or cookie.count("#") < 2:
            log.log(f"第{idx + 1}个账号格式错误（应 phone#apptoken#device[#sec-token]），跳过")
            continue
        await Tclx(cookie).run()
        if idx < len(tc_cookies) - 1:
            await asyncio.sleep(random.uniform(*_SLEEP_ACCOUNT))


if __name__ == '__main__':
    asyncio.run(main())
    send_notification_message_collection(f"同程旅行福利 - {datetime.now().strftime('%Y/%m/%d')}")
