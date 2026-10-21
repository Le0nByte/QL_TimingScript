# -*- coding=UTF-8 -*-
# @Project          QL_TimingScript
# @fileName         中国移动云盘.py
# @author           Echo
# @EditTime         2026/10/15
# cron: 0 0 8,10,12,15 * * *   （每 4 小时一轮：云盘日/限量活动都在整点开）
# const $ = new Env('中国移动云盘');
"""
中国移动云盘（云朵中心 / 云盘专属 AI豆）签到脚本

─────────────────────────── 环境变量 ───────────────────────────
  ydyp_ck           必需   账号凭据，多账号用 @ 分隔
  ydyp_ua           必需   自己手机的 User-Agent（抓包复制完整整串）；不填直接退出
  ydyp_device_id    可选   全局默认设备号（UUID）；ydyp_ck 第 4 段可单独覆盖它
  ydyp_device_token 强烈建议 真机设备令牌（约 88 字符 base64）；不填则签到/领记录豆会回 614
  ydyp_upload_fill  可选   是否补足「当月上传满 100 个」，默认 0（该通道实测无效，见说明 5）

  ydyp_ck 格式（# 分隔，后两段可省）：
      <authorization>#<手机号>#<jwtToken>#<deviceId>
  例：ydyp_ck=Basic bW9iaWxl....xxx#13800000000
  多账号：ydyp_ck=账号1@账号2@账号3

──────────────────── 这些值怎么拿到（手机抓一次包就够）────────────────────
  抓包工具：手机装 Reqable（推荐）或 Stream；也可用本仓库附带的 PC 抓取工具
            「移动云盘抓authorization.zip」（解压后右键管理员运行，自动抓出 authorization）。

  authorization   抓「中国移动云盘」App 的任意一条请求，看请求头里的 authorization
                  （形如 Basic bW9iaWxl...，是一整串）；云朵中心 H5（浏览器打开
                  m.mcloud.139.com/portal/newsignin/ 并登录）的请求头里也有同一个值。
                  ⚠ 它内嵌 13 位过期时间戳，有效期约 30 天；接口返回「05050006 暂无权限！」
                  就是过期了，重新抓一次替换即可。
  手机号          登录用的移动手机号（用于脱敏展示 + 派生默认设备号，脚本不会外传）。
  jwtToken        （可省）抓 tyrzLogin 响应里的 result.token（形如 eyJhbGci....xx.yy）。
                  填了会先用它、失效自动回退到 authorization 重换；不填每轮现换也行。
  deviceId        （可省）抓 tyrzLogin 请求体里的 deviceId（UUID 形式）。
                  不填则由脚本按账号派生：每个账号稳定、各人互不相同，无需手填。
  ydyp_device_token （强烈建议）抓任意一条 App 领取请求，复制请求体里的 deviceId 整串
                  （形如 AbCdEf012345...Q==，约 88 字符 base64）。抓法：抓包工具里搜
                  receiveV3（或搜你待领记录的那串数字 ID），看这条 POST 的请求体：
                    {"client":"app","cloudId":...,"cloudType":0,"deviceId":"<就是这一串>"}
                  它由 App 内置 SMSdk 依设备指纹生成、本机固定，抓一次可长期用。
  ydyp_ua         （必需）抓包里复制任意请求的完整 User-Agent 整串（含结尾的 MCloudApp/版本）。
                  必须各填各的：它要和自己的设备令牌配套，UA 与设备对不上本身就是风险信号，
                  多人共用一串也等于「同一台设备」。不填脚本直接退出。

──────────────────────────── 风控建议 ────────────────────────────
  · 一账号一设备指纹：ydyp_ua + 设备令牌各填各的；deviceId 默认值已按账号派生，各人天然不同。
  · 不要把 ck 借给别人用（同一 ck 多 IP 登录本身就会触发风控）；也不要多机同跑一个账号。

覆盖的领豆入口（均以 App/H5 接口面为准）：
  A. 云朵中心 sign_in_3
     1) 每日签到 startSignIn
     2) 签到翻倍 multiple（每月一次）
     3) 任务点击 task/click + 逐条领奖 receiveTask（含 FINISH 已完成的补领）
     4) 记录豆领取 receiveV3（含 cloudType=1 的膨胀/月度奖励 receiveTaskExpansion）
     5) 上传文件任务（106 手动上传 / 522 当月上传满100个）走 OSE 新通道
  B. 老 market 接口（实测存活）：连续备份奖励 backupgift（读接口已切新域）、通知任务 msgPushOn
  C. 独立活动巡检 activity_patrol：直播间小红花、年度回馈开福袋、TokenPK 阶段奖励、云盘日盲盒/礼品、邮箱短信
     —— 每个活动先查状态，只在「可领/有库存/有次数」时才动作，无库存/已领/已结束自动跳过。

说明：
  1) 凭据三种形态脚本都认：Basic authorization（推荐）/ ssoToken / 裸 jwtToken（eyJhbGci....xx.yy）。
     直填 jwtToken 时脚本先探活再使用，失效会自动回退到 authorization 重换，不会一路空跑。
  2) 设备标识有两套，别混淆：
     a) 明文设备号（UUID）：走头部 deviceId / 登录请求体。优先级：ydyp_ck 第 4 段 >
        ydyp_device_id > 按账号派生（一个账号固定一个、各人互不相同）。一般不用手填。
     b) 加密设备令牌：签到 startSignIn（URL 参数）、领记录豆 receiveV3（请求体）、兑换
        exchangeV3 这几个接口会带上它，服务端据此确认「设备已登记」。不填一律回
        614「活动太火爆啦，锁定失败」——实测同账号同请求，仅补上这串即领取成功。
        抓法见上方 ydyp_device_token。
  3) 依赖：httpx、python-dotenv；同目录需 log.py / get_env.py / sendNotify.py（本仓库自带，
     一起下载即可）。pip 装依赖：pip install httpx python-dotenv
  4) 兑换接口带滑块验证（getSlidePuzzle + puzzleOffset），脚本无法完成。
  5) 「当月上传文件满100个」的补足功能**实测无效**：OSE 活动上传（能完成「手动上传一个文件」）
     不计入该任务的 `process` 计数（上传 59 个后仍为 41），家庭云 V3 通道又要求账号已开通家庭云
     （`queryFamilyCloud` 返回空列表）。故该开关默认关闭（`ydyp_upload_fill=1` 才启用），
     522 只能靠真实上传/PC 客户端自然累计。
  6) 已下线的入口已移除（2026-10-15 实测）：
     · 老接口抽奖 playoffic：`/market/playoffic/draw` 返回 602 活动已结束（POST 回 502 请求方法异常，
       说明调用形态没错、是活动停了；drawInfo 仍回 surplusNumber=50 属残留额度，别被它骗了）。
     · 微信公众号签到 followSignInfo：602 活动已结束。
     · 中秋灯谜（窗口 2026-10-07 截止）、酷狗联合活动（602 已结束）本就未接入。
  7) 云盘日（会员日）每月 16 号 10:00 开窗，10/12/15 点各放一波限量礼品（库存看 totalCount/hasStock）：
     · 未开窗时 /mcloudday/gift/* 与 blindbox/* 整体 404（活动按窗口上下线）→ 先查 online 再动作，
       未开窗只打一行提示，不再逐条试探 20+ 礼品刷屏（2026-10-15 旧版刷满一屏 404 就是这么来的）。
     · **动作接口比读接口多两道门槛**：① 必须带 JSON body（抽奖体 {"client":"1"}，App 端取值）
       ② 必须带 isDeviceId 设备头；缺任一项网关直接回 nginx 404（不是业务码）。2026-10-16 实测对照：
       POST {} → 404；POST {"client":"1"} + 设备头 → 200 code:0 且 lotteryCount 同步 -1。
     · 惊喜助力礼按邀请人数分档（inviteCount 1/4/10），未达门槛回 40001「未满足惊喜奖品条件」。
     · 礼品领取**必须带 smsCode 字段**（值可空码 MD5("")）：实测只发 {prizeId} 会回 nginx 404；
       需短信验证码的奖品回 10004「验证码错误」，脚本按 msg 提示到 App 内手领。
  8) 老 market 域 caiyun.feixin.10086.cn 会间歇性 DNS 解析失败（[Errno -3] Try again：实测同一轮
     5 个请求里 3 个失败、2 个成功，同日重试即恢复）。脚本对「请求未发出」的连接类错误自动重试
     3 次（1s/2s 退避），读取接口失败再回退一次；读超时不重试，避免抽奖类接口被重复提交。
  9) 本轮核对过、但**没有加入**的现役活动（附原因，省得下次重复调研）：
     · 13 周年升级礼 National_v13gift（2026-04-27~12-31）：领奖要短信验证码（getPrize?smsCode=）。
     · 玩 AI National_playAI（~2026-12-31）：抽奖次数靠完成 H5 里的 AI 玩法任务（抠图/漫画/转录），
       纯接口做不了。
     · 使用时长反馈 National_sessionDuration（2026-09-30~10-31）：acceptPrize 需短信验证码。
     · 红骑兵 2026 National_RedCavalry2026（~12-31）：打卡/答题接口可用，但活动本身不发 AI豆（只有勋章）。
     · TokenPK 任务 /ycloud/tokenpk/task/list：该路由未部署（404），只能用阶段奖励（脚本已覆盖）。
     · 集卡/美图抽奖/备图/酷狗/转赠红包等：接口 404 或活动窗口已结束。
  10) **任务奖励是服务端「异步记账」的**（issue #50 根因）：任务 state 变 FINISH 是即时的，但奖励要过一会儿
     才以「记录豆」（infoV3.receiveList）形式出现。同一账号实测：08:00 传完文件当次 receiveTask 全 0、
     记录豆为空（结束只 +3 = 签到），10:00 再跑才看到「待领 6」并在记录豆通道领到 +6 ——
     用户感知就是「每天第一遍领不到、要跑两遍」。脚本因此在任务段结束后刷新状态并有限轮询补领
     （settle_task_rewards，总预算见 WAIT_SETTLE）；记账超出预算也不影响正确性，下一轮定时任务会补领。
"""
import asyncio
import hashlib
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime

import httpx

import log
from get_env import get_env
from sendNotify import send_notification_message_collection

try:
    from dotenv import load_dotenv, find_dotenv
    
    load_dotenv(find_dotenv())
except Exception:
    pass

UA = (os.environ.get("ydyp_ua") or "").strip()  # 必需：自己手机的完整 User-Agent（抓包可得，与设备令牌配套）
DEVICE_ID_ENV = (os.environ.get("ydyp_device_id") or "").strip()  # 全局默认设备号（被 ydyp_ck 第 4 段覆盖）
DEVICE_TOKEN = (os.environ.get("ydyp_device_token") or "").strip()

H5 = "https://m.mcloud.139.com"  # 新接口平台（与云朵中心 H5 同源）
YCLOUD = H5 + "/ycloud"
MARKET = "https://caiyun.feixin.10086.cn"  # 老 market 平台（抽奖/备份/通知仍在此）
SIGN_SALT = "sekaMdYYLIZfbCfm"  # x-signature 盐值（前端 char-code 混淆还原）
SOURCE_ID = "001005"  # 云盘渠道号：换 ssoToken 与登录必须一致
CLIENT_VERSION = "13.2.2"
APP_VERSION = CLIENT_VERSION + ".0"  # 头部 appVersion（真机同款，跟随上一行）
MARKET_NAME = "sign_in_3"
GIFT_SMS_CODE = "d41d8cd98f00b204e9800998ecf8427e"  # MD5("")：云盘日领取的 smsCode 字段必须存在（值可空码，需短信的奖品会回 10004 验证码错误）  # 云朵中心活动标识
UPLOAD_MARKET = "National_redCavalry"  # OSE 上传通道唯一可用活动（实测 8 个候选仅此通过）
UPLOAD_FILL = os.environ.get("ydyp_upload_fill", "0") == "1"  # 522 补足：**实测无效**（OSE 上传不计入 522 计数），默认关闭
# 任务奖励异步记账的补领轮询间隔（秒，累加即总预算）：只在前段真的点过任务时才等，最多 70s
WAIT_SETTLE = (10, 20, 30, 45, 60)  # ≈165s 总预算


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _sign_headers(params_str: str = "") -> dict:
    """云朵中心网关签名头：x-signature = MD5(salt + rid + ts + nonce + params串 + salt)"""
    rid, nonce = str(uuid.uuid4()), str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    return {"x-request-id": rid, "x-timestamp": ts, "x-nonce": nonce,
            "x-signature": _md5(SIGN_SALT + rid + ts + nonce + params_str + SIGN_SALT)}


def _require_ua() -> bool:
    """UA 是必需项：必须填自己手机的 UA（与设备令牌配套），缺失直接退出。"""
    if UA:
        return True
    log.log("❌ 未配置环境变量 ydyp_ua（必需项）—— 抓包复制任意请求的完整 User-Agent 整串填进来")
    log.log("   形如：Mozilla/5.0 (Linux; Android 15; <你的机型> Build/XXX; wv) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Version/4.0 Chrome/XXX Mobile Safari/537.36 MCloudApp/<版本>")
    return False


class MobileCloudDisk:
    """中国移动云盘 —— 云朵中心（AI豆）任务执行器"""
    
    def __init__(self, cookie: str):
        fields = (cookie or "").split("#")
        self.authorization = fields[0].strip() if fields else ""
        self.account = fields[1].strip() if len(fields) > 1 else ""
        self.token = fields[2].strip() if len(fields) > 2 else ""
        # 设备号优先级：ydyp_ck 第 4 段 > ydyp_device_id > 按账号派生（UUID 形式，稳定且各账号互不相同）
        self.device_id = (fields[3].strip() if len(fields) > 3 and fields[3].strip()
                          else DEVICE_ID_ENV
                               or str(uuid.uuid5(uuid.NAMESPACE_DNS, "ydyp:" + (self.account or "unknown"))))
        self.show_account = ((self.account[:3] + "****" + self.account[-4:])
                             if len(self.account) >= 11 else self.account)
        self.jwt = ""
        self.beans = None
        self.signed_today = None
        self.info = {}  # infoV3 全量结果（含 receiveList）
        self.client = httpx.AsyncClient(verify=False, timeout=60, follow_redirects=True)
        self.base_headers = {"User-Agent": UA, "Accept": "*/*",
                             "Referer": H5 + "/portal/newsignin/index.html", "Origin": H5,
                             "deviceId": self.device_id}
    
    # ── 基础设施 ────────────────────────────────────────────────────────
    def _h(self, extra: dict = None) -> dict:
        h = dict(self.base_headers)
        if self.jwt:
            h["jwtToken"] = self.jwt
        if extra:
            h.update(extra)
        return h
    
    def _dev_h(self, extra: dict = None) -> dict:
        """真机同款设备头：isDeviceId 让网关校验「体/参数里的 deviceId」。
        未配 ydyp_device_token 时保持原样"""
        h = self._h(extra)
        if DEVICE_TOKEN:
            h.pop("deviceId", None)  # App 不在头部带设备号，设备号走请求体 / URL 参数
            h.update({"isDeviceId": "true", "activityId": MARKET_NAME,
                      "appVersion": APP_VERSION, "x-requested-with": "com.chinamobile.mcloud"})
        return h
    
    async def _req(self, method: str, url: str, **kw):
        """统一请求 + JSON 解析；异常吞掉返回 None，避免单点失败中断整轮。
        连接/DNS 类失败自动重试 3 次（1s/2s 退避）：运维实测老 market 域
        caiyun.feixin.10086.cn 会间歇性解析失败（[Errno -3] Try again），单次失败不等于接口下线。
        注意只重试「请求没发出去」的错误（ConnectError/ConnectTimeout/DNS），
        读超时（ReadTimeout）不重试，避免抽奖这类会消耗次数的接口被重复提交。
        quiet=True 时不再打印「非 JSON 响应」告警——给预期可能 404/未部署的探测型接口用。"""
        quiet = kw.pop("quiet", False)
        tries = int(kw.pop("tries", 3))
        for attempt in range(1, tries + 1):
            try:
                resp = await self.client.request(method, url, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
                    httpx.ProxyError, OSError) as e:
                if attempt < tries:
                    await asyncio.sleep(1.0 * attempt)  # 给 DNS/连接一点恢复时间
                    continue
                if not quiet:
                    log.log(f"    ❌ 请求失败（已重试 {tries} 次）{url.split('?')[0]}: {e}")
                return None
            except Exception as e:
                log.log(f"    ❌ 请求异常 {url.split('?')[0]}: {e}")
                return None
            try:
                return resp.json()
            except ValueError:
                if not quiet:
                    snippet = " ".join(resp.text.split())[:100]  # 404 会回整页 HTML，压成单行再入日志
                    log.log(f"    ⚠️ 非 JSON 响应 {resp.status_code}: {snippet}")
                return None
        return None
    
    async def _yget(self, path: str, params: dict = None, quiet: bool = False, dev: bool = False):
        """dev=True：按真机方式把设备令牌放进 URL 参数（startSignIn 就是这样）。"""
        params = dict(params or {})
        if dev and DEVICE_TOKEN:
            params["deviceId"] = DEVICE_TOKEN
        return await self._req("GET", YCLOUD + path, params=params,
                               headers=self._dev_h() if dev else self._h(), quiet=quiet)
    
    async def _ypost(self, path: str, payload: dict = None, quiet: bool = False, dev: bool = False):
        """dev=True：按真机方式把设备令牌放进请求体（receiveV3 就是这样）。"""
        jh = {"Content-Type": "application/json;charset=UTF-8"}
        payload = dict(payload or {})
        if dev and DEVICE_TOKEN:
            payload["deviceId"] = DEVICE_TOKEN
        return await self._req("POST", YCLOUD + path, json=payload,
                               headers=(self._dev_h(jh) if dev else self._h(jh)), quiet=quiet)
    
    async def _mget(self, path: str):
        """老 market 接口（抽奖 / 备份 / 通知）"""
        return await self._req("GET", MARKET + path, headers=self._h())
    
    async def _balance(self):
        """读豆余额（infoV3.result.total 为权威值）"""
        r = await self._yget("/signin/page/infoV3", {"client": "app"})
        if r and r.get("code") == 0:
            self.info = r.get("result") or {}
            return self.info.get("total")
        r = await self._yget("/signin/page/getCloudNum", {"client": "app"})
        return (r or {}).get("result") if r and r.get("code") == 0 else None
    
    async def _jwt_alive(self) -> bool:
        """探一下当前 jwtToken 是否仍然可用（只读接口，无副作用）"""
        r = await self._yget("/signin/page/infoV3", {"client": "app"})
        return bool(r and r.get("code") == 0 and r.get("result"))
    
    async def login(self) -> bool:
        if self.jwt:
            return True
        # 形态 1/2：第 1 段或第 3 段直接是 jwtToken
        for cand, label in ((self.authorization, "第1段"), (self.token, "第3段")):
            if cand.count(".") == 2 and len(cand) > 80:
                self.jwt = cand
                if await self._jwt_alive():
                    log.log(f"  🔑 使用{label}直填的 jwtToken（{self.show_account}）")
                    return True
                self.jwt = ""
                log.log(f"  ⚠️ {label}填的 jwtToken 已失效")
                if label == "第1段":  # 第 1 段是 jwt 时没有可回退的凭据
                    log.log("  ❌ 请重新抓包替换 ydyp_ck（第 1 段应填 Basic authorization）")
                    return False
        
        sso = await self._query_sso_token()
        if sso and await self._tyrz_login(sso):
            return True
        return False
    
    async def _query_sso_token(self):
        """① authorization → ssoToken（toSourceId 必须与登录 sourceId 一致，实测 001005）"""
        r = await self._req("POST", "https://user-njs.yun.139.com/user/querySpecTokenV2",
                            headers={"authorization": self.authorization, "User-Agent": UA,
                                     "Content-Type": "application/json"},
                            json={"toSourceId": SOURCE_ID})
        if r and r.get("success") and (r.get("data") or {}).get("token"):
            return r["data"]["token"]
        if (r or {}).get("code") == "05050006":
            log.log("  ❌ authorization 已失效（05050006 暂无权限），请重新抓包更新 ydyp_ck")
        else:
            log.log(f"  ❌ 换取 ssoToken 失败：{r}")
        return None
    
    async def _tyrz_login(self, sso: str) -> bool:
        """② ssoToken → jwtToken（POST JSON；签名字符串为空串）"""
        body = {"token": sso, "openAccount": False,
                "marketName": MARKET_NAME, "sourceId": SOURCE_ID}
        h = {"Content-Type": "application/json;charset=UTF-8", "type": "signupPlatform",
             "deviceId": self.device_id, "User-Agent": UA,
             "Referer": H5 + "/portal/newsignin/index.html", "Origin": H5}
        h.update(_sign_headers(""))
        r = await self._req("POST", H5 + "/ycloud/auth-service/auth/tyrzLogin",
                            content=json.dumps(body, separators=(",", ":")), headers=h)
        if r and r.get("code") == 0 and (r.get("result") or {}).get("token"):
            self.jwt = r["result"]["token"]
            log.log(f"  🔑 登录成功（{self.show_account}）")
            return True
        log.log(f"  ❌ 换取 jwtToken 失败：{r}")
        return False
    
    async def query_status(self, quiet=False):
        """签到状态（infoV3）+ 豆余额 + 待领明细。quiet=True 只刷新 self.info 不打日志（供补领轮询用）"""
        r = await self._yget("/signin/page/infoV3", {"client": "app"})
        if r and r.get("code") == 0:
            res = r.get("result") or {}
            self.info = res
            today = datetime.now().day
            item = next((x for x in (res.get("cal") or [])
                         if x.get("d") == today and x.get("currentMonth") == 1), None)
            self.signed_today = bool(item and item.get("s"))
            self.beans = res.get("total")
            if not quiet:
                log.log("  📅 签到：%s（连签 %s 天｜本月 %s 天｜单次 +%s 豆）"
                        % ("今日已签 ⏭️" if self.signed_today else "今日未签 🉑",
                           res.get("signCount"), res.get("monthDays"), res.get("signInPoints")))
                log.log(f"  🫘 AI豆余额：{self.beans}（接口标记待领 {res.get('toReceive')}）")
            return res
        log.log(f"  ⚠️ 查询签到状态失败：{r}")
        return None
    
    async def sign_in(self):
        """点击即签到（接口幂等：今日已签仍返回 todaySignIn=true）"""
        r = await self._yget("/signin/page/startSignIn", {"client": "app"}, dev=True)
        if r and r.get("code") == 0:
            res = r.get("result") or {}
            # 接口存在读从库返回 todaySignIn=False、但当天日历已标已签的情况（issue #50 实测：
            # 返回 todaySignIn=False 的同一份响应里 cal[d=今天].s=True，事后余额也 +3）→ 以日历为准，避免误报
            today = datetime.now().day
            cell = next((x for x in (res.get("cal") or [])
                         if x.get("d") == today and x.get("currentMonth") == 1), None)
            signed = bool(res.get("todaySignIn")) or bool(cell and cell.get("s"))
            if signed:
                extra = "" if res.get("todaySignIn") else "（todaySignIn=False，但日历已标已签，按成功计）"
                log.log(f"  ✅ 签到完成（+{res.get('signInPoints')} 豆｜连签 {res.get('signCount')} 天）{extra}")
            else:
                log.log(f"  ⚠️ 签到未生效：{res}")
        elif r and r.get("code") in (614, 615):
            log.log("  ⚠️ 签到失败（614/615 风控），稍后重试")
        else:
            log.log(f"  ❌ 签到失败：{r}")
    
    async def sign_double(self):
        r = await self._yget("/signin/page/multiple", {"client": "app"})
        if r and r.get("code") == 0:
            res = r.get("result") or {}
            log.log(f"  ✖️2️⃣ 签到翻倍领取成功：+{res.get('cloudCount')} 豆")
        elif r and "已领取" in str(r.get("msg")):
            log.log(f"  ✖️2️⃣ 签到翻倍：{r.get('msg')} ⏭️")
        else:
            log.log(f"  ✖️2️⃣ 签到翻倍：{r}")
    
    async def claim_records(self):
        """
        infoV3.receiveList 三类：
          cloudType=0 → POST /signin/page/receiveV3 {client, cloudId=recordId, cloudType}
          cloudType=1 → GET  /signin/page/receiveTaskExpansion?acceptDate=YYYYMM（膨胀/月度）
          cloudType=2 → 下月可领取，跳过
        """
        if not self.info:
            await self.query_status()
        items = self.info.get("receiveList") or []
        if not items:
            log.log("  🫘 待领取记录：无 ⏭️")
            return 0
        total = pending = lock_streak = 0
        for it in items:
            ct, num = it.get("cloudType"), int(it.get("cloudNum") or 0)
            if ct == 2:
                log.log(f"  🫘 {num} 豆：下月可领取 ⏭️")
                continue
            if ct == 1:
                date = it.get("acceptDate") or datetime.now().strftime("%Y%m")
                rr = await self._yget("/signin/page/receiveTaskExpansion", {"acceptDate": date})
                if not (rr and rr.get("code") == 0):
                    rr = await self._mget(f"/market/signin/page/receiveTaskExpansion?acceptDate={date}")
                if rr and rr.get("code") == 0:
                    out = rr.get("result")
                    got = out.get("cloudCount") if isinstance(out, dict) else out
                    log.log(f"  🫘 膨胀/月度奖励领取成功：{got}（{date}）")
                    total += num
                else:
                    log.log(f"  🫘 膨胀/月度奖励领取失败：{rr}")
                continue
            rr = None
            if not DEVICE_TOKEN and total == 0 and pending == 0:
                log.log("  ⚠️ 未配置 ydyp_device_token：领取记录豆会被判「未知设备」回 614，")
                log.log("     按脚本头「这些值怎么拿到」抓一次填上即可（同账号实测补上即成功）")
            tries = 1 if lock_streak >= 2 else 3  # 连续锁定后不再逐笔重试，节省运行时间
            for attempt in range(1, tries + 1):
                rr = await self._ypost("/signin/page/receiveV3",
                                       {"client": "app", "cloudId": it.get("recordId"), "cloudType": ct},
                                       dev=True)
                if rr and rr.get("code") == 0:
                    break
                if (rr or {}).get("code") not in (614, 615):
                    break
                if attempt < tries:
                    await asyncio.sleep(3 * attempt)
            if rr and rr.get("code") == 0:
                lock_streak = 0
                got = (rr.get("result") or {}).get("receive") or num
                log.log(f"  🫘 领取 {got} 豆 ✅（recordId={it.get('recordId')}）")
                total += int(got or 0)
            else:
                pending += num
                lock_streak += 1
                log.log(f"  🫘 领取失败（{num} 豆, recordId={it.get('recordId')}, code={(rr or {}).get('code')}）："
                        f"{(rr or {}).get('msg')}"
                        + ("｜614/615=设备未登记或风控锁定（检查 ydyp_device_token），下次运行自动重试"
                           if (rr or {}).get("code") in (614, 615) else ""))
            await asyncio.sleep(random.uniform(0.8, 1.6))
        if total:
            log.log(f"  🫘 记录豆合计领取：+{total}")
        if pending:
            log.log(f"  🫘 暂未领到：{pending} 豆（服务端锁定，稍后/下次运行重试）")
        return total
    
    async def task_list(self):
        body = {"marketname": MARKET_NAME, "client": 1, "clientVersion": CLIENT_VERSION}
        r = await self._ypost("/signin/task/taskListV3", body)
        if not (r and r.get("code") == 0):
            log.log(f"  ❌ 获取任务列表失败：{r}")
            return
        tasks = r.get("result") or []
        clicked = skipped = 0
        for t in tasks:
            tid, name, gid = t.get("id"), (t.get("name") or "").strip(), t.get("groupid")
            if gid in ("hidden", "hiddenabc") and tid not in (106, 522):
                continue
            if t.get("state") != "FINISH":
                await self._click_task(tid, name)
                if tid in (106, 522):  # 上传类任务：走 OSE 新通道
                    await self.upload_file()
                await asyncio.sleep(random.uniform(1.0, 2.0))
                clicked += 1
            else:
                skipped += 1
        gained = 0
        for t in tasks:
            tid, name = t.get("id"), (t.get("name") or "").strip()
            got = await self._claim_task(tid, name)
            gained += got or 0
        log.log(f"  🗂️ 任务处理：点击 {clicked} 条｜已完成 {skipped} 条｜本次领到 {gained} 豆")
        return clicked
    
    async def settle_task_rewards(self):
        """任务奖励异步记账的补领（issue #50 根因，详见说明 10）。

        任务 state→FINISH 是即时的，奖励却要过一会儿才出现在 infoV3.receiveList（记录豆）里；
        旧版只在本轮开头领一次，于是「第一遍永远领不到、第二遍才行」。
        这里刷新状态 + 有限轮询，记账够快时当次领掉；就是不快也只是维持旧行为（下一轮定时任务补领）。
        """
        seen = {it.get("recordId") for it in ((self.info or {}).get("receiveList") or [])}
        elapsed = 0
        for wait in WAIT_SETTLE:
            await asyncio.sleep(wait)
            elapsed += wait
            await self.query_status(quiet=True)  # 只刷新 self.info，不打日志
            items = (self.info or {}).get("receiveList") or []
            fresh = [it for it in items
                     if it.get("recordId") not in seen and it.get("cloudType") != 2]
            if fresh:
                log.log(f"      ⏳ 任务奖励已记账（等待 {elapsed}s，{len(fresh)} 笔），立即补领")
                got = await self.claim_records()   # claim_records 直接用刚刷新的 self.info
                if got:
                    log.log(f"      🫘 本次补领 +{got} 豆")
                return got
        log.log(f"      ⏳ 任务奖励仍在服务端记账中（已等 {elapsed}s）：本轮不领，下一轮定时任务自动补领")
        return 0

    async def _click_task(self, task_id, name=""):
        r = await self._yget("/signin/task/click", {"key": "task", "id": task_id})
        ok = bool(r and r.get("code") == 0)
        log.log(f"      {'✅' if ok else '⏭️'} {name[:20]}（id={task_id}）{'' if ok else '：' + str(r)}")
    
    async def _claim_task(self, task_id, name=""):
        """领取任务奖励（任务未完成时返回 result:0）"""
        r = await self._yget("/signin/page/receiveTask", {"taskId": task_id})
        if r and r.get("code") == 0 and (r.get("result") or 0) > 0:
            log.log(f"      🎁 「{name[:20]}」领取 {r['result']} 豆")
            return int(r["result"])
        return 0
    
    
    async def backup_cloud(self):
        """连续备份奖励 + 每月膨胀云朵"""
        info = await self._yget("/backupgift/info")  # 新域同值且更稳
        if not (info and info.get("code") == 0):
            info = await self._mget("/market/backupgift/info")
        state = ((info or {}).get("result") or {}).get("state")
        if state == 0:
            r = await self._mget("/market/backupgift/receive")
            if r and r.get("code") == 0:
                res = r.get("result")
                got = res.get("result") if isinstance(res, dict) else res
                log.log(f"  ☁️ 连续备份奖励领取成功：{got} 豆")
            else:
                log.log(f"  ☁️ 连续备份奖励领取失败：{r}")
        elif state == 1:
            log.log("  ☁️ 连续备份奖励：本月已领 ⏭️")
        elif state is None:
            log.log(f"  ☁️ 连续备份奖励：接口返回异常 ⏭️ {info}")
        else:
            log.log(f"  ☁️ 连续备份奖励：本月未备份，暂无 ⏭️（state={state}）")
        
        exp = await self._mget("/market/signin/page/taskExpansion")
        res = (exp or {}).get("result") or {}
        if res.get("preMonthBackup") and not res.get("curMonthBackupTaskAccept"):
            accept_date = res.get("aeptDate") or datetime.now().strftime("%Y-%m-%d")
            r = await self._mget(f"/market/signin/page/receiveTaskExpansion?acceptDate={accept_date}")
            if r and r.get("code") == 0:
                out = r.get("result")
                got = out.get("cloudCount") if isinstance(out, dict) else out
                log.log(f"  ☁️ 膨胀云朵领取成功：{got}")
            else:
                log.log(f"  ☁️ 膨胀云朵：{r}")
        else:
            log.log("  ☁️ 膨胀云朵：暂无可领 ⏭️")
    
    async def notice_task(self):
        """开启 App 通知奖励（msgPushOn）"""
        r = await self._mget("/market/msgPushOn/task/status")
        res = (r or {}).get("result") or {}
        if not res:
            log.log(f"  ⏭️ 通知任务不可用：{r}")
            return
        log.log(f"  🔔 通知已开启 {res.get('onDuaration')} 天（累计 {res.get('total')} 天）")
        for t, st in ((1, res.get("firstTaskStatus")), (2, res.get("secondTaskStatus"))):
            if st == 2:  # 2 = 可领取
                rr = await self._req("POST", MARKET + "/market/msgPushOn/task/obtain",
                                     json={"type": t},
                                     headers=self._h({"Content-Type": "application/json"}))
                if rr and rr.get("code") == 0:
                    log.log(f"  🔔 通知任务{t} 领取成功：{rr.get('result')}")
                else:
                    log.log(f"  🔔 通知任务{t} 领取返回：{rr}")
    
    
    async def upload_file(self, count=1):
        """OSE 三步上传：getUploadUrl → PUT 原始字节 → file/complete。
        实测：传 1 个即把任务 106 推到 FINISH；但**不计入 522**（见 说明 5）。"""
        ok = fail = streak = 0
        total = max(1, count)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        for i in range(total):
            fname = "mock_%s_%02d.txt" % (stamp, i + 1)
            data = ("mcloud mock doc %s" % fname).encode()
            r = await self._ypost("/api/cloud/ose/activity/getUploadUrl",
                                  {"marketName": UPLOAD_MARKET, "fileName": fname, "fileSize": len(data)})
            res = (r or {}).get("result") or {}
            if not (r and r.get("code") == 0 and res.get("uploadUrl")):
                fail += 1
                streak += 1
                log.log(f"      📤 取上传地址失败（{i + 1}/{total}）：{(r or {}).get('msg') or r}")
                if streak >= 3:
                    log.log("      📤 连续失败 3 次，中止本批上传（疑似频控），下次运行继续")
                    break
                await asyncio.sleep(3)
                continue
            try:
                resp = await self.client.put(res["uploadUrl"], content=data,
                                             headers={"Content-Type": "application/octet-stream"})
            except Exception as e:
                fail += 1
                streak += 1
                log.log(f"      📤 PUT 异常（{i + 1}/{total}）：{e}")
                continue
            if resp.status_code not in (200, 201, 204):
                fail += 1
                streak += 1
                log.log(f"      📤 PUT 失败 HTTP {resp.status_code}（{i + 1}/{total}）")
                continue
            algo = (res.get("hashAlgorithm") or "SHA256").upper()
            ch = (hashlib.sha256(data).hexdigest() if algo == "SHA256"
                  else hashlib.md5(data).hexdigest())
            rr = await self._ypost("/api/cloud/ose/file/complete",
                                   {"uploadId": res.get("uploadId"), "fileId": res.get("fileId"),
                                    "contentHash": ch, "contentHashAlgorithm": res.get("hashAlgorithm") or "SHA256"})
            if rr and rr.get("code") == 0:
                ok += 1
                streak = 0
            else:
                fail += 1
                streak += 1
                log.log(f"      📤 完成上传失败（{i + 1}/{total}）：{rr}")
            if total > 5 and (i + 1) % 10 == 0:
                log.log(f"      📤 进度 {i + 1}/{total}（成功 {ok}｜失败 {fail}）")
            await asyncio.sleep(random.uniform(0.6, 1.2))
        if ok:
            log.log(f"      📤 本次上传 {ok} 个文件（失败 {fail}）")
        return ok
    
    async def upload_fill_522(self, need: int):
        """522 补足开关（默认关闭，实测无效，仅备口径变化）。
        另：家庭云 V3 通道（group.yun.139.com/hcy/…）需账号已开通家庭云，普通账号走不通。"""
        if need <= 0:
            return
        if not UPLOAD_FILL:
            log.log(f"      📤 522 还差 {need} 个（不统计本通道上传，需真实上传/PC 客户端累计）")
            return
        log.log(f"      📤 522 补足上传 {need} 个文件（注意：实测该通道不计入 522）…")
        await self.upload_file(count=min(need, 100))
    
    async def live_room_flower(self):
        """直播间小红花：首次参与直接赠花（实测领到 10 朵）"""
        info = await self._yget("/liveRoomFeedback/user/info")
        res = (info or {}).get("result") or {}
        if not res:
            log.log(f"    🌸 直播间：接口不可用 ⏭️")
            return
        if res.get("hasNotReceivedRedFlower") == 1:
            r = await self._ypost("/liveRoomFeedback/firstParticipate/give")
            out = (r or {}).get("result") or {}
            if r and r.get("code") == 0:
                log.log(f"    🌸 首参领红花：{out.get('prizeName')}（{out.get('flowerNum')} 朵）✅")
            else:
                log.log(f"    🌸 首参领红花失败：{r}")
        else:
            log.log(f"    🌸 直播间红花：已领 ⏭️（库存 {res.get('flowerCnt')}｜有效期 {res.get('redFlowerValidDate')}）")
        rf = await self._yget("/liveRoomFeedback/redFlower/queryRevivalFlowers")
        if rf and rf.get("code") == 0:
            log.log(f"    🌸 可复活红花：{rf.get('result')}")
    
    async def annual_reward(self):
        """年度回馈：开福袋抽奖（与新春礼同源接口 /simple/springgift/*，活动窗口至 2026-12-31；有次数才抽）"""
        tl = await self._yget("/simple/springgift/getTaskList")
        reg_ok = True
        for t in ((tl or {}).get("result") or []):
            if t.get("complete") or not t.get("needRegister") or not reg_ok:
                continue
            r = await self._ypost("/simple/springgift/registerTask", {"mark": t.get("id")}, quiet=True)
            if r is None:  # 该路由服务端未部署（404），后续不再尝试
                reg_ok = False
                log.log("    🧧 年度回馈：任务登记接口未部署（registerTask 404），跳过登记 ⏭️")
        cnt = await self._yget("/simple/springgift/getLotteryCount")
        try:
            n = int((cnt or {}).get("result") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            log.log("    🧧 年度回馈：无抽奖次数 ⏭️")
            return
        log.log(f"    🧧 年度回馈：可用 {n} 次")
        for _ in range(min(n, 6)):
            r = await self._ypost("/simple/springgift/lotteryPrize", {"isClient": True})
            name = ((r or {}).get("result") or {}).get("prizeName")
            log.log(f"        {'🎉 抽中：' + str(name) if name else '⏭️ 本次未中奖'}")
            await asyncio.sleep(random.uniform(1.0, 2.0))
    
    async def token_pk(self):
        """TokenPK：阶段进度奖励 + 抽奖机会"""
        home = await self._yget("/tokenpk/toplist/progress/queryHome")
        res = (home or {}).get("result") or {}
        if not res:
            log.log("    🏆 TokenPK：活动未开 ⏭️")
            return
        used = res.get("usedToken")
        stages = res.get("rewardStages") or []
        log.log(f"    🏆 TokenPK：已用 {used} token｜{len(stages)} 个阶段")
        for st in stages:
            if st.get("status") == 1:  # 1 = 可领取
                r = await self._ypost("/tokenpk/toplist/progress/receiveReward",
                                      {"phaseNo": st.get("phaseNo")})
                log.log(f"        🏆 阶段 {st.get('phaseNo')}（{st.get('threshold')}）奖励：{r}")
        await self._ypost("/tokenpk/toplist/progress/autoReceiveLotteryChance")
        ch = await self._yget("/tokenpk/toplist/progress/queryRemainChance")
        try:
            c = int((ch or {}).get("result") or 0)
        except (TypeError, ValueError):
            c = 0
        for _ in range(c):
            r = await self._ypost("/tokenpk/toplist/progress/lottery")
            log.log(f"        🎟️ TokenPK 抽奖：{(r or {}).get('result')}")
            await asyncio.sleep(1.2)
    
    async def mcloud_day(self):
        """云盘日（会员日，每月 16 号 10:00 开窗）：盲盒 + 礼品。
        未开窗时 /mcloudday/gift/* 与 /mcloudday/blindbox/* 整体 404（活动按窗口上下线），
        所以先看 online 再动作——否则会像旧版那样逐条试探 20+ 个礼品、刷一屏 404。"""
        info = await self._yget("/mcloudday/common/activityInfo", {"marketName": "mCloudDay"})
        res = (info or {}).get("result") or {}
        if not res:
            log.log("    📦 云盘日：接口不可用 ⏭️")
            return
        online = bool(res.get("online"))
        log.log("    📦 云盘日：主活动 %s｜盲盒 %s｜额外礼 %s｜邀请 %s"
                % (online, res.get("blindboxOnline"), res.get("extGiftOnline"),
                   res.get("inviteOnline")))
        if not online:
            eta = ""
            try:
                left = float(res.get("countDownTimeStamp") or 0) / 3600000.0
                eta = f"，约 {left:.1f} 小时后开窗" if left > 0 else ""
            except (TypeError, ValueError):
                eta = ""
            log.log(f"    📦 云盘日未开窗（每月 16 号 10:00）{eta} ⏭️ 礼品/盲盒接口未开窗返回 404，已跳过")
            return
        if res.get("blindboxOnline"):
            li = await self._yget("/mcloudday/blindbox/lotteryInfo", quiet=True)
            lres = (li or {}).get("result") or {}
            try:
                n = int(lres.get("lotteryCount") or 0)
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                log.log("        🎁 盲盒：今日免费次数已用完 ⏭️")
            for _ in range(min(max(n, 0), 5)):
                # ⚠️ 抽奖必须「JSON body（client=1=App 端）+ isDeviceId 设备头」两者齐备，
                #    否则网关直接回 nginx 404（不是业务错误码）。2026-10-16 实测对照：
                #    POST {}                 -> 404
                #    POST {"client":"1"}+设备头 -> 200 {"code":0}（次数同步 -1）
                # 只重试连接类错误、读超时不重试，避免重复消耗次数（见 _req）。
                r = await self._ypost("/mcloudday/blindbox/lottery", {"client": "1"}, dev=True, quiet=True)
                if r and r.get("code") == 0:
                    prize = (r.get("result") or {}).get("prize") or {}
                    name = prize.get("prizeName") if isinstance(prize, dict) else prize
                    log.log(f"        🎁 盲盒：抽中 {name}" if name else "        🎁 盲盒：空盒（本次未中奖）")
                else:
                    log.log(f"        🎁 盲盒：{(r or {}).get('msg') or r}")
                await asyncio.sleep(random.uniform(1.2, 2.0))
            if lres.get("hasAnySurprisePrizeStock"):
                # 惊喜助力礼按邀请人数分档（inviteCount 1/4/10），未达门槛回 40001「未满足惊喜奖品条件」
                r = await self._ypost("/mcloudday/blindbox/receiveSurprisePrize",
                                      {"inviteCount": 1}, quiet=True)
                if r and r.get("code") == 0:
                    log.log(f"        🎁 惊喜礼：{r.get('result')}")
                else:
                    log.log(f"        🎁 惊喜礼：{(r or {}).get('msg') or r}（当前邀请 {lres.get('totalInviteCount', 0)} 人）")
        gl = await self._yget("/mcloudday/gift/list", quiet=True)
        gre = (gl or {}).get("result") or {}
        prizes = (gre.get("nationalPrizeList") or []) + (gre.get("provPrizeList") or [])
        stock = [p for p in prizes if p.get("hasStock") and not p.get("receiveFlag")]
        if not stock:
            # 限量礼品（totalCount/hasStock 由服务端放的量决定）；每月 16 号 10/12/15 点各放一波
            log.log(f"    📦 云盘日礼品：{len(prizes)} 项均无库存（限量已抢完）或已领 ⏭️")
            return
        got = []
        for p in stock:
            # 入参照 H5：{prizeId, smsCode, deviceId}。2026-10-16 实测（拿零库存奖品打，不消耗）：
            #   只发 {prizeId}（无论带不带设备头/令牌）-> nginx 404；补 smsCode 字段 -> 200 业务码
            #   ⇒ smsCode 必须存在（空码 = MD5("")），需短信的奖品会回 10004「验证码错误」
            r = await self._ypost("/mcloudday/gift/receive",
                                  {"prizeId": p.get("prizeId"), "smsCode": GIFT_SMS_CODE},
                                  dev=True, quiet=True)
            if r and r.get("code") == 0:
                got.append(str(p.get("prizeName")))
            else:
                m = str((r or {}).get("msg") or r)
                hint = "（该奖品要短信验证码，只能 App 内手领）" if ("验证" in m or "短信" in m) else ""
                log.log(f"        🎁 礼品「{p.get('prizeName')}」未领到：{m}{hint}")
            await asyncio.sleep(random.uniform(0.8, 1.5))
        log.log(f"    📦 云盘日礼品：成功 {len(got)}/{len(stock)} 件"
                + (f"（{'、'.join(got[:6])}{'…' if len(got) > 6 else ''}）" if got else ""))
    
    async def email_sms(self):
        """邮箱/短信联合活动：状态查询 + 奖励尝试（资格受限时返回 604）"""
        ti = await self._yget("/openemailsms-service/openEmailsms/getTaskInfo")
        res = (ti or {}).get("result") or {}
        if res:
            log.log("    📧 邮箱短信：任务一 %s｜任务二 %s"
                    % ((res.get("taskOneInfo") or {}).get("taskStatus"),
                       (res.get("taskTwoInfo") or {}).get("taskStatus")))
        r = await self._yget("/openemailsms-service/openEmailsms/reward")
        if r and r.get("code") == 0:
            log.log(f"    📧 邮箱短信奖励：{r.get('result')}")
        else:
            log.log(f"    📧 邮箱短信奖励：{(r or {}).get('msg')} ⏭️")
    
    async def activity_patrol(self):
        """活动巡检：逐个活动查状态，仅在有可领项时动作"""
        pats = (("签到翻倍", self.sign_double), ("直播间小红花", self.live_room_flower),
                ("年度回馈开福袋", self.annual_reward), ("TokenPK", self.token_pk),
                ("云盘日", self.mcloud_day), ("邮箱短信", self.email_sms))
        for name, fn in pats:
            try:
                await fn()
            except Exception as e:
                log.log(f"    ❌ {name} 异常：{e}")
            await asyncio.sleep(random.uniform(0.5, 1.2))
    
    async def run(self):
        log.log(f"========== 用户【{self.show_account}】 ==========")
        log.log(f"  📱 设备号 {self.device_id}｜UA {UA if len(UA) <= 72 else UA[:72] + '…'}"
                f"｜设备令牌 {'已配置 ✅' if DEVICE_TOKEN else '未配置 ⚠️（领记录豆会 614）'}")
        if not await self.login():
            log.log("  ❌ 登录失败，跳过该账号")
            return
        begin = await self._balance()
        await self.query_status()
        log.log("──────── 签到 ────────")
        await self.sign_in()
        log.log("──────── 记录豆领取（签到/任务记录）────────")
        await self.claim_records()
        log.log("──────── 任务 ────────")
        clicked = await self.task_list()
        # 522 缺口检查
        r = await self._ypost("/signin/task/taskListV3",
                              {"marketname": MARKET_NAME, "client": 1, "clientVersion": CLIENT_VERSION})
        for t in ((r or {}).get("result") or []):
            if t.get("id") == 522 and t.get("state") != "FINISH":
                await self.upload_fill_522(100 - int(t.get("process") or 0))
        if clicked:
            # 上传/点击类任务的奖励异步记账，跑完立即轮询补领（issue #50）
            await self.settle_task_rewards()
        log.log("──────── 连续备份奖励 ────────")
        await self.backup_cloud()
        log.log("──────── 通知任务 ────────")
        await self.notice_task()
        log.log("──────── 活动巡检 ────────")
        await self.activity_patrol()
        end = await self._balance()
        if begin is not None and end is not None:
            log.log(f"──────── 结束：AI豆 {begin} → {end}（+{end - begin}）────────")
        else:
            log.log(f"──────── 结束时 AI豆余额：{end} ────────")
        log.log("")


async def main():
    if not _require_ua():
        return
    cookies = get_env("ydyp_ck", "@")
    if not cookies:
        log.log("❌ 未配置环境变量 ydyp_ck")
        return
    for ck in cookies:
        worker = MobileCloudDisk(ck)
        try:
            await worker.run()
        finally:
            await worker.client.aclose()
    send_notification_message_collection(f"中国移动云盘签到通知 - {datetime.now().strftime('%Y/%m/%d')}")


async def check():
    """只读自检：不签到、不抽奖、不领豆"""
    if not _require_ua():
        return
    cookies = get_env("ydyp_ck", "@")
    if not cookies:
        log.log("❌ 未配置环境变量 ydyp_ck")
        return
    for ck in cookies:
        w = MobileCloudDisk(ck)
        try:
            log.log(f"========== 自检【{w.show_account}】 ==========")
            log.log("  📱 设备号：%s｜UA：%s｜设备令牌：%s" % (
                w.device_id, UA if len(UA) <= 72 else UA[:72] + "…",
                "已配置 ✅" if DEVICE_TOKEN else "未配置 ⚠️（签到/领记录豆会回 614）"))
            if not await w.login():
                log.log("  ❌ 凭据无效：请重新抓包更新 ydyp_ck")
                continue
            await w.query_status()
            items = w.info.get("receiveList") or []
            log.log(f"  🫘 待领取明细 {len(items)} 笔：")
            for it in items:
                log.log("      cloudType=%s %s 豆%s" % (it.get("cloudType"), it.get("cloudNum"),
                                                        f" acceptDate={it.get('acceptDate')}" if it.get(
                                                            "acceptDate") else
                                                        f" recordId={it.get('recordId')}" if it.get(
                                                            "recordId") else ""))
            r = await w._ypost("/signin/task/taskListV3",
                               {"marketname": MARKET_NAME, "client": 1, "clientVersion": CLIENT_VERSION})
            tasks = (r or {}).get("result") or []
            log.log(f"  🗂️ 任务 {len(tasks)} 条：")
            for t in tasks:
                log.log("      [%-10s] id=%-4s %-7s step=%-2s proc=%-4s %s" % (
                    t.get("groupid"), t.get("id"), t.get("state"), t.get("currstep"),
                    t.get("process"), (t.get("name") or "").strip()[:24]))
            b = await w._mget("/market/backupgift/info")
            log.log(f"  ☁️ 备份奖励：{b.get('result') if b else b}")
            p = await w._mget("/market/msgPushOn/task/status")
            log.log(f"  🔔 通知任务：{p.get('result') if p else p}")
            log.log("  🧭 活动状态（只读）：")
            for name, path, params in (("直播间", "/liveRoomFeedback/user/info", None),
                                       ("年度回馈抽奖次数", "/simple/springgift/getLotteryCount", None),
                                       ("TokenPK", "/tokenpk/toplist/progress/queryHome", None),
                                       ("云盘日", "/mcloudday/common/activityInfo", {"marketName": "mCloudDay"}),
                                       ("邮箱短信", "/openemailsms-service/openEmailsms/getTaskInfo", None)):
                rr = await w._yget(path, params)
                log.log("      %-14s code=%s %s" % (name, (rr or {}).get("code"),
                                                    json.dumps((rr or {}).get("result"), ensure_ascii=False)[:120]))
            log.log("  ✅ 自检完成（未做任何写操作）\n")
        finally:
            await w.client.aclose()


if __name__ == "__main__":
    if "--check" in sys.argv:
        asyncio.run(check())
    else:
        asyncio.run(main())
