# 语音栈部署（算力机 / WSL2）

2026-08-16 建立。曼波音色 TTS 从零跑通的完整记录与复现步骤。

## 拓扑

```
HA (NucBox)
  └─ media_player.play_media
       └─ MQTT broker ── Mosquitto :1883（本机 conda 版）
            └─ HASS.Agent（Windows 侧，便携版）
                 └─ HTTP GET 127.0.0.1:9881   ← mirrored 网络共享 localhost
                      └─ tts_proxy :9881      ← 补 Content-Length + 磁盘缓存
                           └─ GPT-SoVITS api.py :9880  ← 曼波音色，实时合成
                                └─ 电脑扬声器
```

## 路径约定

| 内容 | 位置 | 入库 |
|---|---|---|
| GPT-SoVITS 本体 | `~/apps/GPT-SoVITS` | ✗ upstream clone |
| 曼波权重 / 参考音频 | `~/apps/GPT-SoVITS/{GPT,SoVITS}_weights_v2Pro/`、`refer/` | ✗ 240MB，版权归 Cygames |
| Mosquitto 配置与密码 | `~/apps/mosquitto/config/` | ✗ 含凭据 |
| 缓存 / 日志 | `~/apps/jarvis/{cache,logs}/` | ✗ |
| 脚本 | 本仓库 `scripts/` | ✓ |
| systemd unit | 本仓库 `deploy/systemd/` | ✓ |

## 一、GPT-SoVITS

```bash
bash scripts/setup_gptsovits.sh        # 装 Miniconda + 环境 + 底模（约 6GB）
```

曼波权重来自 [MamboTTS](https://github.com/Tsukimisaka/MamboTTS) 的 release 整合包，
从 `models/` 里取出三个文件放到对应目录：

| 文件 | 放到 |
|---|---|
| `manbo-e10.ckpt` | `GPT_weights_v2Pro/` |
| `manbo_e8_s168.pth` | `SoVITS_weights_v2Pro/` |
| `refer.wav` | `refer/manbo_refer.wav` |

参考音频对应的**参考文本**（`-dt` 参数，必须一致，否则音色会漂）：

> 大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容

### 装的时候会踩的四个坑（都已在脚本里处理）

1. **torchaudio 版本错配** —— `requirements.txt` 没锁索引源，pip 会从默认 PyPI 拉
   CUDA 13 构建版，跟 cu128 的 torch 对不上。必须从 cu128 源重装：
   `pip install --index-url https://download.pytorch.org/whl/cu128 --force-reinstall --no-deps torchaudio==2.11.0`
2. **缺 `nvidia-npp-cu12`** —— torchcodec 依赖，但不在 torch 的预加载清单里，要单独 `pip install`（282MB）
3. **conda-forge 装的 FFmpeg 太新** —— 默认给 ffmpeg 9，torchcodec 只支持 4–8，
   要降级：`conda install --override-channels -c conda-forge 'ffmpeg=7.*'`
4. **`LD_LIBRARY_PATH` 必须显式设置** —— 见 `scripts/run_gptsovits_api.sh` 注释

⚠️ 第 3、4 条尤其要留意：**失败形式是「HTTP 200 + 0 字节」，不报错**。
以后升级 conda 包时 ffmpeg 很容易又被顶到最新版，会以同样方式静默失效。

另外 `install.sh` 需要打一行补丁才能在只有 conda-forge 的环境里跑
（`conda install` 加 `--override-channels`），否则会撞 Anaconda 官方频道的服务条款门槛。

## 二、Mosquitto

因 HA 主机拉不到 Docker Hub 镜像（DNS 污染 + IP 封锁），加载项装不上，
改用 conda-forge 版跑在本机（248KB，无需 root）：

```bash
conda create -n mqtt --override-channels -c conda-forge -y mosquitto
mosquitto_passwd -c ~/apps/mosquitto/config/passwd ha
mosquitto_passwd    ~/apps/mosquitto/config/passwd hassagent
```

两个账号分开是为了可单独吊销（蓝图第 05 节令牌隔离）。
配置见 `~/apps/mosquitto/config/mosquitto.conf`：`listener 1883 0.0.0.0` + `allow_anonymous false`。

> **这是权宜之计。** broker 属于 24/7 职责，按蓝图第 14.4 节不该跑在会关机的算力机上。
> NUC 能装加载项后应迁回去。

## 三、防火墙（Windows 侧，需管理员）

WSL 用 `networkingMode=mirrored`，**入站要开两层**，只开一层不通：

```powershell
New-NetFirewallRule -DisplayName "MQTT 1883 (WSL, LAN only)" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow -Profile Any -RemoteAddress LocalSubnet
New-NetFirewallHyperVRule -Name "MQTT-1883-WSL" -DisplayName "MQTT 1883 WSL" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 1883 -Action Allow
```

**9880/9881 不需要开** —— mirrored 模式下 Windows 直接够得着 WSL 的回环，
而 HASS.Agent 正是从 Windows 侧发起请求的。

## 四、HASS.Agent（Windows）

用 **Standalone 便携版**（绕开安装器的 UAC）。代价是安装器会做的
URL ACL 预留没人做，Local API 绑不上 5115，需手动补（管理员 PowerShell）：

```powershell
netsh http add urlacl url=http://+:5115/ user="$env:USERNAME"
```

HA 侧集成**不在 HACS 默认索引**，要加自定义存储库
`https://github.com/hass-agent/HASS.Agent-Integration`。
搜索 HACS 只能搜到 `v1k70rk4/HASS.Agent.NET10-Integration`，
那个**只兼容 .NET10 客户端**，配官方 2.x 客户端会失败。

⚠️ 两者 domain 同为 `hass_agent`、装在同一目录，
所以**必须先删旧的再装新的**，反过来会把新装的文件一起删掉。

装好后 HA 会经 MQTT 自动发现，确认发现流即可建实体（无需手填 host/port）。

## 五、systemd（用户级）

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mosquitto-jarvis gptsovits-api tts-proxy
```

**需要 root 一次**，否则退出登录后服务会被杀：

```bash
sudo loginctl enable-linger $USER
```

## 附：NUC 的网络限制（2026-08-16 摸清）

### 结论先行

| 目标 | 可达性 |
|---|---|
| **ghcr.io** | ✅ 完全可用 |
| **Docker Hub（registry-1.docker.io）** | ❌ DNS 被污染 + IP 不可达 |

所以准确的说法是「**NUC 装不了官方加载项**」，不是「装不了加载项」。
`hassio-addons`、`ESPHome`、`Music Assistant` 这些社区仓库的镜像都在 ghcr.io，**正常可装**
（实测装成功过 `a0d7b954_ssh`，日志里 `Starting Docker app ghcr.io/hassio-addons/ssh` 一次通过）。

这条对路线图很关键：**给其他房间做播放出口的 ESPHome 方案不受影响**，
而且 ESPHome 设备走 HA 原生 API，根本不经 MQTT。

### DNS 污染的具体表现

`registry-1.docker.io` 每次解析到不同的知名被墙 IP，三次实测分别是：

| 时间 | 返回的假 IP | 实际归属 |
|---|---|---|
| 初次 | `185.60.216.36` | Facebook |
| 换阿里 DNS 后 | `199.59.148.15` | Twitter |
| 再次 | `162.125.7.1` | Dropbox |

**换上游 DNS 无效**——GFW 对明文 53 端口一律注入伪造应答，查国内的阿里/DNSPod 也一样，
伪造包先到就赢。唯一解法是加密查询（DoT/DoH），而 Supervisor 的 `servers` 字段
**只接受 `dns://` 格式**，传 `tls://` 会被 schema 拒绝。

（`ha dns options` 的 `--fallback` 说明里写着「Cloudflare DoT」，但它只在上游**失败**时触发；
污染返回的是「成功」的假答案，触发不了兜底。这也是为什么问题一直不易察觉。）

### 试过但无效的方案

**打 tag 骗过 Supervisor —— 不管用。** 即使本地已有
`homeassistant/amd64-addon-mosquitto:7.1.0`，Supervisor 装的时候仍会无条件去拉 manifest：

```
Failed to connect to registry docker.io: Connection timeout
Downloading docker image homeassistant/amd64-addon-mosquitto with tag 7.1.0.
Can't install ...: dial tcp 162.125.7.1:443: i/o timeout
```

镜像源本身是通的（`docker.1ms.run` 拉取成功），但拉下来没用。

### 尚未尝试的方案

1. **给宿主机 Docker 配 registry mirror**（`/etc/docker/daemon.json`）——根治，但要用特权容器改宿主机文件、
   改完必须重启 Docker（连带重启 Supervisor 与 HA），**JSON 写错会导致 HA 起不来**，headless 机器需接显示器救援
2. **路由器刷 ShellClash**（小米 BE6500 Pro / RD08，`xmir-patcher` 支持，需先降级到 1.0.46 解锁 SSH）——
   一劳永逸且全屋受益，但有变砖风险，且这是家里唯一的路由器
3. **独立跑 Mosquitto 容器**（不走加载项体系）——能达成目的，但 Supervisor 检测到非托管容器
   可能把系统标记为 unsupported

### Supervisor API 的正确访问方式

长期访问令牌对 REST 的 `/api/hassio/*` **一律 401**，
但 HA 前端本身是通过 **WebSocket 的 `supervisor/api` 命令**代理过去的，那条通道可读可写。
见 `scripts/supervisor_ws.py`：

```bash
python scripts/supervisor_ws.py get  /dns/info
python scripts/supervisor_ws.py get  /addons
python scripts/supervisor_ws.py post /addons/<slug>/restart
```

⚠️ 日志类端点（`/supervisor/logs`、`/addons/<slug>/logs`）返回纯文本，
WS 代理接不住，会报空错误；看日志仍需 UI 或终端里的 `ha supervisor logs`。

## 六、验证

```bash
curl -s 'http://127.0.0.1:9881/healthz'
python scripts/tts_stream_bench.py          # 流式基准
```

实测参考（RTX 5060 Ti，曼波音色 v2Pro）：

| 指标 | 数值 |
|---|---|
| 模型加载 | 4s |
| 短句合成（「书房的灯已经关好了」） | 0.42s |
| 长句合成（70 字） | 2.12s |
| 合成实时率 | 6.4x |
| 显存占用 | 4.4GB |
| 首字延迟（整段一次合成） | 3.98s |
| **首字延迟（句级流式）** | **1.15s** |
