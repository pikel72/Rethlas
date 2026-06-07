# Rethlas - 代码库总体分析

## 1. 项目概述

Rethlas 是一个基于 OpenAI Codex CLI 构建的数学自然语言推理系统。它使用两个协作的 AI 代理来解决研究级别的数学问题：

- **生成代理**：从 markdown 文件中读取数学问题，通过迭代推理生成非形式化证明蓝图。
- **验证代理**：检查证明蓝图的正确性，并生成结构化判定结果。

系统遵循"生成-验证"循环：生成代理编写证明，验证代理检查证明，如果验证失败，生成代理修改后重试。

## 2. 仓库结构

```
Rethlas/
├── README.md                           # 项目文档
├── agents/
│   ├── generation/                     # 证明生成代理
│   │   ├── AGENTS.md                   # 代理控制逻辑与指令
│   │   ├── .codex/
│   │   │   ├── config.toml             # Codex CLI 配置
│   │   │   └── agents/
│   │   │       └── subgoal-prover.toml # 子代理定义
│   │   ├── .agents/skills/             # 10 个技能定义
│   │   │   ├── construct-counterexamples/
│   │   │   ├── construct-toy-examples/
│   │   │   ├── direct-proving/
│   │   │   ├── identify-key-failures/
│   │   │   ├── obtain-immediate-conclusions/
│   │   │   ├── propose-subgoal-decomposition-plans/
│   │   │   ├── query-memory/
│   │   │   ├── recursive-proving/
│   │   │   ├── search-math-results/
│   │   │   └── verify-proof/
│   │   ├── mcp/
│   │   │   ├── server.py               # MCP 工具服务器
│   │   │   ├── __init__.py
│   │   │   └── requirements.txt
│   │   ├── data/                       # 问题输入文件
│   │   │   ├── example.md
│   │   │   ├── example/
│   │   │   └── modrep/
│   │   ├── site/                       # Zola 结果浏览站点
│   │   │   ├── config.toml
│   │   │   ├── serve.sh
│   │   │   ├── setup_theme.sh
│   │   │   ├── transform_math.py
│   │   │   ├── templates/
│   │   │   └── content/
│   │   └── tests/
│   │       └── run_example.sh          # 运行脚本
│   └── verification/                   # 证明验证代理
│       ├── AGENTS.md                   # 代理控制逻辑与指令
│       ├── .codex/
│       │   └── config.toml             # Codex CLI 配置
│       ├── .agents/skills/             # 3 个技能定义
│       │   ├── check-referenced-statements/
│       │   ├── synthesize-verification-report/
│       │   └── verify-sequential-statements/
│       ├── api/
│       │   ├── server.py               # FastAPI HTTP 服务器
│       │   ├── __init__.py
│       │   └── requirements.txt
│       ├── mcp/
│       │   ├── server.py               # MCP 工具服务器
│       │   ├── __init__.py
│       │   └── requirements.txt
│       ├── schemas/
│       │   └── verification_output.schema.json
│       ├── scripts/
│       │   └── test_verify_endpoint.py
│       └── requirements.txt
```

## 3. 技术栈

| 层级 | 技术 |
|------|------|
| AI 运行时 | OpenAI Codex CLI (`codex exec`) |
| 模型 | gpt-5.5，`xhigh` 推理强度 |
| 代理框架 | Codex 多代理 + `.agents/skills/` |
| MCP 服务器 | FastMCP (Python) |
| HTTP API | FastAPI + Uvicorn |
| 搜索后端 | LeanSearch（arXiv 定理搜索） |
| 站点生成器 | Zola + MATbook 主题 |
| 数学渲染 | MathJax 3 |
| 编程语言 | Python 3.11、Bash |

## 4. 架构

### 4.1 部署架构

```
┌─────────────────────┐         ┌──────────────────────┐
│  生成代理             │         │  验证代理              │
│  (Codex CLI)         │         │  (Codex CLI)          │
│                      │         │                       │
│  AGENTS.md           │         │  AGENTS.md            │
│  10 个技能           │         │  3 个技能              │
│  subgoal-prover      │         │                       │
│                      │  HTTP   │                       │
│  MCP 服务器 ─────────┼────────>│  FastAPI 服务器        │
│  (端口: stdio)       │ :8091   │  (端口: 8091)         │
└─────────────────────┘         └──────────────────────┘
         │                                │
         └──────── 共享目录 ───────────────┘
              memory/ / results/
```

### 4.2 数据流

1. 用户在 `data/` 中提供 markdown 格式的数学问题。
2. `run_example.sh` 调用 `codex exec` 启动生成代理。
3. 生成代理读取问题、初始化内存，进入自适应控制循环。
4. 循环过程中，代理使用各种技能（搜索、证明、验证）和 MCP 工具。
5. 组装完整证明草稿后，调用 `verify_proof_service`（MCP 工具）。
6. MCP 工具向验证 API（`:8091/verify`）发送 HTTP POST 请求。
7. 验证 API 以子进程方式启动 Codex 验证代理。
8. 验证代理检查证明并写入 `verification.json`。
9. API 将判定结果返回给生成代理。
10. 如果判定为 `"wrong"`，生成代理修改后重试。
11. 如果判定为 `"correct"`，证明保存为 `blueprint_verified.md`。

### 4.3 关键设计决策

- **基于技能的代理设计**：每种数学推理能力封装为一个技能，拥有独立的 `SKILL.md` 和 `openai.yaml` 配置。
- **仅追加内存**：所有中间推理产物以 JSONL 格式持久化，支持完整审计追踪和基于 BM25 的检索。
- **严格验证**：验证器采用零容忍策略——任何关键错误或间隙都导致 `"wrong"` 判定。
- **递归子代理委派**：复杂问题可以为每个分解计划生成子代理进行并行探索。
- **外部知识锚定**：所有非平凡声明都通过 LeanSearch（arXiv 定理数据库）进行验证。

## 5. 子系统索引

| 子系统 | 说明 | 报告 |
|--------|------|------|
| 生成代理 | 带自适应控制循环的证明生成 | [analysis-generation-agent.md](analysis-generation-agent.md) |
| 验证代理 | 带严格判定逻辑的证明验证 | [analysis-verification-agent.md](analysis-verification-agent.md) |
| 内存系统 | 基于 JSONL 的仅追加内存与 BM25 搜索 | [analysis-memory-system.md](analysis-memory-system.md) |
| 技能系统 | 两个代理共 13 个技能 | [analysis-skills-system.md](analysis-skills-system.md) |
| 站点渲染 | 基于 Zola 的结果浏览静态站点 | [analysis-site-rendering.md](analysis-site-rendering.md) |
| 基础设施 | Codex 配置、MCP 服务器、API、运行脚本 | [analysis-infra-config.md](analysis-infra-config.md) |

## 6. 代码度量

| 指标 | 数值 |
|------|------|
| 源文件总数（不含 .venv） | ~60 |
| Python 源文件 | ~6 |
| 技能定义（SKILL.md） | 13 |
| 代理 YAML 配置 | 13 |
| Shell 脚本 | 4 |
| 配置文件（toml、json） | ~8 |
| Python 代码行数（约） | ~850 |
| 代理指令行数（约） | ~1200 |

## 7. 优势

1. **结构清晰的代理架构**：生成与验证分离明确，接口定义清晰。
2. **丰富的技能系统**：10 个生成技能覆盖完整的数学推理生命周期（搜索、举例、反例、证明、分解、验证）。
3. **持久化内存**：所有推理步骤均有日志，支持调试、审计和跨会话复用。
4. **外部知识锚定**：集成 LeanSearch 防止幻觉定理。
5. **严格验证**：零容忍验证确保只有正确的证明才能通过。
6. **结果浏览**：Zola 站点提供了带 LaTeX 渲染的证明结果浏览界面。

## 8. 改进建议

1. **缺少测试套件**：唯一的测试是 `test_verify_endpoint.py`，这是一个手动脚本而非自动化测试。内存、BM25、路径清理和 API 的单元测试可以提升可靠性。
2. **代码重复**：两个 MCP 服务器（`generation/mcp/server.py` 和 `verification/mcp/server.py`）共享大量代码（JSONL I/O、`search_arxiv_theorems`、内存操作）。抽取共享库可减少维护负担。
3. **API 调用无重试/退避**：`search_arxiv_theorems` 和 `verify_proof_service` 使用 `requests.post` 没有重试逻辑或指数退避。
4. **URL 硬编码**：`THEOREM_SEARCH_URL` 和 `VERIFY_PROOF_URL` 是硬编码常量，应通过环境变量配置。
5. **运行脚本无错误恢复**：`run_example.sh` 不处理部分失败（如 Codex 中途崩溃时内存可能处于不一致状态）。
6. **安全风险**：Codex 命令使用 `--dangerously-bypass-approvals-and-sandbox`，这对自主运行是必要的，但应作为风险记录。
7. **缺少进度监控**：长时间运行时，除了查看日志文件外没有监控代理进度的方式。
