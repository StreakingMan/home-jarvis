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
| Mosquitto 配置与密码 | `~/apps/mosquitto/config/` | ✗ 含凭据（`passwd` 是哈希，取不回明文） |
| **MQTT 账号明文备份** | `~/apps/jarvis/config/mqtt_credentials.txt`（`chmod 600`） | ✗ **仓库是公开的，切勿入库** |
| Ollama 本体 | `~/apps/ollama`（2.1G，含 CUDA 运行库） | ✗ |
| Ollama 模型 | `~/apps/ollama/models` | ✗ |
| Ollama 机器相关配置 | `~/apps/jarvis/config/ollama.env`（代理等） | ✗ |
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

### 开机自愈（两条配套，缺一不可）

**① linger** —— 否则没有登录会话时 systemd 会杀掉全部用户服务。需 root 一次：

```bash
sudo loginctl enable-linger $USER
```

**② 登录时拉起 WSL** —— WSL 不随 Windows 开机启动，不拉起来上面那条也白搭。

计划任务这条路走不通：`schtasks /create` 即使用 `/sc onlogon /rl LIMITED`
仍返回「拒绝访问」，要提权。**改用启动文件夹，不需要管理员**，而且内容可见、可随时删：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis-Boot-WSL.vbs
```

```vbs
CreateObject("WScript.Shell").Run "wsl.exe -d Ubuntu -e true", 0, False
```

`wsl -e true` 只是让这条命令本身立刻退出，WSL 实例会继续运行；
参数 `0` 隐藏窗口、`False` 不等待，所以登录时无感。

**两条的关系**：只有 ② 没有 ① → WSL 起来了但服务被杀；
只有 ① 没有 ② → 服务能常驻但 Windows 重启后 WSL 根本没起来。

完整链路：

```
Windows 登录 → Jarvis-Boot-WSL.vbs 拉起 WSL
             → systemd 因 Linger=yes 启动用户管理器
             → 四个 enabled 服务自动就绪
```

验证：

```bash
loginctl show-user $USER --property=Linger        # 应为 Linger=yes
systemctl --user is-enabled mosquitto-jarvis gptsovits-api tts-proxy ollama
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


---

## 七、Ollama（对话层）

```bash
bash scripts/setup_ollama.sh                       # 免 root 装到 ~/apps/ollama
cp deploy/systemd/ollama.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ollama
~/apps/ollama/bin/ollama pull qwen3:8b
```

免 root 是刻意的：官方 `install.sh` 要 sudo（装 `/usr/local/bin` + 系统级服务），
改用 release tarball 解压到用户目录。解压需要 zstd，系统没装，
但 **Python 3.14 自带 `compression.zstd`**（PEP 784），直接拿它解，省一次 apt。

### ⚠️ 必须给服务显式配代理

`registry.ollama.ai` 国内直连不通（`TLS handshake timeout`）。
`ollama pull` 只是客户端，**真正下载的是 systemd 拉起的 server**，
而它**继承不到 shell 里的代理变量** —— 日志开头那行 env dump 里
`HTTP_PROXY:` `HTTPS_PROXY:` 全是空的。

解法是 unit 里 `EnvironmentFile=-%h/apps/jarvis/config/ollama.env`，文件内容：

```
HTTPS_PROXY=http://127.0.0.1:7897
HTTP_PROXY=http://127.0.0.1:7897
NO_PROXY=localhost,127.0.0.1,192.168.0.0/16,::1
```

代理地址是机器相关的，所以不入库（前缀 `-` 表示文件缺失时 systemd 不报错）。

> 这跟 `run_gptsovits_api.sh` 里的 `LD_LIBRARY_PATH` 是同一类问题：
> **systemd 拉起的服务不继承 shell 环境**，凡是依赖环境变量的东西都要在 unit 里显式给。

### 绑定地址与其他服务不同

| 服务 | 绑定 | 原因 |
|---|---|---|
| `gptsovits-api` / `tts-proxy` | `127.0.0.1` | 调用方 HASS.Agent 在本机，mirrored 网络共享 localhost |
| **`ollama`** | **`0.0.0.0`** | **调用方是 NUC 上的 HA，跨机**，需开两层防火墙 |

```powershell
New-NetFirewallRule -DisplayName "Ollama 11434 (WSL, LAN only)" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Any -RemoteAddress LocalSubnet
New-NetFirewallHyperVRule -Name "Ollama-11434-WSL" -DisplayName "Ollama 11434 WSL" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 11434 -Action Allow
```

### 实测（RTX 5060 Ti，qwen3:8b Q4，2026-08-16）

| 指标 | 数值 |
|---|---|
| 生成速度 | **74.4 tok/s**（原假定 45，低估了 65%） |
| 首 token | 0.13–0.15s |
| prompt（83 个实体） | 2,543 token |
| prefill 首次 / 缓存命中 | **0.76s → 0.02s** |
| 端到端（含工具调用） | 0.57–1.40s |
| 显存 | 6,455 MiB |

**★ prompt 缓存是最大的意外收获。** 系统提示词不变时 Ollama 复用 KV 缓存，
prefill 几乎免费（0.02s）。这比 token 数下降更值钱，而且**正是砍暴露面换来的**——
83 个实体的状态变化频率远低于 435 个，缓存能长时间不被打穿。

⚠️ **冷启动的数字会骗人**：首次推理测出来只有 1.9 tok/s，那是 CUDA 预热被计进
`eval_duration` 的假象，热态是 74.4。测性能务必先跑一次预热。

### 工具调用能力（`scripts/ollama_ha_bench.py`）

四条典型指令，函数名和 `area`/`domain` **全对**，但**模型会凭空编造 `name`**：

```
「关掉书房的灯」   → HassTurnOff(area:书房, domain:[light], name:"台灯1")
                                                   ↑ 书房没有「台灯1」
「卧室风扇开一下」 → HassTurnOn(domain:[fan], name:"卧室风扇")
                                              ↑ 真名是「风扇  风扇」
```

`name` 填错可能让 HA 匹配失败。缓解方向：提示词里写明「只在用户点名具体设备时才填
name」。另外该测试构造的提示词**没有 `areas:` 字段**（HA 真实提示词有），
模型是纯靠设备名猜区域，真实环境准确率应更高。

### LLM 与 TTS 共存：无代价

| | 显存 | TTS 实时率 |
|---|---|---|
| LLM 已卸载 | 3,547 MiB | 6.2x |
| LLM 常驻 | 9,029 MiB | **6.3x** |

常驻在显存里不影响 TTS 吞吐，只有**同时推理**才互抢算力。
两模型共占 9G，余 7G，塞 whisper（1.6G）绰绰有余。

### 启动后应看到

```
inference compute  library=CUDA compute=12.0 driver=13.3
name=CUDA0 "NVIDIA GeForce RTX 5060 Ti" total="15.9 GiB" available="14.8 GiB"
```

## 八、HA 侧接入 Ollama

### ⚠️ 主条目 + 子条目，两步

HA 2026 的 Ollama 集成把「连接」和「对话代理」拆开了：

1. **主条目**：设置 → 设备与服务 → 添加集成 → Ollama，填 `http://<算力机IP>:11434`，
   API 密钥留空。提交成功后 `subentries=0`，**此时还没有任何 conversation 实体**
2. **子条目**：在集成页面添加「对话代理」。这一步才产生 `conversation.*` 实体

只做第一步会看到「集成已加载但实体不出现」，容易误以为失败。

用 API 直接建子条目（省去 UI 点击）：

```bash
E=<主条目的 entry_id>   # config_entries/get 里查
FID=$(curl -s --noproxy '*' -X POST -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" -d "{\"handler\":[\"$E\",\"conversation\"]}" \
  "$HA_URL/api/config/config_entries/subentries/flow" | jq -r .flow_id)
curl -s --noproxy '*' -X POST -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" -d '{
    "name":"Qwen3 对话代理", "model":"qwen3:8b",
    "llm_hass_api":["assist"], "num_ctx":8192, "max_history":5, "think":false
  }' "$HA_URL/api/config/config_entries/subentries/flow/$FID"
```

### 采用的配置

| 字段 | 值 | 依据 |
|---|---|---|
| `llm_hass_api` | `["assist"]` | 启用 HA 控制，拿到 Assist 的工具集 |
| `think` | `false` | 蓝图第 13 节「本地 LLM 关 thinking」——思考 token 念不出来，音箱会死寂 |
| `num_ctx` | 8192 | prompt 2,543 tok + 历史 + 回复，留足余量 |
| `max_history` | 5 | 限制历史轮数，防 prompt 随对话膨胀打穿缓存 |

### 端到端实测（真实管线，`/api/conversation/process`）

| 指令 | 耗时 | 结果 |
|---|---|---|
| 「书房的灯现在是开着的吗」 | 6.18s（冷） | 回答准确 |
| 「现在客厅有几盏灯亮着」 | 1.22s | 回答准确（10 盏全 off） |
| 「把书房的灯关掉」 | 1.22s | ✅ 实际 off |
| 「书房的灯再打开」 | 1.02s | ✅ 实际 on |

**稳定在 1.0–1.2 秒**，落在蓝图第 03 节给对话层的 1–3 秒预算内，且偏快的一端。
首次调用 6.18s 是模型重载，常驻后不再出现。

## 九、Wyoming TTS 封装（曼波接进 Assist）

`tts_proxy` 只是「一个能返回 wav 的 URL」，靠 `media_player.play_media` 播 ——
那条路能做主动播报，但**接不进 Assist 管线**，管线要的是一个真正的 `tts.*` 实体。

`scripts/wyoming_manbo_tts.py` 把它包成 Wyoming TTS 服务（`:10200`），
HA 的「Wyoming Protocol」集成会注册成 `tts.manbo`。

```bash
~/miniconda3/envs/GPTSoVits/bin/pip install wyoming
cp deploy/systemd/wyoming-manbo-tts.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now wyoming-manbo-tts
```

### 句级流式在这里落地

按 `。！？；` 切句、逐句合成、逐句吐 `AudioChunk`，而不是整段合成完再发。
协议层实测：

| 指标 | 数值 |
|---|---|
| 首个音频事件 | **1.02s** |
| 全部完成 | 1.86s |
| 音频 | 32000Hz / 16bit / 单声道，5.56s |

上游接的是 `tts_proxy(:9881)` 而非 `api.py(:9880)` —— 代理补了 `Content-Length`
并带磁盘缓存，固定播报只合成一次。

### 踩的坑

`global UPSTREAM` 写在 `argparse` 之后 → `SyntaxError: name 'UPSTREAM' is used
prior to global declaration`。systemd 会一直 `activating` 重试，
日志里才看得到真实原因。

## 十、faster-whisper（STT）

```bash
bash scripts/setup_whisper.sh
cp deploy/systemd/wyoming-whisper.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now wyoming-whisper
```

独立 conda 环境，不与 GPTSoVits 共用 —— faster-whisper 走 CTranslate2、
GPT-SoVITS 走 PyTorch，两者对 cuDNN/cuBLAS 的要求不一定一致。

模型选 **large-v3-turbo**：809M 参数，比 large-v3 快约 8 倍，中文质量损失很小，
fp16 约 1.6GB。**别用 base/small** —— 中文识别质量会毁掉整条链路，
前面再准后面也救不回来。

同样需要 `LD_LIBRARY_PATH`（CTranslate2 要 cuDNN/cuBLAS，pip 版 nvidia 包装在
`site-packages/nvidia/*/lib`）。

### ⚠️ 别用 hf-mirror 下模型

初版给了 `HF_ENDPOINT=https://hf-mirror.com`，结果**大文件下成、小文件全挂**：

```
model.bin                 ✅ 1.6GB 下完
config.json               ❌ 重试 30 次
preprocessor_config.json  ❌
tokenizer.json            ❌
vocabulary.json           ❌
→ huggingface_hub.errors.LocalEntryNotFoundError
```

原因是 hf-mirror 对 `HEAD` 请求返回 **308 永久重定向**回 huggingface.co，
`huggingface_hub` 的 HEAD 探测在重定向链上失败。

既然 unit 已经通过 `EnvironmentFile` 给了 Clash 代理（Ollama 那 5GB 就是这么下的），
**直连 huggingface.co 更可靠**。脚本改成只在显式设置时才用 `HF_ENDPOINT`。

⚠️ 排查时注意：模型下载发生在 **systemd 服务内部**，不是独立任务，
`du -sh ~/apps/whisper` 和服务日志是唯二的观察窗口。

### 实测（large-v3-turbo，fp16）

| 指标 | 数值 |
|---|---|
| 冷启动首次 | 0.63–6.62s（**不可信**） |
| **热态** | **0.30s / 5.56s 音频 = 实时率 18.6x** |
| 显存 | ~2.2GB |

### 领域词汇与 VAD

```
--initial-prompt "以下是智能家居的中文语音指令。常见词汇：猫砂、猫厕所、猪笼草缸…"
--vad-filter
```

`initial_prompt` 除了纠正专有名词，还能**把输出钉在简体中文上** ——
whisper 中文输出简繁不稳是老毛病。`--vad-filter`（Silero VAD）滤掉非语音段，
治 whisper 在静音上幻觉出「谢谢观看」这类 B 站字幕残留。

TTS→STT 往返实测：

```
⚠️ 猫砂 → 猫虾          ← 唯一的错，且原因在 TTS 端
✅ 猪笼草缸、水泵
✅ 玄关柜灯带、轨道射灯
```

「猫砂」和「猪笼草缸」都在 initial_prompt 里，只有前者错 —— 说明是
GPT-SoVITS 把「砂(shā)」念得接近「虾(xiā)」，whisper 忠实转录了它听到的。

⚠️ **这类闭环测试有局限**：TTS→STT 往返把两端误差混在一起，
真实准确率要对着麦克风说话才知道。

## 十一、端到端管线实测

`scripts/pipeline_bench.py` 走 HA 的 WebSocket `assist_pipeline/run`，
完整链路 音频 → STT → 对话代理 → 执行 → TTS，量每一段。
比逐个服务单测有价值 —— 单测各段都快，但管线里还有 HA 自身的调度开销。

```
输入「把书房的灯关掉」  音频 1.94s

  STT   0.23s
  LLM   1.51s
  TTS   0.00s   ← HA 立即返回 URL，实际合成在播放时才发生
  总计  1.97s   ← 不含 VAD 判定说完话的 0.5-0.8s
```

真实体感约 2.5-2.8s，落在蓝图第 03 节对话层 1-3s 预算内。

### ⚠️ 曼波音色的发音问题

两次独立测试，whisper 都把 `sh` 听成 `x`：

```
书房(shū fáng) → 修房(xiū fáng)
猫砂(shā)      → 猫虾(xiā)
```

**两个词都在 `initial_prompt` 里却依然听错**，说明不是 STT 的词汇问题，
而是音色本身的发音缺陷 —— 参考音频是日语原声，跨语种合成中文时声母系统对不上。

影响面：不只是测试，**真实播报也会受影响**。缓解方向是换中文参考音频重做 zero-shot。

⚠️ **因此 TTS→STT 闭环测试不能用来评估 STT 准确率** —— 两端误差混在一起。
真实识别率要对着麦克风说话才知道。

### 一个值得记的行为

LLM 听到不认识的「修房的灯」时**没有瞎猜**，而是回
「"修房的灯"在系统中没有被识别到，请提供更具体的信息」，灯也没被误关。
说明 `llm_hass_api` 的工具约束在起作用，比硬蒙一个实体好得多。

## ⚠️ 通用规则：冷启动数字一律不可信

三个模型服务的首次推理全部严重偏慢，实测：

| 服务 | 冷启动 | 热态 | 倍数 |
|---|---|---|---|
| Ollama | 1.9 tok/s | 74.4 tok/s | 39x |
| GPT-SoVITS | 实时率 2.1x | 6.4x | 3x |
| faster-whisper | 实时率 0.8x | 18.6x | 23x |

原因是 CUDA 图预热/模型加载被计进了推理耗时。
**测任何模型服务的性能，务必先跑一次丢弃，再测。**

### 防火墙

Wyoming 的两个端口都要开（同 MQTT / Ollama）：

```powershell
New-NetFirewallHyperVRule -Name "Wyoming-10200-WSL" -DisplayName "Wyoming TTS 10200" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 10200 -Action Allow
New-NetFirewallHyperVRule -Name "Wyoming-10300-WSL" -DisplayName "Wyoming STT 10300" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 10300 -Action Allow
New-NetFirewallRule -DisplayName "Wyoming 10200/10300 (WSL, LAN only)" -Direction Inbound -Protocol TCP -LocalPort 10200,10300 -Action Allow -Profile Any -RemoteAddress LocalSubnet
```

**症状识别**：HA 的 Wyoming 集成对话框一直转圈、而服务端日志里**没有任何连接记录**、
`ss` 里也看不到连接 —— 那是 TCP 连接被静默丢弃（防火墙 DROP），不是握手失败。
握手失败会在服务端留下日志。

## 待办：下一步

前置条件已全部就绪 —— 暴露面 435 → 83，prompt ~2,299 token，prefill 约 0.77s，
落在语音场景给 LLM 的 300ms–2s 预算内。

### 选型：Qwen3-8B Q4，不是 14B

蓝图第 03 节原定 14B，但那时没把 TTS 的显存算进去。实测：

| | 空闲 | 合成峰值 |
|---|---|---|
| gptsovits-api | 925 MiB | ~4.4 GB |

按峰值算（16 GB 卡）：

| 方案 | LLM | + whisper | 合计 | 结论 |
|---|---|---|---|---|
| **Qwen3-8B Q4** | ~6.5G | 1.6G | ~12.5G | ✅ 可共存 |
| Qwen3-14B Q4 | ~10.5G | 1.6G | ~16.5G | ❌ 超 |

领域窄（开关设备、查状态），要的是**工具调用稳、快**而非聪明，8B 绰绰有余。

### 与 TTS 的关键差异：必须绑 0.0.0.0

`gptsovits-api` / `tts-proxy` 绑回环即可，因为调用方 HASS.Agent 就在本机
（mirrored 网络共享 localhost）。**Ollama 的调用方是 NUC 上的 HA，跨机**，所以：

- `OLLAMA_HOST=0.0.0.0:11434`
- 需开两层防火墙（Windows 防火墙 + Hyper-V），与 MQTT 1883 同样处理，需管理员权限

### 步骤

1. 装 Ollama，写 systemd 用户 unit（与现有三个并列），拉 `qwen3:8b`
2. 开防火墙两条规则
3. HA 加 Ollama 集成，指向本机局域网 IP:11434
4. **开「优先本地处理命令」** —— 反射层先接，接不住才给 LLM，这是三层架构第一、二层的落点
5. **关 thinking**（蓝图第 13 节）—— Qwen3 原生支持；HA 集成是否透出开关待实测，兜底是
   system prompt 里写 `/no_think`
6. **先用 HA 的文字对话调通** —— 不需要任何语音硬件即可完整验证 LLM 那一层
7. 用真模型重跑 `scripts/tts_stream_bench.py`，把假定的 45 tok/s 换成实测值

### 已知缺口

曼波 TTS 目前**不是 HA 的 `tts.*` provider**，只是「一个能返回 wav 的 URL」，
靠 `media_player.play_media` 播。所以 Ollama 接上后**对话的语音回复接不上曼波音色**。

- 短期：Ollama 只做文字对话，语音回复暂用现有 provider
- 正解：把 GPT-SoVITS 封成 Wyoming TTS server 或自定义 TTS 集成 → 得到 `tts.manbo` 实体

### 游戏模式让路

```
HASS.Agent 上报 GPU 占用 / 活动进程（配置里加两个传感器即可）
      ↓ 超阈值
POST /api/generate  {"model":"qwen3:8b", "keep_alive": 0}   ← 立即卸载，释放 ~6.5G
      ↓
Assist pipeline 的对话代理切云端
      ↓ 游戏退出
切回本地 + 空请求预热
```
