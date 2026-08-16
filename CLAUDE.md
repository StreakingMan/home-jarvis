# home-jarvis 工作上下文

本仓库记录「贪玩兰粤」智能家居向家庭贾维斯演进的探索与实现。此文件供 Claude Code 跨机器携带上下文——新环境打开即可续接工作。

**仓库定位**：主要用于经 HA MCP 做实验和沉淀记录；不通过 Claude 直接控制家中设备（设备控制交给 HA 自身的语音/自动化体系）。

**脱敏基线（公开仓库）**：令牌、设备序列号、内网 IP、设备云 ID 均不入库。序列号经 `.env` 的 `P1S_SERIAL` 注入脚本；文档/脚本中所有 `*_cn_9000000xx`、`blt_3_anonbledevxx`、`group_9000...` 形态的实体 ID **均为假名**，仅示意结构——实际操作时一律按实体友好名称在 HA 中检索真实 ID。

## 接入方式

- HA 实例：`http://homeassistant.local`（**80 端口**，非 8123）；备用固定 IP 不入库，见本机 `.env` 的 `HA_URL` 或路由器 DHCP 保留
- 令牌在项目根 `.env`（`HA_URL` / `HA_TOKEN`，已 gitignore，新机器需手动创建）
- `./ha.sh`：REST 快捷工具（`states` / `state <id>` / `call <domain> <service> '<json>'` / `services` / `get <path>`）
- HA 官方 MCP Server 已启用（SSE `/mcp_server/sse`）；新机器上用 `claude mcp add` 注册
- 该令牌对 REST 的 `/api/hassio/*` 返回 401，但**走 WebSocket 的 `supervisor/api` 命令可读可写**（`scripts/supervisor_ws.py`）：读 DNS/系统信息、装卸载与启停加载项都能做。日志类端点是纯文本，WS 代理接不住
- 仪表板等 UI 操作优先走 WebSocket API（参考 `scripts/create_dashboard.py`），免 SSH 免重启

## 家庭事实

- 宿主机 NucBox G3 Plus（N150，无头）；算力机 14600KF + 5060 Ti 16G（Windows + WSL2，兼职游戏）
- 算力机已跑：GPT-SoVITS 曼波音色 TTS（:9880 + 缓冲代理 :9881）、Mosquitto broker（:1883）、Ollama（:11434）、Wyoming whisper（:10300）/ manbo TTS（:10200）/ **sherpa-onnx KWS（:10400，唤醒词「你好曼波」）** / satellite（:10700）、HASS.Agent；详见 `deploy/README.md`
- 唤醒词走 sherpa-onnx **开放词表** KWS，不是自训模型：换唤醒词只改 `run_kws.sh` 一行，不用训练。内部是带调拼音（`n ǐ h ǎo m àn b ō`）不是汉字。openWakeWord 那条路已废弃
- **算力机 24/7 常开**（不只是游戏机），所以唤醒词这类常驻职责可以放在它上面
- 路由器：小米 BE6500 Pro（RD08）。**NUC 只是拉不到 Docker Hub**（DNS 污染，每次返回不同的假 IP）；**ghcr.io 完全可用**，所以社区加载项（hassio-addons / ESPHome / Music Assistant）能正常装，只有官方加载项装不了。细节与试过的无效方案见 `deploy/README.md` 附录
- 三只猫：哦多茄、雕猫、妹妹；Petkit 猫厕所 MAX / MAX PRO 2；米家喂食器×2、无线饮水机
- 拓竹 P1S、美的空调（LAN）、小米人在传感器 Pro、闲置小爱×2、闲置 iPad
- 约 2806 实体 / 115 设备，已隐藏 1519 个噪音实体；12 台离线设备待确认删除

## 硬性约束

- **绝不拿卧室设备做测试**：家人常在卧室休息。评测集、调试脚本、工具调用验证一律用书房；`scripts/eval_cases.yaml` 已写死这条
- **植物灯保持常开**：批量关灯时排除「猪笼草缸 灯」和「盆栽射灯」两个实体（按名称在 HA 中检索；本仓库实体 ID 均已假名化，勿直接引用）；误关立即恢复
- 不读写 `.env` 内容到任何文档/输出；密钥不入库
- 敏感设备（门锁类）不暴露给任何 LLM/Agent；Agent 动作先确认

## 关键决策（详见 docs/blueprint.md 第 13 节）

语音三层架构（本地意图→Assist+本地 LLM→异步 Agent）；本地 LLM 关 thinking；固定模糊词用别名；认知层 OpenClaw 经 MCP 接入不替代语音管线；习惯→自动化必须人工确认；游戏模式经 HASS.Agent 卸载本地 LLM 切云端管线。

认知层框架约束（第 14 节）：主动动作一律经打扰闸门 `script.jarvis_speak`，不许直接调 announce/start_conversation；习惯档案带状态与命中/否决计数，60 条硬上限；拒绝写回档案且永不删除；记忆与反思留 HA 侧（24/7 职责不依赖会关机的 GPU 机）。**反思的数据边界未决**（云端/本地/脱敏三选一，见 14.5）。

## 文档地图

- `docs/blueprint.md` — 架构蓝图主文档（14 节）
- `deploy/README.md` — **语音栈部署实录**（曼波 TTS / Mosquitto / HASS.Agent，含四个静默失败的坑）
- `docs/session-2026-08-15.md` — 探索过程问答归档
- `docs/voice-tuning.md` — **语音层调优**（提示词工程 + 实体命名；含「命名错了设备就控制不了」的实测，社区无成体系资料）
- `prompts/v7..v12.txt` — 系统提示词各版本；用 `scripts/set_prompt.py --file` 写入（Ollama 把提示词放在 **subentry** 里，只能走 reconfigure flow）
- `scripts/run_eval.py` + `scripts/eval_cases.yaml` — **语音评测集**（20 条，每条跑 3 遍报通过率）。改提示词前后各跑一次看回归
- `scripts/eval_kws.py` — **唤醒词评测**（168 正 / 616 对抗样本，扫 threshold × score）。换唤醒词或调旋钮前后各跑一次；`scripts/smoke_kws.py` 是不依赖麦克风的端到端冒烟测试
- `docs/model-tuning.md` — **模型调优决策**（提示词 vs 微调 vs 评测集：什么时候该动哪个杠杆）
- `docs/exposure-policy.md` — **实体暴露策略**（该不该暴露给 LLM 的判定规则，含官方默认逻辑与 8 条补充规则）
- `docs/related-work.md` — **相关研究对照**（结论 vs 官方/社区/学术；含三处设计警告与可落地增量清单）
- `docs/progress.md` — **进展时间线**（约定：日期只出现在这个文件；知识按模块沉淀到各文档，不用日期组织）
- `docs/memory-snapshot.md` — 项目记忆快照
- 在线版蓝图为私有 Artifact，未随公开仓库发布
