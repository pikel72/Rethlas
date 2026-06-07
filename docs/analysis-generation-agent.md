# 生成代理 - 子系统分析

## 1. 概述

生成代理是 Rethlas 的核心推理引擎。它从 markdown 文件中读取数学问题，通过迭代的、基于技能的过程生成经验证的证明蓝图。代理通过 `AGENTS.md` 和 `.codex/config.toml` 配置，作为 Codex CLI 代理运行。

**关键文件：**
- `agents/generation/AGENTS.md`（225 行）— 代理控制逻辑
- `agents/generation/.codex/config.toml`（30 行）— Codex 配置
- `agents/generation/.codex/agents/subgoal-prover.toml`（7 行）— 子代理定义
- `agents/generation/mcp/server.py`（463 行）— MCP 工具服务器
- `agents/generation/tests/run_example.sh`（141 行）— 运行脚本

## 2. 代理配置

### 2.1 Codex 配置（`.codex/config.toml`）

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
```

- 使用 gpt-5.5 模型，最大推理强度。
- 启用多代理模式：最多 10 个并行线程，3 层递归深度。
- 每个代理任务超时时间为 1 小时。
- 定义了 `subgoal-prover` 子代理，继承相同的模型设置。

### 2.2 MCP 服务器集成

Codex 配置中注册了 MCP 服务器：
```toml
[mcp_servers.reasoning_agent]
command = "python3"
args = ["./mcp/server.py"]
tool_timeout_sec = 3600
```

这使代理可以使用 6 个 MCP 工具：
- `search_arxiv_theorems` — 向 LeanSearch 查询数学结果
- `verify_proof_service` — 调用验证 API
- `memory_init` — 为问题初始化内存
- `memory_append` — 向内存通道追加记录
- `memory_search` — 基于 BM25 的内存搜索
- `branch_update` — 更新分支状态跟踪

## 3. 控制流程（AGENTS.md）

### 3.1 自适应控制循环

代理遵循 4 步循环：

1. **评估状态**：评估已尝试的内容、已有信息和当前卡点。
2. **选择技能**：根据当前状态选择最合适的技能。
3. **执行并持久化**：执行技能，将所有产物持久化到内存，更新分支状态。
4. **停止规则**：仅在验证通过时停止。

### 3.2 技能选择启发式

代理有详细的技能选择启发式规则：

| 条件 | 技能 |
|------|------|
| 开始新问题/分支 | `$obtain-immediate-conclusions` |
| 需要外部定理 | `$search-math-results` |
| 检查已有知识 | `$query-memory` |
| 受阻，需要更简单的例子 | `$construct-toy-examples` |
| 测试脆弱声明 | `$construct-counterexamples` |
| 信息足够，可以规划 | `$propose-subgoal-decomposition-plans` |
| 计划已创建 | `$direct-proving` |
| 所有计划已筛选 | `$recursive-proving` |
| 所有计划失败 | `$identify-key-failures` |
| 完整证明已组装 | `$verify-proof` |

### 3.3 验证修复循环

验证失败时：
1. 使用验证报告进行修改。
2. 优先解决关键错误。
3. 必要时改变策略（不仅仅是局部修复）。
4. 解决剩余间隙。
5. 重新调用相应技能。

### 3.4 硬性不变量

代理有 14 条硬性不变量，包括：
- 所有中间产物必须写入内存。
- 失败路径是强制性的且可查询。
- 验证必须通过才能输出。
- 外部结果必须附带完整来源标识符。
- 外部结果必须经过上下文检查才能使用（展开定义、消除术语歧义）。
- 不得读取工作目录以外的内容。
- 最终定理声明必须来自输入文件的原文。

## 4. 输出规范

代理以类论文的 markdown 格式编写证明：

```markdown
# lemma lem:xxx

## statement
...

## proof
...
```

输出文件：
- `results/{problem_id}/blueprint.md` — 工作草稿
- `results/{problem_id}/blueprint_verified.md` — 经验证的证明（通过验证后）

## 5. 问题输入格式

问题是 `data/` 目录下的 markdown 文件：
- `data/example.md` — 未分类问题
- `data/category/problem.md` — 分类问题
- `data/category/problem.refs/` — 可选的参考文献目录

`problem_id` 从相对于 `data/` 的路径派生，不含 `.md`：
- `data/example.md` -> `problem_id=example`
- `data/algebra/modrep.md` -> `problem_id=algebra/modrep`

## 6. 运行脚本（`run_example.sh`）

运行脚本的功能：
1. 验证 `PROBLEM_FILE`（必须是相对路径，在 `data/` 下，必须存在）。
2. 从文件路径提取 `problem_id` 和 `ref_dir`。
3. 预处理 PDF 参考文献（使用 `pdftotext` 转为文本）。
4. 设置日志输出到 `logs/{problem_rel}/{problem_id}.md`。
5. 检查验证服务是否可达。
6. 使用相应提示词运行 `codex exec`。
7. 执行期间每 30 秒显示已用时间。

## 7. 子代理：子目标证明器

```toml
name = "subgoal-prover"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
```

一个专门的子代理，接收分解计划并尝试证明所有子目标。由 `$recursive-proving` 技能使用。报告哪些已证明、哪些失败。

## 8. 数据流

```
data/example.md
     │
     ▼
run_example.sh
     │
     ▼
codex exec ──> AGENTS.md（控制循环）
     │              │
     │              ├── 技能: obtain-immediate-conclusions
     │              ├── 技能: search-math-results ──> search_arxiv_theorems (MCP)
     │              ├── 技能: construct-toy-examples
     │              ├── 技能: construct-counterexamples
     │              ├── 技能: propose-subgoal-decomposition-plans
     │              ├── 技能: direct-proving
     │              ├── 技能: recursive-proving ──> 生成子代理
     │              ├── 技能: identify-key-failures
     │              └── 技能: verify-proof ──> verify_proof_service (MCP)
     │                                              │
     │                                              ▼
     │                                      HTTP POST :8091/verify
     │                                              │
     │                                              ▼
     │                                      验证代理
     │
     ▼
results/{problem_id}/blueprint_verified.md
```

## 9. 观察

1. **设计良好的自适应循环**：技能选择启发式规则全面，覆盖了完整的数学推理生命周期。
2. **严格的锚定要求**：代理被要求引用来源、展开定义，并在使用外部结果前检查适用性。
3. **递归能力**：多代理设置支持 `max_depth=3` 的递归探索，可深入探索证明策略。
4. **内存优先方法**：每个决策和中间结果都被持久化，使过程完全可审计。
5. **无显式成本控制**：使用 `xhigh` 推理强度和 1 小时超时，长时间运行的问题可能成本较高。
