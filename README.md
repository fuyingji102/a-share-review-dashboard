# A股资金与轮动 · 盘后复盘台

一个本地运行的盘后可视化程序。数据来自同花顺扶摇 Financial API，刷新后会把归一化结果保存到 `data/dashboard-data.json`，每日摘要保存到 `data/history/`，用于逐步形成主线轮动时间轴。

## 快速打开（无需 API Key）

双击 **`serve.cmd`**，然后访问 <http://127.0.0.1:8765> —— 页面会加载 `data/dashboard-data.json` 中已有的快照数据，无需同花顺 API Key。

## 手机访问（局域网）

1. 启动服务器时用 `--bind 0.0.0.0`：`python app.py --bind 0.0.0.0`
2. 查看电脑的局域网 IP：在 cmd 中输入 `ipconfig`，找到 IPv4 地址（如 `192.168.1.100`）
3. 手机在同个 WiFi 下，浏览器打开 `http://192.168.1.100:8765`

## 手机访问（外网）

### 方式一：Netlify 托管（推荐，最方便，电脑不用一直开）

把每天的盘后数据自动发布到 Netlify，手机打开网址就能看，电脑采集完就可以关。

**实现原理：**

```
Windows 定时任务 15:30 → collect.py 采集数据 → git push → Netlify 自动部署
```

**前置条件：**
1. 项目已初始化 Git 并推送到 [GitHub](https://github.com)
2. 在 [Netlify](https://app.netlify.com) 导入该仓库（Build command 留空，Publish directory 填 `.`）

**实时资金流向怎么办？**

Netlify 函数（`netlify/functions/fund-flow.js`）会在浏览器打开时实时代理东方财富的资金流 API。页面打开时：

1. 从 `data/dashboard-data.json` 加载静态盘后数据（大盘概况、行业榜、情绪等）
2. 同时通过 Netlify Function 实时获取资金流分钟数据
3. 资金流向图是**实时**的，其他盘后数据是**快照**的

**设置方法：**
1. 右键 **`setup-scheduled-task.ps1`** → "以管理员身份运行"，选择 Netlify 模式
2. 脚本会用 `collect-and-push.ps1` 替代纯采集，采集完自动 `git push`
3. Netlify 收到推送后自动部署

完成后你得到一个 `https://xxx.netlify.app` 的链接，手机、电脑随时随地都能打开。

> **注意：** Netlify 免费版每月 125,000 次函数调用，个人使用绰绰有余。资金流向数据仅在页面打开时请求，不会持续轮询。

### 方式二：Tailscale（免费，加密隧道）

1. 在你的 **电脑** 和 **手机** 上都安装 [Tailscale](https://tailscale.com/download)
2. 登录同一个账号
3. 电脑上启动复盘台：`python app.py --bind 127.0.0.1`（不需暴露到公网）
4. 手机用 Tailscale 分配的 IP 地址访问：`http://100.x.x.x:8765`
5. 只要两台设备都开着 Tailscale，随时随地都能连，数据加密传输。

### 方式三：Cloudflare Tunnel（免费，无需公网 IP）

1. 安装 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. 运行：`cloudflared tunnel --url http://127.0.0.1:8765`
3. 会生成一个 `https://xxxx.trycloudflare.com` 地址，手机打开即可
4. 每次重启地址会变，适合临时分享。长期用可注册 Cloudflare 域名创建固定隧道。

### 方式四：路由器端口转发（不推荐）

在路由器后台设置端口转发，把外网端口映射到电脑的 8765 端口。**有安全风险**，建议只配合 Tailscale 或 Cloudflare 使用。

## 完整部署方案（Windows 长期运行）

目标是：每天自动采集、服务器常开、手机能看。

### 文件说明

| 文件 | 作用 |
|------|------|
| `app.py` | 复盘台 HTTP 服务器 + 手动刷新采集 |
| `collect.py` | 独立数据采集器（运行一次即退出，供定时任务用） |
| `serve.cmd` | 双击启动静态 HTTP 服务器 |
| `setup-scheduled-task.ps1` | 一键创建 Windows 定时任务 |

### 部署步骤

**1. 配置 API Key**

把同花顺扶摇的 Token 设为环境变量，或在项目根目录创建 `.env`：
```
FUYAO_TOKEN=你的token
```

**2. 运行采集测试**

```powershell
python collect.py
```

如果返回行情摘要，说明 API Key 配置正确。

**3. 设置定时任务（每日 15:30 自动采集）**

右键 **`setup-scheduled-task.ps1`** → "以管理员身份运行"。

它会创建两个任务：
- **AShareDashboard-Collect**：每个交易日 15:30 运行 collect.py，日志保存在 `logs/collect.log`
- **AShareDashboard-Server**：登录 Windows 时自动启动复盘台服务器（绑定 `0.0.0.0`）

**4. 登录后自动启动**

重启电脑或重新登录，复盘台服务器会自动启动。手机在同个 WiFi 下即可访问。

### 手动管理

```powershell
# 手动采集
python collect.py

# 启动服务器（局域网可访问）
python app.py --bind 0.0.0.0

# 查看采集日志
type logs\collect.log

# 查看定时任务
schtasks /query /tn "AShareDashboard-*" /v /fo list
```

## 当前口径

- 两市成交额、红绿盘家数：全市场行情快照聚合。
- 行业/概念 Top 10：同花顺指数涨幅与成交额强度代理，不是资金净流入。
- 机构/游资净买：龙虎榜当日 `range_days=1` 口径。
- 涨跌停与连板：涨停池、当日异动和连板天梯。
- 龙头股：当日热股榜候选；价格走势从每日快照开始积累。
- 主力资金、ETF净流入、炸板率：当前数据源未开放或无直接字段，页面明确显示“待接入”。

## 安全

程序不会把 API Key 写入此交付目录。它优先读取环境变量，并兼容读取现有 `work/Financial-API/.env`。请勿把密钥提交到 Git 或发送到公开渠道。

本工具用于盘后研究，不构成投资建议。
