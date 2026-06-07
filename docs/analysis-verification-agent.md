# 验证代理 - 子系统分析

## 1. 概述

验证代理检查数学证明的正确性。它接收 markdown 格式的声明和证明，逐步验证证明，生成结构化 JSON 判定结果。代理同时作为 Codex CLI 代理（执行实际验证）和 HTTP API（与生成代理集成）运行。

**关键文件：**
- `agents/verification/AGENTS.md`（170 行）— 代理控制逻辑
- `agents/verification/.codex/config.toml`（18 行）— Codex 配置
- `agents/verification/api/server.py`（175 行）— FastAPI HTTP 服务器
- `agents/verification/mcp/server.py`（391 行）— MCP 工具服务器
- `agents/verification/schemas/verification_output.schema.json`（90 行）— 输出 Schema

## 2. 架构

验证子系统有三层：

```
生成代理
     │
     ▼ (HTTP POST /verify)
FastAPI 服务器 (api/server.py)
     │
     ▼ (子进程: codex exec)
Codex 验证代理 (AGENTS.md)
     │
     ▼ (MCP 工具)
MCP 服务器 (mcp/server.py)
     │
     ├── search_arxiv_theorems
     ├── memory_init / memory_append / memory_query
     ├── validate_verification_output
     └── write_verification_output
```

## 3. HTTP API（`api/server.py`）

### 3.1 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/verify` | 验证证明 |

### 3.2 验证端点

**请求：**
```json
{
  "statement": "证明每个素数阶有限群都是循环群。",
  "proof": "# lemma lem:1\n## statement\n..."
}
```

**处理流程：**
1. 根据时间戳 + 声明的 SHA256 哈希生成唯一 `run_id`。
2. 在 `results/{run_id}/` 分配结果目录。
3. 构建 Codex 命令：`codex exec -C {work_dir} -m gpt-5.5 --config model_reasoning_effort=xhigh --dangerously-bypass-approvals-and-sandbox "{prompt}"`。
4. 以子进程运行 Codex，将输出捕获到 `log.md`。
5. 等待完成（可配置超时）。
6. 读取结果目录中的 `verification.json`。
7. 返回 JSON 负载。

### 3.3 配置

环境变量：
- `CODEX_BIN` — codex 二进制路径（默认：`codex`）
- `CODEX_MODEL` — 模型名称（默认：`gpt-5.5`）
- `CODEX_REASONING_EFFORT` — 推理强度（默认：`xhigh`）
- `CODEX_TIMEOUT_SECONDS` — 子进程超时（默认：`0` = 无超时）

### 3.4 错误处理

- 504：Codex 子进程超时。
- 500：Codex 以非零退出码退出，或未找到验证输出，或输出不是有效 JSON。

## 4. 代理逻辑（AGENTS.md）

### 4.1 验证工作流

代理遵循 6 步流程：

1. **初始化运行上下文**：读取 Run_id、Statement、Proof。从 Statement 中提取假设。
2. **顺序验证证明项**：按文本顺序检查每个声明/子证明的逻辑有效性、定理应用、缺失假设和不正当跳跃。
3. **外部引用检查**：对每个引用的外部定理，查询 `search_arxiv_theorems`，将返回结果与引用声明比较，展开定义并检查上下文适用性。
4. **构建验证报告**：汇总所有错误和间隙。
5. **判定规则**：当且仅当 `critical_errors` 和 `gaps` 都为空时为 `"correct"`，否则为 `"wrong"`。
6. **输出写入**：通过 `write_verification_output` 持久化 `verification.json`。

### 4.2 问题分类

| 类型 | 说明 |
|------|------|
| `critical_error` | 逻辑错误、定理误用、矛盾、引用定理错误 |
| `gap` | 跳过的推导、模糊论证、缺失中间证明、可疑的未使用假设 |

### 4.3 严格判定规则

```
verdict = "correct"  当且仅当  critical_errors == []  且  gaps == []
verdict = "wrong"    其他情况
```

当 `verdict = "wrong"` 时：
- `repair_hints` 必须非空，为每个主要问题提供具体修复指导。

当 `verdict = "correct"` 时：
- `repair_hints` 必须为空字符串。

### 4.4 未使用假设处理

代理被明确指示不要假设未使用的假设是无害的。它必须仔细推理：
- 该假设确实是冗余的，还是
- 证明遗漏了必要的使用（间隙或错误）。

## 5. 技能

验证代理有 3 个技能：

### 5.1 `verify-sequential-statements`（顺序验证声明）
- 按文本顺序遍历证明。
- 在每一步检查局部推理有效性。
- 将发现分类为 `critical_error` 或 `gap`。
- 持久化到 `statement_checks` 通道。

### 5.2 `check-referenced-statements`（检查引用声明）
- 验证每个外部论文引用。
- 先查询 `search_arxiv_theorems`，再回退到网络搜索。
- 从引用论文的上下文中展开定义。
- 检查跨上下文的术语不匹配。
- 持久化到 `reference_checks` 通道。

### 5.3 `synthesize-verification-report`（综合验证报告）
- 汇总 `statement_checks` 和 `reference_checks` 中的所有发现。
- 应用严格判定规则。
- 根据 JSON Schema 验证输出。
- 写入 `verification.json`。

## 6. MCP 服务器（`mcp/server.py`）

### 6.1 工具

| 工具 | 说明 |
|------|------|
| `search_arxiv_theorems` | 向 LeanSearch 查询定理匹配 |
| `memory_init` | 初始化运行内存 |
| `memory_append` | 向通道追加记录 |
| `memory_query` | 带过滤器查询通道记录 |
| `validate_verification_output` | 根据 JSON Schema 验证 |
| `write_verification_output` | 写入并验证最终输出 |

### 6.2 内存通道

| 通道 | 内容 |
|------|------|
| `statement_checks` | 每条声明的验证结果 |
| `reference_checks` | 外部引用验证结果 |
| `verification_reports` | 最终验证报告 |
| `failed_checks` | 失败检查记录 |
| `events` | 审计追踪事件 |

### 6.3 输出验证

`validate_verification_output` 函数检查：
- 通过 `jsonschema.Draft202012Validator` 进行 Schema 合规性检查。
- 判定/发现一致性：`"correct"` 要求发现为空；`"wrong"` 要求发现非空。
- `repair_hints` 一致性：正确时为空，错误时非空。

## 7. JSON Schema（`verification_output.schema.json`）

```json
{
  "verification_report": {
    "summary": "string",
    "critical_errors": [{"location": "string", "issue": "string"}],
    "gaps": [{"location": "string", "issue": "string"}]
  },
  "verdict": "correct" | "wrong",
  "repair_hints": "string"
}
```

`verification_report` 中的可选字段：`notes`、`checked_items`、`external_reference_checks`。

每个发现可选包含 `severity` 和 `evidence` 字段。

## 8. 观察

1. **严格但公正**：零容忍策略确保只有真正正确的证明才能通过，但修复提示为修复问题提供了可操作的反馈。
2. **上下文感知的引用检查**：代理被指示展开引用论文中的定义并检查术语不匹配，这在数学中至关重要，因为同一个词在不同上下文中可能有不同含义。
3. **基于子进程的架构**：验证代理作为 API 服务器生成的 Codex 子进程运行。这很简单，但为每个验证请求增加了延迟（Codex 启动时间）。
4. **无缓存**：每个验证请求都生成新的 Codex 进程，即使相同的证明已被验证过。
5. **单文件输出**：验证结果是单个 JSON 文件，便于解析和处理。
