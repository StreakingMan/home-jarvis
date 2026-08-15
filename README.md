# home-jarvis

把一套 Home Assistant 智能家居（贪玩兰粤）一步步改造成「家庭贾维斯」的探索记录与实现——语音 AI 分层架构、主动式助理、本地大模型算力、认知层 Agent。目标是沉淀出可供他人参考的完整路径：每个决策都有理由，每个实现都有脚本。

## 现状（2026-08-15）

- **底座**：HAOS @ NucBox G3 Plus（N150，无头），约 2806 实体 / 115 设备，小米生态为主 + Petkit 双猫厕所（三只猫）+ 拓竹 P1S
- **已完成**：实体治理（隐藏 1519 噪音实体）、MCP Server 接入、单屏 iOS 风仪表板「贪玩兰粤」
- **规划中**：三层语音架构（本地意图 / Assist+本地 LLM / 异步认知 Agent），算力在 14600KF + 5060 Ti

## 目录

| 路径 | 内容 |
|---|---|
| [`docs/blueprint.md`](docs/blueprint.md) | **主文档**：完整架构蓝图与 13 节决策记录 |
| [`docs/session-2026-08-15.md`](docs/session-2026-08-15.md) | 探索过程归档：20 个问题的问答脉络 |
| [`docs/memory-snapshot.md`](docs/memory-snapshot.md) | AI 助手项目记忆快照（跨机器携带上下文） |
| [`scripts/`](scripts/) | 仪表板创建/更新脚本（HA WebSocket API，免重启；实体 ID 已假名化） |
| `ha.sh` | HA REST 快捷工具（states / call / services） |
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
- [ ] 阶段一：Assist 管线（本地优先+云端兜底）、Expose 白名单、别名工程
- [ ] 阶段二：Voice PE 进屋、小爱播报出口、主动问询自动化
- [ ] 阶段三：AI Task 晨间简报、习惯档案与夜间反思循环、游戏模式自动化
- [ ] 阶段四：OpenClaw 认知层、iPad 中控屏、LLM Vision 看猫

详见 [`docs/blueprint.md` 第 12 节](docs/blueprint.md#12-落地路线图)。
