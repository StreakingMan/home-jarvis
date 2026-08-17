# 相关研究对照

本项目各模块的实测结论,与官方(HA)、社区、学术研究的系统对照。一次全面调研沉淀(约 70 条经打开验证的来源,此处收录最有价值的部分),按模块归档;每条标注与本项目结论的关系:**印证 / 矛盾或警告 / 增量**。

**总判断**:本项目实测结论无一被推翻;官方演进方向与本项目高度共振;三处设计收到警告(已回填蓝图第 14 节);另有若干可对外输出的原创点。

---

## 一、上下文与暴露面

### 官方演进轨迹(与本宅路线同向)

- [HA LLM API 开发者文档](https://developers.home-assistant.io/docs/core/llm/) + [core llm.py 源码](https://github.com/home-assistant/core/blob/dev/homeassistant/components/homeassistant/llm.py)——**印证**:官方清单就是扁平实体 YAML(`names`/`domain`/`areas`/少量 attributes),不含 state、不含 entity_id、无 device 层;device 层级(child devices)只进 registry 不进 prompt
- [PR #141034](https://github.com/home-assistant/core/pull/141034) 把 state 剥出 prompt 改 `GetLiveContext`,token 降 49%——**印证**:官方选的正是「半惰性」(清单常驻、状态惰性),与本宅 R7 一致
- [PR #154561](https://github.com/home-assistant/core/pull/154561) balloob 回滚「把 entity_id 给 LLM」:"It adds too much data, hurting general performance"——**强印证**:官方用数据裁决了「LLM 以友好名寻址、token 预算优先」
- [PR #152408](https://github.com/home-assistant/core/pull/152408) GetDateTime 把日期移出 prompt 吃 KV 前缀缓存,Ollama qwen3:4b 延迟 500ms→100ms;配套 commit 给别名排序求字节级稳定——**增量**:官方优化重心已从「压 token」转向「prompt 前缀稳定化」,本宅应确认 HA 版本已含这批改动
- [PR #142873](https://github.com/home-assistant/core/pull/142873) 去状态化后模型不主动调 GetLiveContext,靠提示词硬修——**印证**:半惰性对模型指令跟随敏感,是本宅否决全惰性的同一风险
- [org Discussion #500](https://github.com/orgs/home-assistant/discussions/500) 社区求实体 description 字段供 LLM 用,官方未接——现状仍是「名字承载全部语义」

### 社区替代路线(均已考察)

- [MCP Assist](https://community.home-assistant.io/t/mcp-assist-95-token-reduction-for-voice-assistants-with-local-cloud-llms/977977) 全惰性检索——已否决,见 exposure-policy.md
- [home-llm 清单格式](https://github.com/acon96/home-llm/blob/develop/docs/Model%20Prompting.md)——**矛盾点**:它暴露 entity_id 且按区域分组,与官方相反;因为它靠微调模型兜底格式。其「Minimal/Reduced/Full 三档工具定义」和 **ICL 例子注入**(prompt 里放 3–5 条工具调用示范)是可借鉴增量——注意与「具体措辞会被照抄」的实测教训权衡,示范须多样化
- [JohnTheNerd RAG prompt 生成器](https://github.com/JohnTheNerd/homeassistant-llm-prompt-generator)(已归档)——embedding 检索命中区域再注入,介于常驻与全惰性之间;其归档说明维护成本高

### 空档(本宅可对外输出)

学术界**没有**「实体命名/表示格式 → 工具调用准确率」的系统消融;官方只有定性建议。本宅「风扇␠␠风扇 0/6 → 卧室风扇 4/4」「每实体 ~32 token」的实测数据有分享价值。

---

## 二、小模型能力边界与评测

### 能力边界(印证「8B 上限」判断)

- [HomeBench(ACL 2025)](https://arxiv.org/abs/2505.19628)——13 模型评测:Qwen2.5-7B 整体成功率仅 **11%**,GPT-4o 也只 48%,且在「多设备+无效指令」场景 **0%**;ICL/RAG/微调均不足以根治。其「有效/无效指令」设计值得抄:本宅评测集缺「该拒不拒」类负例
- [SmartBench](https://arxiv.org/abs/2603.06636)——量化 8B 幻觉不存在设备的现象;[SimuHome](https://arxiv.org/pdf/2509.24282) 把「先查状态再行动」单列为难点——正是本宅「有点冷」实测的学术版
- [TinyLLM](https://arxiv.org/abs/2511.22138)——边缘小模型多轮/多步是系统性短板,最佳混合优化也只 55.6%(多轮)
- BFCL 摘录(置信度中等,[来源](https://arxiv.org/pdf/2601.15625)):Qwen3-4B ≈40.9%、8B ≈46.8%;BFCL v4 把「幻觉/该拒绝不调用」单列为 10% 权重考项——「谎报执行」已被主流 benchmark 制度化

### 提示词长度 vs 服从度(印证,且有正式术语)

- [Prompt Design at Scale](https://arxiv.org/abs/2607.19257)——指令数增大,完美服从率崩塌(N=80 全模型归零)。**差异点**:它测得大模型接近极限倾向拒答,本宅实测 8B 是**编造**(谎报执行)——小模型失败模式更恶性,这是本宅的原创观察
- [Same Task, More Tokens(ACL 2024)](https://arxiv.org/abs/2402.14848)——退化远早于标称窗口(社区常引 ~3000 token 起);关键约束靠近末尾损害更小——印证「首尾各说一遍」
- 该现象的检索关键词:**context rot / instruction adherence degradation**([综述](https://www.emergentmind.com/topics/context-degradation-in-large-language-models))
- [Brief Is Better](https://arxiv.org/pdf/2604.02155)——函数调用场景思维链预算非单调,更长不一定更好——支持「关 thinking」
- [Tool Hallucination / Reliability Alignment](https://arxiv.org/pdf/2412.04141)——「谎报执行成功」属工具幻觉分类学,已有对齐层解法研究

### 约束解码(增量,可落地)

- [XGrammar-2 系](https://arxiv.org/pdf/2601.04426)——语法约束把 8B 函数调用 59%→81%,schema 有效性 100%;但**只治格式幻觉,不治选错设备**
- [Let Me Speak Freely?](https://arxiv.org/abs/2408.02442)——格式约束会伤推理,越紧越伤;[DOMINO](https://arxiv.org/abs/2403.06988)——朴素约束解码若不与子词词表对齐会损准确率
- [Ollama Structured Outputs](https://ollama.com/blog/structured-outputs)——`format` 传 JSON schema 即可。**落地建议**:开 schema 约束,把评测失败归因收敛到纯语义错误;但对推理型回答慎用

### 评测与微调(增量)

- [OHF-Voice/intents](https://github.com/OHF-Voice/intents)——官方「输入句→期望意图+槽位」测试 YAML(含中文),可批量转成 `assist_pipeline/run` 用例扩充评测集。注意它只覆盖模板匹配层;**HA 官方没有 LLM 层评测集,本宅自建评测走真实管线属领先实践**
- [acon96 Home-3B-v3](https://huggingface.co/acon96/Home-3B-v3-GGUF)——窄域微调 1–3B 在自建测试集 97%+(对照 HomeBench 通用 7B 的 11%);证明「提示词到顶后微调」路线性价比,呼应 model-tuning.md;但同分布测试集,无效指令/泛化未验证

---

## 三、抽象指令与规划(对照蓝图第 04 节四类分诊)

- [AdaHome](https://arxiv.org/abs/2607.18034)——**与分诊思想几乎同构**:本地小模型 + 意图感知路由,简单指令直出、模糊指令才分配推理预算;直接指令 86.7%、延迟降 3 倍。最值得精读的一篇
- [Sasha(IMWUT 2024)](https://arxiv.org/abs/2305.09802)——"make it cozy" 类目标态指令的在线迭代推理管线;揭示裸 LLM 幻觉计划等失败模式——**其失败模式正是本宅离线化路线的动机**;学术界对「有点冷」类的标准术语:underspecified command
- [SAGE](https://arxiv.org/abs/2311.00772)——重型在线 agent 50 任务 75%(基线 30%),代价是多轮 LLM 调用——证明重推理路线存在但本地小模型走不了,支持把它放异步层
- [MiCU(小米,已上线米家)](https://arxiv.org/abs/2606.01099)——用日志+LLM 合成数据把模糊指令能力蒸馏进领域模型——「智能离线积累」的工业同构,只是积累到权重而非 scene
- [AwareAuto](https://arxiv.org/abs/2408.12687)——两步 LLM 把模糊语言编译成多分支自动化,91.7%,强调人在回路——④类(条件时序)的编译先例
- [HA intents #1669](https://github.com/home-assistant/intents/discussions/1669)——证实「除了 X 都关」不在内建意图;Alexa/Google 亦无 except 子句,业界事实标准就是**预建 group**——③类判断与现状吻合
- 「反思离线编译」的学术脉络:[CASAS](https://casas.wsu.edu)(前 LLM 时代已验证习惯可挖掘并编译为自动化)+ [RecRules](https://dl.acm.org/doi/abs/10.1145/3344211)(规则推荐→用户采纳)+ [LLM-Enhanced Logs](https://arxiv.org/abs/2412.12653)(LLM 语义化日志做行为预测)。**组合成统一分诊器+夜间反思闭环的完整系统未见先例**

---

## 四、主动性与习惯学习(对照蓝图第 14 节)

### 印证

- [Opportune Moments(IMWUT 2020)](https://dl.acm.org/doi/10.1145/3411810)——40 人实地实验:在场与忙碌度是可打扰性核心因子——「在场判定+静默窗口」有直接依据;可补充维度:情绪/忙碌度(本宅暂无)
- [May I Interrupt?(CUI 2021)](https://dl.acm.org/doi/10.1145/3469595.3469629)——用户对主动音箱意见严重分化,明确担心打扰疲劳、要求频率可控——支持「预算+降级链」,且提示预算应可按人调
- [RecSys 2023(Google)](https://arxiv.org/abs/2308.12256)——工业推荐系统的 not-to-recommend 损失与负反馈响应度度量——「忽略 3 次→静音」是其硬阈值实现
- [ai_automation_suggester](https://github.com/ITSpecialist111/ai_automation_suggester)(765★)——「建议须人工确认」同构,其 accepted/declined/dismissed 数据结构可参考;但它不读历史日志不学习行为——**反思闭环仍是差异化空间**
- [HA Roadmap 2025](https://www.home-assistant.io/blog/2025/05/09/roadmap-2025h1/)——官方因隐私立场明确不做个人日志学习,走 Device Database 集体智能——**官方短期不会有此能力,自建有长期价值**

### 警告(已回填蓝图 14.1/14.2/14.3)

- [业界通知速率经验](https://notigrid.com/blog/notification-rate-limiting-alert-fatigue)——每日 3–5 条是用户关通知的临界点;本宅 suggest+ask=5 已顶上限,建议砍半起步。学术界无正式量化研究——分类型每日预算是本宅稀少的原创点
- [Multi-Granular Negative Feedback](https://arxiv.org/html/2511.18700v1)——不理解拒绝原因就压制同类,泛化差——粗粒度永久负向清单有过度压制风险,归档条目应记录拒绝时的上下文/原因
- [Lally 2010](https://onlinelibrary.wiley.com/doi/10.1002/ejsp.674)——人类习惯固化中位数 66 天(个体 18–254 天)——「两周+10 次」作检测门槛可行,但措辞应叫「疑似规律」而非「习惯已形成」

### 习惯挖掘方法参照

- [DISCOVER](https://arxiv.org/abs/2503.01733)(自监督聚类发现日常模式,0.01% 标签)、[FP-Growth 时序关联](https://pmc.ncbi.nlm.nih.gov/articles/PMC11991222/)(CASAS 上 84.8%)——「分类+频繁模式挖掘」两段式与「Recorder→夜间反思」同构;「多少次算习惯」文献中普遍是可调参数,无公认数字

---

## 五、中文唤醒词

- [openWakeWord Discussion #52](https://github.com/dscripka/openWakeWord/discussions/52)——维护者:非英语瓶颈在缺多说话人 TTS 声源 + Google 预训练音频嵌入模型可能只见过英语(域偏移)
- [社区多语言唤醒词帖](https://community.home-assistant.io/t/custom-non-english-wake-words-for-home-assistant-93-recall-fully-offline-onnx-100-kb/1021320)——作者实测 openWakeWord 训中文「根本不行」,自建管线达 90%+ 召回并开源推理端
- **对本宅的含义**:「你好曼波」已跑通,但与社区共识存在张力——应系统实测误唤醒率与远场召回,别等实用中才发现;若效果不达标,该帖方案是备选

---

## 可落地增量清单(按性价比排序)

1. **确认 HA 版本含 prompt 前缀稳定化改动**(GetDateTime 外移、别名排序)——官方实测 5 倍延迟差,可能零成本
2. **Ollama 开 JSON schema 约束**——治格式幻觉,让评测失败归因收敛到语义层
3. **评测集补「无效指令」负例**(HomeBench 设计)+ 用 OHF-Voice/intents 中文语料批量扩容
4. **实测唤醒词误唤醒率与远场召回**
5. **试 ICL 例子注入**(home-llm 式,3–5 条多样化示范)
6. 微调作为提示词到顶后的备胎(acon96 路线,已在 model-tuning.md 论证)

## 本宅可对外输出的原创点

1. 命名→工具调用成败的系统实测(学术无消融、官方仅定性)
2. 四类分诊 + 夜间反思离线编译的完整闭环(AdaHome+CASAS+RecRules 交集,无先例系统)
3. 分类型每日打扰预算 + 话题衰减的具体机制(无量化研究先例)
4. 走真实管线的 LLM 层评测集(HA 官方测试只覆盖模板层)
5. 小模型「接近极限时编造而非拒答」的失败模式观察
