# 项目记忆快照 · 2026-08-15

> Claude Code 项目记忆的完整落地副本。原件在本机 `~/.claude/projects/` 下，按项目路径隔离；此快照使记忆可随仓库跨机器携带。换机器后，新会话可读本文件恢复全部背景。
>
> **注意**：本文件中的实体 ID 已做公开脱敏（假名化），与真实 ID 不对应；实际操作按实体友好名称检索。真实记忆原件仍在本机。

## haos-setup — HAOS 实例与接入

- HAOS 跑在 **80 端口**（非 8123）：`http://homeassistant.local/`，备用固定 IP 见 `.env`/路由器。长期令牌存在项目根 `.env`（`HA_TOKEN`），管理员级，但 Supervisor 代理 `/api/hassio/*` 对该令牌返回 401（装 Add-on 需走 UI）。
- 项目根有 `ha.sh`（REST 快捷工具：states/state/call/services/get）。
- 已启用 HA 官方内置 **MCP Server 集成**（2026-08-14 创建，entry_id <entry_id>），SSE 端点 `/mcp_server/sse`，已用 `claude mcp add`（local scope）注册为 `homeassistant`。
- 已装 Claude Code 插件 `home-assistant-manager@claude-skill-homeassistant`（komal-SkyNET，user scope）。
- 已建自定义仪表板 **「贪玩兰粤」**（url_path `tanwan-lanyue`，2026-08-15，iOS 风格 sections 视图）。创建走 WebSocket API（`lovelace/dashboards/create` + `lovelace/config/save`，无需 SSH/重启）；侧边栏排序用 `frontend/set_user_data` key `sidebar`（仅对令牌所属用户生效）。
- 实例约 2806 实体 / 115 设备，小米生态为主（Xiaomi Home 集成），另有 Bambu Lab P1S、Petkit（3 只猫：哦多茄、雕猫、妹妹）、Midea AC LAN、HACS。
- 宿主机：GMKtec NucBox G3 Plus（Intel N150），计划无头运行。

## entity-cleanup — 实体清理进度

- 实例约 2806 个实体，绝大部分来自 xiaomi_home 集成（每设备生成几十个 MIoT 属性实体）。
- 2026-08-14 已完成：通过 WebSocket API（`config/entity_registry/update`）批量将六类噪音域全部设为 `hidden_by: user`，共 **1519 个**：event 293 / button 371 / notify 118 / number 398 / select 271 / text 68。
- 待办：
  - 12 台全离线设备（扫地机 M30 Pro 183 实体、打印机 58、lumi 窗帘 41、电热水瓶等，共 459 实体）——需用户确认哪些已弃用后删除。
  - 用户计划在 xiaomi_home 集成选项里关闭这些实体类型的转换（从源头减量）。注意：若删除注册表残留后又重新勾选，隐藏标记会丢失需重新隐藏。

## plant-lights-stay-on — 植物灯保持常开（用户反馈）

- 用户要求：植物相关的灯要一直开着，批量关灯时不要关它们。
- 已知植物灯实体：
  - 猪笼草缸 灯 `light.hfjh_cn_9000000001_m100_s_15_light`（客厅）
  - 盆栽射灯 `light.lemesh_cn_9000000002_wy0d02_s_2_light`（阳台）
- **Why**：植物需要照明，批量按区域关灯（如关客厅所有灯）会误关猪笼草缸灯。
- **How to apply**：执行「关某区域/全部灯」时排除以上实体；若误关需立即恢复。

## dining-pendant-main-light — 餐厅吊灯是主灯（用户反馈）

- 餐厅吊灯是餐厅的**主灯**。设备有两个实体：
  - `light.leishi_cn_9000000003_eps126_s_2_light`（餐厅吊灯 灯）= 主照明
  - 另一个「餐厅吊灯 氛围灯」实体只是附属氛围光，不要把整个吊灯当成氛围灯
- **Why**：曾把吊灯误称为氛围灯，用户纠正过。
- **How to apply**：用户在餐厅需要照明（如吃饭）时，开「餐厅吊灯 灯」这个主灯实体。

## jarvis-blueprint — 贾维斯方案

- 完整方案文档：本仓库 `docs/blueprint.md`（在线版为私有 Artifact，未随公开仓库发布）
- 核心决策速记：语音三层架构（HA 本地意图 → Assist+本地 LLM → 异步 Agent）；本地 LLM 关 thinking；固定模糊词用别名/动态歧义给 LLM；认知层用 OpenClaw 经 MCP 接入（不替代语音管线）；习惯→自动化必须人工确认；GPU 机（14600KF+5060Ti 16G，Windows+WSL2 因要打游戏）同机跑 STT/LLM/TTS+Agent，游戏模式经 HASS.Agent 自动卸载本地 LLM 切云端；iPad 只当中控屏（Kiosker+browser_mod），Voice PE 当耳嘴；小爱只做 play_text 播报出口。
