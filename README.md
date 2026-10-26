# 智慧种植智能顾问（ReAct 智能体）

基于 LangChain ReAct 架构的家庭园艺与种植智能顾问，实现思考 -> 行动 -> 观察的自主推理闭环，覆盖种植知识问答、浇水/施肥/打药/防冻等天气适配建议、病虫害防治与个性化月度种植报告四类场景。

## 功能

- 基于 `create_agent` 构建 ReAct 智能体，集成 7 类工具（知识库检索、真实天气查询、用户 ID、当前月份、种植日志、报告上下文注入、记录保存），支持多轮记忆与多工具协同
- 基于中间件实现工具调用监控（`wrap_tool_call`）、模型调用日志（`before_model`）与动态提示词切换（`dynamic_prompt`），在种植咨询与报告生成两种模式间自动切换
- 构建 Chroma + text-embedding-v3 的 RAG 检索链路，知识库覆盖蔬菜/果树/花卉的种植习性、水肥管理、病虫害防治、修剪繁殖等文档，支持 PDF/TXT 加载与 MD5 增量去重
- 采用抽象工厂 + YAML 配置中心管理模型与向量化参数，支持流式输出；多轮记忆靠每轮把完整对话历史重新喂给无状态的 LLM 实现；报告场景自动生成 Markdown 月度种植养护报告与建议
- 基于 Streamlit 的 Web 对话界面，侧边栏注册/登录，聊天按当前 user_id 存取个人种植日志

## 目录结构

```
Agent智能体/
├── app.py                  # 入口：Streamlit 对话界面
├── requirements.txt        # 依赖清单
├── agent/
│   ├── react_agent.py      # ReAct 智能体：模型 + 7 工具 + 3 中间件
│   └── tools/
│       ├── agent_tools.py  # 7 类工具：知识检索 / 天气 / 用户ID / 月份 / 种植日志 / 报告注入 / 记录保存
│       ├── weather_tool.py # 真实天气：Open-Meteo 地理编码 + 实时/预报
│       └── middleware.py   # 3 个中间件：工具监控 / 模型日志 / 动态提示词切换
├── rag/
│   ├── rag_service.py      # 检索 -> 拼上下文 -> 模型总结
│   └── vector_store.py     # Chroma 封装 + PDF/TXT 入库（MD5 去重）
├── model/
│   └── factory.py          # 抽象工厂：模型 / 向量化
├── config/                 # YAML 配置（rag / chroma / prompts / agent）
├── prompt/                 # 提示词（main / rag_summarize / report）
├── data/                   # 知识库文档（种植/园艺）
└── utils/                  # 配置加载 / 文件 / 日志 / 路径 / 提示词加载 / SQLite

运行时产物（已 gitignore）：.env、logs/、chroma_db/、md5.text
```

## 调用流程

```
用户提问
  -> app.py（Streamlit 入口，传完整对话历史实现多轮记忆）
  -> ReactAgent.execute_stream（agent/react_agent.py，stream_mode="values" 流式）
       ReAct 循环：思考 -> 调用工具 -> 观察（最多 5 次）
         ├─ 知识问答  rag_summarize -> Chroma 检索(k=3) -> 模型总结
         ├─ 天气适配  get_weather -> Open-Meteo 真实接口（先问城市再查）
         └─ 报告生成  get_user_id -> get_current_month -> fill_context_for_report -> fetch_external_data
       中间件（横切）：
         ├─ monitor_tool（@wrap_tool_call）-> 记录工具调用，识别报告场景
         └─ report_prompt_switch（@dynamic_prompt）-> 报告场景切换提示词
  -> 流式返回前端，Streamlit 渲染
```

## 动态提示词切换（种植咨询 / 报告）

这是项目最核心的机制：同一个 Agent 里，种植咨询和报告生成用不同的系统提示词，靠一个运行时状态 `context.report` 在两个中间件之间传递——`@dynamic_prompt` 负责**读**、`@wrap_tool_call` 负责**写**：

```mermaid
flowchart TD
    A[用户提问] --> B["每次模型调用前<br/>report_prompt_switch 读 context.report"]
    B --> C{"context.report<br/>是否为 true？"}
    C -->|否| D["种植咨询提示词<br/>main_prompt.txt"]
    C -->|是| E["报告提示词<br/>report_prompt.txt"]
    D --> F[模型 ReAct 推理 -> 调用工具]
    E --> F
    F --> G["工具执行 monitor_tool<br/>若调用 fill_context_for_report<br/>则置 context.report = true"]
    G --> B
```

**流程说明**：

1. 每次模型调用前，`report_prompt_switch`（`@dynamic_prompt`）读 `context.report` 决定用哪套提示词，默认 `false` -> 种植咨询提示词；
2. 用户要生成报告时，种植咨询提示词里的报告生成强约束引导模型依次调用 `get_user_id -> get_current_month -> fill_context_for_report -> fetch_external_data`；
3. 当 `fill_context_for_report` 被调用，`monitor_tool`（`@wrap_tool_call`）把 `context.report` 置为 `true`；
4. 下一轮模型调用自动切到报告提示词，继续生成报告。

## 天气查询（真实 Open-Meteo 接口）

`get_weather` 工具接入 [Open-Meteo](https://open-meteo.com/) 免费天气接口，**无需 API Key**，实现见 `agent/tools/weather_tool.py`，两步走：

1. **地理编码**：`get_coordinate_by_city(city)` 把城市名转成经纬度（`geocoding-api.open-meteo.com`）；
2. **天气查询**：`fetch_weather_by_city(city)` 用经纬度查实时温度/相对湿度 + 未来 5 小时预报（温度/降水/风速），拼成字符串喂给 LLM。

返回示例：

```
【北京天气】
实时：温度30.1℃，相对湿度23%
未来5小时预报：
09-21 08:00 温度20.6℃ 降水0.0mm 风速4.7m/s
09-21 09:00 温度22.4℃ 降水0.0mm 风速5.2m/s
...
```

**先问城市再查的多轮流程**：`prompt/main_prompt.txt` 里的天气/城市强约束要求模型——当用户问题涉及浇水、施肥、打药、防冻、遮阴等需要结合天气判断的操作、但当前还不知道城市时，先反问用户确认城市，等用户回复后再调 `get_weather`，禁止自行猜测或编造城市。典型对话：

```
用户：今天适合给番茄浇水吗？
顾问：请问您所在的城市是哪里呢？
用户：北京
顾问：北京今天温度 30℃，湿度 23%，未来几小时有降水……（调用 get_weather 后给出浇水建议）
```

任何一步失败（城市查不到 / 接口异常）都返回友好提示语而非抛异常，不会中断 Agent 主流程。

## 演示场景

四类场景覆盖全部 7 工具 + 3 中间件：

### 天气适配（多轮：先问城市再查）

![天气查询](img/q1-weather.png)

### 报告生成（动态提示词切换）

![报告生成-工具调用](img/q2-report-1.png)

![报告生成-报告正文](img/q2-report-2.png)

### 病虫害防治（RAG 检索）

![病虫害防治](img/q3-troubleshoot.png)

### 边界兜底

![边界兜底](img/q6-fallback.png)

## 技术栈

- Python
- LangChain —— ReAct 智能体、工具调用、中间件、LCEL
- Chroma —— 向量数据库；阿里云百炼 text-embedding-v3 —— 文本向量化
- DeepSeek —— 对话模型
- Open-Meteo —— 真实天气接口（地理编码 + 实时/预报）
- Streamlit —— Web 界面
- PyYAML —— 配置管理
- SQLite —— 用户与种植日志持久化（WAL 模式）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

复制 `.env.example` 为 `.env`，填入 DeepSeek 与阿里云百炼密钥：

```bash
cp .env.example .env
```

### 3. 构建知识库

知识库文档已备好（`data/` 下约 2.2 万条种植/园艺 TXT：43 篇人工科普文章 + 8 个分类文件共 21818 条百科问答），数据来源与格式见 [data/README.md](data/README.md)。如需替换，把新文档（PDF/TXT）放入 `data/` 目录，然后在项目根目录运行：

```bash
python -c "from rag.vector_store import VectorStoreService; VectorStoreService().load_documents([])"
```

向量会持久化到 `chroma_db/`（已 gitignore）。

### 4. 启动

```bash
streamlit run app.py
```

## 注意事项

- **密钥安全**：API Key 放在 `.env` 中，已加入 `.gitignore`，请勿提交到仓库。
- **首次构建**：需先执行步骤 3 构建知识库，否则问答无法检索到内容。
- 报告/种植日志数据存于 SQLite（`data/app.db`），首次启动自动建库并播种 12 个演示用户与近一年种植日志，无需手动准备数据。
- 演示账号：用户名 `1001`~`1012`，密码均为 `123456`。
