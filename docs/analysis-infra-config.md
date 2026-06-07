# 基础设施与配置 - 子系统分析

## 1. 概述

本报告涵盖基础设施组件：Codex CLI 配置、MCP 服务器、HTTP API、运行脚本和依赖管理。

## 2. Codex CLI 配置

### 2.1 生成代理（`.codex/config.toml`）

```toml
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
personality = "pragmatic"

[features]
multi_agent = true

[agents]
max_threads = 10
max_depth = 3
job_max_runtime_seconds = 3600

[mcp_servers.reasoning_agent]
command = "python3"
args = ["./mcp/server.py"]
tool_timeout_sec = 3600
```

**关键设置：**
- gpt-5.5 模型，最大推理强度。
- 启用多代理：最多 10 个并行线程，3 层递归深度。
- 1 小时任务超时。
- MCP 服务器注册为 `reasoning_agent`。
- 定义了模型迁移路径以保持向后兼容（gpt-5.2 -> gpt-5.3 -> gpt-5.5）。

### 2.2 验证代理（`.codex/config.toml`）

```toml
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
personality = "pragmatic"

[mcp_servers.verification_agent]
command = "python3"
args = ["./mcp/server.py"]
tool_timeout_sec = 3600
```

**与生成代理的区别：**
- 无多代理配置（验证是单代理）。
- MCP 服务器注册为 `verification_agent`。

### 2.3 子代理定义（`.codex/agents/subgoal-prover.toml`）

```toml
name = "subgoal-prover"
description = "An agent that try to prove all the subgoals..."
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
```

由 `recursive-proving` 技能用于生成并行证明尝试。

## 3. MCP 服务器

### 3.1 生成代理 MCP 服务器（`agents/generation/mcp/server.py`）

**框架：** FastMCP 2.0+

**工具（6 个）：**
| 工具 | 函数 | 说明 |
|------|------|------|
| `search_arxiv_theorems` | `search_arxiv_theorems()` | 查询 LeanSearch |
| `verify_proof_service` | `verify_proof_service()` | 调用验证 API |
| `memory_init` | `memory_init()` | 初始化问题内存 |
| `memory_append` | `memory_append()` | 追加到内存通道 |
| `memory_search` | `memory_search()` | 跨通道 BM25 搜索 |
| `branch_update` | `branch_update()` | 更新分支状态 |

**外部依赖：**
- `requests` — 用于 LeanSearch 和验证 API 的 HTTP 客户端
- `fastmcp` — MCP 服务器框架

### 3.2 验证代理 MCP 服务器（`agents/verification/mcp/server.py`）

**框架：** FastMCP 2.0+

**工具（6 个）：**
| 工具 | 函数 | 说明 |
|------|------|------|
| `search_arxiv_theorems` | `search_arxiv_theorems()` | 查询 LeanSearch |
| `memory_init` | `memory_init()` | 初始化运行内存 |
| `memory_append` | `memory_append()` | 追加到内存通道 |
| `memory_query` | `memory_query()` | 带过滤器查询 |
| `validate_verification_output` | `validate_verification_output()` | 根据 Schema 验证 |
| `write_verification_output` | `write_verification_output()` | 写入并验证输出 |

**外部依赖：**
- `requests` — 用于 LeanSearch 的 HTTP 客户端
- `fastmcp` — MCP 服务器框架
- `jsonschema` — JSON Schema 验证

## 4. HTTP API（`agents/verification/api/server.py`）

**框架：** FastAPI + Uvicorn

**端点：**

| 方法 | 路径 | 处理函数 | 说明 |
|------|------|----------|------|
| GET | `/health` | `health()` | 返回 `{"status": "ok"}` |
| POST | `/verify` | `verify()` | 验证证明 |

**请求模型：**
```python
class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)
```

**处理流程：**
1. 根据时间戳 + SHA256 哈希生成 `run_id`。
2. 分配结果目录。
3. 构建 Codex 命令。
4. 以子进程运行并捕获输出。
5. 读取并返回 `verification.json`。

**环境变量：**
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CODEX_BIN` | `codex` | Codex CLI 路径 |
| `CODEX_MODEL` | `gpt-5.5` | 模型名称 |
| `CODEX_REASONING_EFFORT` | `xhigh` | 推理强度 |
| `CODEX_TIMEOUT_SECONDS` | `0`（无） | 子进程超时 |

## 5. 运行脚本（`run_example.sh`）

### 5.1 输入验证

- `PROBLEM_FILE` 必须相对于 `agents/generation/`。
- 不得包含 `..`。
- 必须匹配 `data/*.md`。
- 文件必须存在。

### 5.2 问题 ID 提取

```bash
# data/algebra/prob1.md -> problem_rel=algebra/prob1
# data/algebra/prob1.md -> ref_dir=data/algebra/prob1.refs
# data/algebra/prob1.md -> problem_id=prob1
```

### 5.3 参考文献准备

如果 `.refs/` 目录存在：
- 扫描 PDF 文件。
- 使用 `pdftotext -layout` 转为文本。
- 将提取的文本存储在 `.refs/.extracted/`。
- 更新提示词引用提取的文件。

### 5.4 执行

```bash
codex exec \
  -C "$ROOT_DIR" \
  -m "$MODEL" \
  --config "model_reasoning_effort=\"$REASONING_EFFORT\"" \
  --dangerously-bypass-approvals-and-sandbox \
  "$prompt"
```

### 5.5 监控

- 每 30 秒显示已用时间。
- 启动时检查验证服务健康状态。
- 完成时显示总用时。

## 6. 依赖管理

### 6.1 生成代理

```
mcp/requirements.txt:
  fastmcp>=2.0.0
  requests>=2.31.0
```

### 6.2 验证代理

```
requirements.txt:
  -r mcp/requirements.txt
  -r api/requirements.txt
  pytest>=8.0.0

mcp/requirements.txt:
  fastmcp>=2.0.0
  jsonschema>=4.20.0
  requests>=2.31.0

api/requirements.txt:
  fastapi>=0.110.0
  uvicorn>=0.30.0
  httpx>=0.27.0
```

### 6.3 完整依赖树

| 包 | 版本 | 使用者 |
|----|------|--------|
| fastmcp | >=2.0.0 | 两个 MCP 服务器 |
| requests | >=2.31.0 | 两个 MCP 服务器 |
| jsonschema | >=4.20.0 | 验证 MCP |
| fastapi | >=0.110.0 | 验证 API |
| uvicorn | >=0.30.0 | 验证 API |
| httpx | >=0.27.0 | 验证 API |
| pytest | >=8.0.0 | 测试 |

## 7. 外部服务

### 7.1 LeanSearch

- **URL：** `https://leansearch.net/thm/search`
- **用途：** arXiv 定理搜索
- **协议：** HTTP POST + JSON 请求体
- **请求格式：**
  ```json
  {
    "query": "数学声明",
    "task": "检索有用引用...",
    "num_results": 10
  }
  ```
- **响应：** JSON 数组，每个元素包含 `title`、`theorem`、`arxiv_id`、`theorem_id`。

### 7.2 验证 API

- **URL：** `http://127.0.0.1:8091/verify`
- **用途：** 证明验证
- **协议：** HTTP POST + JSON 请求体
- **请求格式：**
  ```json
  {
    "statement": "定理声明",
    "proof": "markdown 证明文本"
  }
  ```
- **响应：** JSON，包含 `verification_report`、`verdict`、`repair_hints`。

## 8. 安全考虑

1. **沙箱绕过**：Codex 命令使用 `--dangerously-bypass-approvals-and-sandbox`，这对自主运行是必要的，但允许无限制的文件系统访问和命令执行。
2. **无身份验证**：验证 API 没有身份验证。应仅在本地暴露。
3. **路径遍历**：`sanitize_problem_id` 函数防止目录遍历，但运行脚本的验证是独立的。
4. **Codex 提示词无输入清理**：问题声明直接传递给 Codex 提示词。恶意 markdown 可能注入指令。

## 9. 观察

1. **部署简单**：系统只需 Python、Node.js（用于 Codex CLI）和可选的 Zola。无需数据库或复杂基础设施。
2. **本地优先设计**：一切在本地运行。唯一的外部依赖是 LeanSearch 用于定理搜索。
3. **基于子进程的验证**：每次验证生成新的 Codex 进程，增加了延迟但提供了隔离。
4. **硬编码端点**：LeanSearch URL 和验证 API URL 是硬编码的。应通过环境变量配置。
5. **无健康监控**：除了 `run_example.sh` 中的初始健康检查外，长时间运行期间没有对验证服务的监控。
