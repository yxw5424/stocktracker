# 把控制台分享给小范围的人(带登录)

> 现实约束:数据要在**国内网络**抓(akshare)。所以**别把整套搬到海外服务器**——
> 推荐「**你本地继续跑(数据没问题)+ 隧道映射成公网 HTTPS 链接 + 密码登录**」。

## 0. 先开登录(暴露前必做)

控制台默认无密码(只绑 127.0.0.1)。要对外,先设密码再起服务:

```powershell
$env:STK_PASSWORD = "你定的访问密码"
$env:STK_SECRET   = "一串随机字符(让登录态重启后不失效)"   # 可选但建议
python -m server.app
```
- 设了 `STK_PASSWORD` 后:**看板可浏览,任何"写操作"(加自选/改规则/发推送)都要先登录**。
- 没设:还是本地无密码模式,仅自己用。

> 安全要点:服务始终只绑 `127.0.0.1`(隧道/nginx 去连它),**永远不要直接 `0.0.0.0` 裸暴露端口**。
> 这是单实例共享:登录的人共用同一份自选/规则(适合小范围信任的人)。仍然**不替任何人下单**。

---

## 方案 A(推荐):Cloudflare Tunnel —— 免费、自带 HTTPS、免备案、不用公网IP

1. 装 `cloudflared`(`winget install Cloudflare.cloudflared` 或官网下载)。
2. 快速分享(临时域名,适合先试):
   ```powershell
   cloudflared tunnel --url http://127.0.0.1:8777
   ```
   会给一个 `https://xxxx.trycloudflare.com` 链接。**把这个链接 + 密码**发给那几个人即可。
3. 固定自有域名(长期用):你域名托管到 Cloudflare 后 →
   ```powershell
   cloudflared tunnel login
   cloudflared tunnel create stocktracker
   cloudflared tunnel route dns stocktracker app.你的域名.com
   cloudflared tunnel run --url http://127.0.0.1:8777 stocktracker
   ```
4.(可选,更稳)Cloudflare Zero Trust → Access → 给 `app.你的域名.com` 加**邮箱白名单**,边缘再挡一层,只有名单里的人能打开。

> 隧道是你机器**主动出站**连 Cloudflare,所以:不用公网IP、不用开端口、**国内域名免 ICP 备案**(域名在 Cloudflare 那边)。代价:**你电脑得开着**。

## 方案 B(临时试一下):ngrok

```powershell
ngrok http 8777
```
给个临时 https 链接,最快验证"别人能不能用"。长期不如 Cloudflare Tunnel。

## 方案 C(always-on,电脑不用开,但要花钱+配置):国内 VPS

- 阿里云/腾讯云**轻量服务器(国内节点,能抓数据)**,把 `run_local.py` + `server.app` 跑上去;
- `nginx` 反代到 `127.0.0.1:8777` + 域名 + HTTPS(certbot 免费证书);
- `STK_PASSWORD` 照设。
- ⚠️ **国内服务器绑域名要 ICP 备案**(几天~几周);只用服务器 IP 可免备案但没 HTTPS。想免备案又要 HTTPS,仍是回到「方案 A 隧道」。
- 适合:要 24 小时在线、电脑不常开。

---

## 三种方案怎么选

| | 你电脑要开着 | 花钱 | 备案 | HTTPS | 难度 |
|---|---|---|---|---|---|
| **A Cloudflare Tunnel** | 是 | 免费 | 免 | 自带 | ★ 最省 |
| B ngrok | 是 | 免费/付费 | 免 | 自带 | ★ 最快试 |
| C 国内 VPS | 否 | 月费 | 要 | 自配 | ★★★ |

**结论:先用 A 的"快速分享"两条命令验证,满意了再上自有域名 + Access 白名单。**
