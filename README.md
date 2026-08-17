# home-jarvis

把一套 Home Assistant 智能家居（贪玩兰粤）一步步改造成「家庭贾维斯」的探索记录与实现——语音 AI 分层架构、主动式助理、本地大模型算力、认知层 Agent。目标是沉淀出可供他人参考的完整路径：**每个决策都有理由，每个实现都有脚本，每个坑都有记录**。

文档按模块归档（面向日后整理成教程/指南）；时间线单独放在 [`docs/progress.md`](docs/progress.md)。

## 架构一览

```
用户语音 ──► 反射层（HA 内建意图，~0.2s）
                │ 未命中
                ▼
            对话层（Assist + 本地 Qwen3-8B，1–2s）
                │ 深活 / 长任务
                ▼
            认知层（异步 Agent，分钟级，经 MCP 接入）
```

- **底座**：HAOS @ NucBox G3 Plus（N150，无头，24/7）
- **算力**：14600KF + 5060 Ti 16G（Windows + WSL2，兼职游戏），跑 STT / LLM / TTS / 唤醒词
- **设备面**：约 2806 实体 / 115 设备，小米生态为主 + Petkit 双猫厕所（三只猫）+ 拓竹 P1S

## 文档地图（按模块）

### 架构总纲

| 路径 | 内容 |
|---|---|
| [`docs/blueprint.md`](docs/blueprint.md) | **主文档**：14 节架构蓝图 + 关键决策记录（含否决的方案和理由） |
| [`docs/related-work.md`](docs/related-work.md) | **相关研究对照**：各模块结论 vs 官方/社区/学术，含警告、可落地增量、原创点 |

### 语音层

| 路径 | 内容 |
|---|---|
| [`docs/voice-tuning.md`](docs/voice-tuning.md) | 提示词工程（16 版迭代实录）+ 给 AI 用的实体命名规则 |
| [`docs/exposure-policy.md`](docs/exposure-policy.md) | 实体暴露策略：判定规则、模型容量上限、否决过的替代方案 |
| [`docs/model-tuning.md`](docs/model-tuning.md) | 调优决策：提示词 vs 微调 vs 评测集，什么时候动哪个杠杆 |
| [`prompts/`](prompts/) | 系统提示词各版本，用 `scripts/set_prompt.py` 写入 |
| `scripts/run_eval.py` + `eval_cases.yaml` | 语音评测集：20 条断言走真实管线，改动前后各跑一次看回归 |

### 部署与运维

| 路径 | 内容 |
|---|---|
| [`deploy/README.md`](deploy/README.md) | 语音栈部署实录：TTS / MQTT / HASS.Agent，含静默失败的坑 |
| [`scripts/`](scripts/) | 暴露面治理、唤醒词训练、仪表板等（HA WebSocket API 免重启；实体 ID 已假名化） |
| `ha.sh` | HA REST 快捷工具（states / call / services） |

### 认知层

规划见 [`docs/blueprint.md`](docs/blueprint.md) 第 08 / 14 节（打扰闸门、习惯档案、夜间反思、OpenClaw 接入）；尚未动工，落地后立独立文档。

### 进展与归档

| 路径 | 内容 |
|---|---|
| [`docs/progress.md`](docs/progress.md) | **进展时间线**（日期只出现在这里） |
| [`docs/session-2026-08-15.md`](docs/session-2026-08-15.md) | 早期探索的问答归档 |
| [`docs/memory-snapshot.md`](docs/memory-snapshot.md) | AI 助手项目记忆快照（跨机器携带上下文） |
| `CLAUDE.md` | 给 Claude Code 的工作上下文（换机器开箱即用） |

## 快速开始（新机器）

1. 克隆本仓库，在根目录创建 `.env`（不入库）：
   ```
   HA_URL=http://homeassistant.local
   HA_TOKEN=<你的长期令牌>
   P1S_SERIAL=<拓竹P1S序列号>   # scripts/ 里的仪表板脚本需要
   ```
2. `./ha.sh states` 验证连通。
3. 用 Claude Code 打开本目录——`CLAUDE.md` 会自动加载全部上下文，可直接续接工作。

## 路线图

- [x] 实体治理与仪表板
- [x] 阶段一：语音栈跑通（STT / LLM / TTS / 唤醒词「你好曼波」）、Assist 管线本地优先、Expose 白名单（435→83）、提示词 + 评测集
- [ ] 阶段二：Voice PE 进屋、小爱播报出口、主动问询自动化
- [ ] 阶段三：AI Task 晨间简报、习惯档案与夜间反思循环、游戏模式自动化（含云端兜底降级链）
- [ ] 阶段四：OpenClaw 认知层、iPad 中控屏、LLM Vision 看猫
- [ ] 零散待办：暴露集合改名（83 个）、房间级灯组（exposure-policy R8）、MQTT broker 迁回 NUC、ESP32-S3 套件改书房桌面卫星（+曼波玩偶外壳，见蓝图第 09 节）、HA 上 HTTPS（浏览器端卫星的前置闸门，见蓝图第 11 节）、确认闲置小爱型号（LX04 则刷机复活为播报点）

详见 [`docs/blueprint.md` 第 12 节](docs/blueprint.md#12-落地路线图)。
