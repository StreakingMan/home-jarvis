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
- 算力机已跑：GPT-SoVITS 曼波音色 TTS（:9880 + 缓冲代理 :9881）、Mosquitto broker（:1883）、HASS.Agent；详见 `deploy/README.md`
- 路由器：小米 BE6500 Pro（RD08）。**NUC 只是拉不到 Docker Hub**（DNS 污染，每次返回不同的假 IP）；**ghcr.io 完全可用**，所以社区加载项（hassio-addons / ESPHome / Music Assistant）能正常装，只有官方加载项装不了。细节与试过的无效方案见 `deploy/README.md` 附录
- 三只猫：哦多茄、雕猫、妹妹；Petkit 猫厕所 MAX / MAX PRO 2；米家喂食器×2、无线饮水机
- 拓竹 P1S、美的空调（LAN）、小米人在传感器 Pro、闲置小爱×2、闲置 iPad
- 约 2806 实体 / 115 设备，已隐藏 1519 个噪音实体；12 台离线设备待确认删除

## 硬性约束

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
- `docs/memory-snapshot.md` — 项目记忆快照
- 在线版蓝图为私有 Artifact，未随公开仓库发布
