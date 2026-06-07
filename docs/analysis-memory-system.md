# 内存系统 - 深度分析

## 1. 概述

Rethlas 的内存系统是一个基于 JSONL 文件的持久化、仅追加存储层，为两个代理提供中间推理产物的记录与检索能力。它不是传统意义上的"内存"（如 RAM 或缓存），而更接近一个**结构化的事件日志 + 语义检索引擎**的组合。

**核心设计目标：**
- 完整审计追踪：每一步推理都可回溯
- 跨技能复用：一个技能的输出可供另一个技能查询
- 语义检索：基于 BM25 算法按相关性检索历史记录
- 路径安全：防止目录遍历攻击

**关键文件：**
- `agents/generation/mcp/server.py` — 生成代理内存实现（463 行）
- `agents/verification/mcp/server.py` — 验证代理内存实现（391 行）

---

## 2. 存储架构

### 2.1 物理结构

```
memory/
├── example/                              # problem_id = "example"
│   ├── meta.json                         # 问题级元数据
│   ├── immediate_conclusions.jsonl       # 直接推论
│   ├── toy_examples.jsonl                # 玩具示例
│   ├── counterexamples.jsonl             # 反例
│   ├── big_decisions.jsonl               # 重大决策
│   ├── subgoals.jsonl                    # 分解计划
│   ├── proof_steps.jsonl                 # 证明尝试
│   ├── failed_paths.jsonl                # 失败路径
│   ├── verification_reports.jsonl        # 验证报告
│   ├── branch_states.jsonl               # 分支状态
│   └── events.jsonl                      # 审计日志
└── algebra_modrep/                       # problem_id = "algebra/modrep"
    ├── meta.json
    └── ...（同上）
```

每个 `problem_id` 对应一个独立目录，目录内每个通道对应一个 JSONL 文件。这种设计使得不同问题的内存完全隔离，同一问题的不同通道也可独立读写。

### 2.2 JSONL 行格式

每条记录是一个完整的 JSON 对象，占据一行：

```json
{
  "timestamp_utc": "2026-05-28T12:00:00+00:00",
  "channel": "immediate_conclusions",
  "record": {
    "statement": "若 G 是素数阶有限群，则 G 是循环群",
    "justification_type": "known_fact",
    "confidence": 0.95,
    "is_fragile": false,
    "fragility_reason": "",
    "suggested_followup": "none",
    "scope": "global"
  }
}
```

外层结构固定为 `timestamp_utc` + `channel` + `record`，`record` 内部的结构由各技能自行定义。这种分层设计既保证了日志格式的统一性，又允许各通道灵活存储不同类型的数据。

### 2.3 meta.json 格式

```json
{
  "problem_id": "example",
  "created_at_utc": "2026-05-28T10:00:00+00:00",
  "updated_at_utc": "2026-05-28T12:30:00+00:00",
  "statement": "证明每个素数阶有限群都是循环群"
}
```

`meta.json` 采用合并更新策略：首次调用 `memory_init` 时写入 `created_at_utc`，后续调用只更新 `updated_at_utc` 和其他传入的字段，不会覆盖已有的 `created_at_utc`。这保证了问题创建时间的稳定性。

---

## 3. 通道设计

### 3.1 生成代理通道（10 个）

| 通道 | 文件名 | 写入者技能 | 数据语义 |
|------|--------|-----------|----------|
| `immediate_conclusions` | `immediate_conclusions.jsonl` | `obtain-immediate-conclusions` | 从问题直接推出的数学结论 |
| `toy_examples` | `toy_examples.jsonl` | `construct-toy-examples`、`construct-counterexamples` | 满足假设和结论的简单示例 |
| `counterexamples` | `counterexamples.jsonl` | `construct-counterexamples` | 证伪尝试的记录 |
| `big_decisions` | `big_decisions.jsonl` | AGENTS.md 控制循环 | 重大战略决策 |
| `subgoals` | `subgoals.jsonl` | `propose-subgoal-decomposition-plans` | 分解计划及其子目标 |
| `proof_steps` | `proof_steps.jsonl` | `direct-proving` | 每个子目标的证明尝试 |
| `failed_paths` | `failed_paths.jsonl` | `identify-key-failures`、其他技能 | 失败的计划和原因 |
| `verification_reports` | `verification_reports.jsonl` | `verify-proof` | 验证服务返回的完整报告 |
| `branch_states` | `branch_states.jsonl` | 多个技能通过 `branch_update` | 证明分支的状态跟踪 |
| `events` | `events.jsonl` | 所有技能（自动） | 审计日志，记录所有操作 |

**通道间的数据流关系：**

```
obtain-immediate-conclusions
        │
        ▼
  immediate_conclusions
        │
        ├──► construct-toy-examples ──► toy_examples
        │                                    │
        ├──► construct-counterexamples ──► counterexamples
        │         │                          │
        │         └──► (如果证伪) ──► failed_paths
        │
        ├──► propose-subgoal-decomposition-plans ──► subgoals
        │                                                │
        │                                                ▼
        │                                          direct-proving ──► proof_steps
        │                                                │
        │                                                ▼
        │                                          recursive-proving
        │                                                │
        │                                                ▼
        │                                          identify-key-failures ──► failed_paths
        │                                                                      │
        │                                                                      ▼
        └──────────────────────────────────────────► (下一轮规划)
```

### 3.2 验证代理通道（5 个）

| 通道 | 文件名 | 写入者 | 数据语义 |
|------|--------|--------|----------|
| `statement_checks` | `statement_checks.jsonl` | `verify-sequential-statements` | 每条声明的检查结果 |
| `reference_checks` | `reference_checks.jsonl` | `check-referenced-statements` | 外部引用的验证结果 |
| `verification_reports` | `verification_reports.jsonl` | `synthesize-verification-report` | 最终验证报告 |
| `failed_checks` | `failed_checks.jsonl` | 各验证技能 | 失败的检查记录 |
| `events` | `events.jsonl` | 所有技能（自动） | 审计日志 |

**验证代理的通道流：**

```
verify-sequential-statements ──► statement_checks
                                          │
                                          ▼
check-referenced-statements ──► reference_checks
                                          │
                                          ▼
                              synthesize-verification-report
                                          │
                                          ├──► verification_reports
                                          └──► verification.json（最终输出）
```

### 3.3 events 通道的特殊性

`events` 通道是唯一一个被**自动写入**的通道。当 `memory_append` 向任何非 `events` 通道写入时，会自动在 `events` 中记录一条：

```json
{
  "timestamp_utc": "...",
  "event_type": "memory_append",
  "channel": "immediate_conclusions"
}
```

此外，各技能还会在 `events` 中记录特殊事件类型：

| 事件类型 | 触发场景 |
|----------|----------|
| `memory_append` | 任何非 events 通道的写入（自动） |
| `immediate_conclusions_stalled` | 获取直接结论技能失败 |
| `toy_examples_inconclusive` | 玩具示例不确定 |
| `counterexample_space_unclear` | 反例搜索空间不明确 |
| `decomposition_plans_not_ready` | 分解计划尚未就绪 |
| `search_math_results_stalled` | 数学搜索无结果 |
| `query_memory_stalled` | 内存查询无用 |
| `key_failures_inconclusive` | 关键失败识别不确定 |
| `recursive_proving_round` | 递归证明轮次 |
| `query_memory` | 内存查询结果摘要 |

这使得 `events` 通道成为整个推理过程的**时间线**，可以完整重建代理的行为序列。

---

## 4. MCP 工具详细分析

### 4.1 `memory_init(problem_id, meta=None)`

**功能：** 初始化一个问题的内存空间。

**实现细节（generation/mcp/server.py 第 243-282 行）：**

1. 调用 `sanitize_problem_id` 清理输入。
2. 通过 `_problem_dir` 解析路径并验证安全性。
3. 创建目录（`mkdir(parents=True, exist_ok=True)`）。
4. 为每个通道创建空文件（`touch(exist_ok=True)`）。
5. 读取已有的 `meta.json`（如果存在）。
6. 合并元数据：保留已有的 `created_at_utc`，更新 `updated_at_utc`。
7. 写入合并后的 `meta.json`。

**幂等性：** 完全幂等。多次调用不会破坏已有数据，只会更新 `updated_at_utc`。

**关键代码片段：**
```python
merged_meta: Dict[str, Any] = {
    "problem_id": sanitized_problem_id,
    "created_at_utc": existing_meta.get("created_at_utc", _utc_now()),
    "updated_at_utc": _utc_now(),
}
merged_meta.update(existing_meta)  # 保留已有字段
if meta:
    merged_meta.update(meta)       # 合并新字段
```

注意合并顺序：先写入默认值，再用 `existing_meta` 覆盖（保留 `created_at_utc`），最后用传入的 `meta` 覆盖。这意味着传入的 `meta` 可以覆盖除 `created_at_utc` 以外的任何字段。

### 4.2 `memory_append(problem_id, channel, record)`

**功能：** 向指定通道追加一条记录。

**实现细节（generation/mcp/server.py 第 285-316 行）：**

1. 验证 `record` 是 dict 类型。
2. 调用 `memory_init` 确保目录和文件存在（幂等保护）。
3. 构造包装对象：`{timestamp_utc, channel, record}`。
4. 调用 `_append_jsonl` 写入文件。
5. 如果通道不是 `events`，自动在 `events` 中记录一条 `memory_append` 事件。

**JSONL 写入实现（第 112-115 行）：**
```python
def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
```

使用 `"a"`（追加模式）打开文件，每次写入一行 JSON + 换行符。`ensure_ascii=False` 允许直接写入 Unicode 字符（如中文、数学符号），而不是转义为 `\uXXXX`。

**自动事件日志：**
```python
if channel != "events":
    event_entry = {
        "timestamp_utc": _utc_now(),
        "event_type": "memory_append",
        "channel": channel,
    }
    _append_jsonl(_channel_path(problem_id, "events"), event_entry)
```

这个设计确保 `events` 通道不会递归触发自身写入。

### 4.3 `memory_search(problem_id, query, channels=None, limit_per_channel=10)`

**功能：** 基于 BM25 算法跨通道搜索内存记录。

**实现细节（generation/mcp/server.py 第 319-376 行）：**

1. 验证查询非空、`limit_per_channel` > 0。
2. 如果未指定通道，搜索除 `events` 外的所有通道。
3. 对每个通道：
   a. 读取所有 JSONL 记录（`_iter_jsonl`）。
   b. 将每条记录序列化为 JSON 字符串作为文档。
   c. 对文档进行 BM25 分词。
   d. 计算 BM25 分数。
   e. 按分数降序排序，同分按时间戳降序。
   f. 过滤掉零分结果。
   g. 限制为 `limit_per_channel` 条。
4. 返回按通道分组的结果。

**BM25 实现详解（第 118-159 行）：**

```python
def _tokenize_bm25(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]+", text.lower())
```

分词器只保留字母、数字和下划线组成的 token，全部转为小写。这意味着：
- 数学符号（如 `∀`、`∈`、`→`）会被丢弃
- LaTeX 命令（如 `\frac`、`\sum`）会被保留（因为包含字母）
- 中文字符会被丢弃（不在 `[A-Za-z0-9_]+` 范围内）

```python
def _bm25_score_documents(query, documents, *, k1=1.5, b=0.75):
    # BM25 公式：
    # score = Σ(tf * idf * (tf * (k1+1)) / (tf + k1*(1-b+b*dl/avgdl)))
    #
    # 其中：
    # - tf = 词项在文档中的频率
    # - idf = log(1 + (N - df + 0.5) / (df + 0.5))
    # - dl = 文档长度
    # - avgdl = 平均文档长度
    # - k1 = 1.5（词频饱和参数）
    # - b = 0.75（长度归一化参数）
```

BM25 参数选择：
- `k1=1.5`：标准值。控制词频饱和速度。k1 越大，高频词的权重提升越明显。
- `b=0.75`：标准值。控制文档长度归一化程度。b=1 时完全按长度归一化，b=0 时不做长度归一化。

**性能特征：**
- 每次搜索加载指定通道的全部 JSONL 记录到内存。
- 对每条记录进行 JSON 序列化和分词。
- 时间复杂度：O(N * D)，其中 N 是查询 token 数，D 是文档数。
- 对于大问题（数千条记录），每次搜索可能需要数百毫秒。

### 4.4 `branch_update(problem_id, branch_id, state)`

**功能：** 更新证明分支的状态。

**实现（第 379-388 行）：**
```python
def branch_update(problem_id, branch_id, state):
    payload = {"branch_id": branch_id, "state": state}
    return memory_append(problem_id, "branch_states", payload)
```

这是 `memory_append` 的便捷封装，将 `branch_id` 和 `state` 打包后追加到 `branch_states` 通道。

### 4.5 验证代理特有工具

#### `memory_query(run_id, channel, filters=None, contains=None, limit=100, reverse=True)`

验证代理不使用 BM25 搜索，而是使用基于过滤的查询：

```python
def memory_query(run_id, channel, filters=None, contains=None, limit=100, reverse=True):
    items = list(_iter_jsonl(path))

    # 按字段过滤
    if filters:
        filtered = []
        for item in items:
            if all(item.get(key) == value for key, value in filters.items()):
                filtered.append(item)
        items = filtered

    # 按文本包含过滤
    if contains:
        needle = contains.lower()
        items = [item for item in items if needle in json.dumps(item).lower()]

    # 反转（最新的在前）
    if reverse:
        items = list(reversed(items))

    return items[:limit]
```

与 BM25 搜索的区别：
- `memory_query` 是精确匹配（字段相等 + 文本包含），适合已知结构的查询。
- `memory_search` 是语义检索（BM25 相关性评分），适合开放式查询。
- `memory_query` 默认返回最新的记录（`reverse=True`），`memory_search` 按相关性排序。

#### `validate_verification_output(payload)`

验证代理独有，用于检查验证输出是否符合 JSON Schema：

```python
def validate_verification_output(payload):
    # 1. 加载 schema
    schema = _load_schema()

    # 2. JSON Schema 验证（Draft 2020-12）
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(payload):
        errors.append(...)

    # 3. 业务逻辑验证
    if verdict == "correct":
        if has_any_finding:
            errors.append("verdict='correct' 时 critical_errors 或 gaps 必须为空")
        if repair_hints != "":
            errors.append("verdict='correct' 时 repair_hints 必须为空字符串")
    elif verdict == "wrong":
        if not has_any_finding:
            errors.append("verdict='wrong' 时必须有至少一个 critical_error 或 gap")
        if not repair_hints.strip():
            errors.append("verdict='wrong' 时 repair_hints 必须非空")
```

这个函数做了两层验证：
1. **结构验证**：JSON Schema 检查字段类型、必填字段等。
2. **语义验证**：判定结果与发现的一致性（correct 时必须无发现，wrong 时必须有发现）。

#### `write_verification_output(run_id, payload)`

```python
def write_verification_output(run_id, payload):
    # 1. 验证输出
    validation = validate_verification_output(payload)
    if not validation["valid"]:
        raise ValueError(...)

    # 2. 写入文件
    output_path = RESULTS_ROOT / run_id / "verification.json"
    with output_path.open("w") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    # 3. 记录到内存
    memory_append(run_id, "verification_reports", {
        "event_type": "final_report_written",
        "output_path": str(output_path),
        "verdict": payload.get("verdict"),
    })
```

先验证、再写入、最后记录。如果验证失败则抛出异常，不会写入无效数据。

---

## 5. 路径安全机制

### 5.1 `sanitize_problem_id`（generation/mcp/server.py 第 47-67 行）

```python
def sanitize_problem_id(raw: str) -> str:
    normalized = raw.strip().replace("\\", "/")
    parts: List[str] = []
    for part in normalized.split("/"):
        stripped = part.strip()
        if stripped in {"", "."}:
            continue
        if stripped == "..":
            raise ValueError("problem_id must not contain '..' path components")
        cleaned = _sanitize_problem_component(stripped)
        if cleaned:
            parts.append(cleaned)
    return "/".join(parts) or "problem"
```

**清理规则：**
1. 反斜杠统一为正斜杠。
2. 跳过空组件和 `.`。
3. 拒绝 `..`（抛出异常）。
4. 对每个组件调用 `_sanitize_problem_component`：
   - 空白替换为 `_`
   - 非字母数字字符（除 `._-` 外）替换为 `_`
   - 合并连续下划线
   - 剥离首尾的 `._`

**示例：**
| 输入 | 输出 |
|------|------|
| `"example"` | `"example"` |
| `"algebra/modrep"` | `"algebra/modrep"` |
| `"../etc/passwd"` | 抛出 ValueError |
| `"my problem!"` | `"my_problem_"` |
|`"a//b"` | `"a/b"` |

### 5.2 `_problem_dir` 双重验证（第 80-86 行）

```python
def _problem_dir(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    problem_dir = (MEMORY_ROOT / sanitized_problem_id).resolve()
    memory_root = MEMORY_ROOT.resolve()
    if not problem_dir.is_relative_to(memory_root):
        raise ValueError("problem_id resolves outside memory root")
    return problem_dir
```

即使 `sanitize_problem_id` 已经拒绝了 `..`，这里仍然做了二次检查：解析符号链接后的绝对路径必须在 `MEMORY_ROOT` 内。这防御了符号链接攻击等边界情况。

### 5.3 验证代理的简化版

验证代理使用 `run_id` 而非 `problem_id`，格式为 `YYYYMMDDTHHMMSSZ_{sha256[:12]}`，由 API 服务器生成，不接受用户输入。因此它的清理函数更简单：

```python
def sanitize_run_id(raw: str) -> str:
    cleaned = re.sub(r"\s+", "_", str(raw).strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "run"
```

没有路径分隔符处理，因为 `run_id` 不应该是路径。

---

## 6. JSONL 读写机制

### 6.1 写入（`_append_jsonl`）

```python
def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
```

- 使用追加模式 `"a"`，不会覆盖已有数据。
- `ensure_ascii=False` 直接写入 Unicode，减少文件体积。
- 每条记录一行，以 `\n` 结尾。

### 6.2 读取（`_iter_jsonl`）

```python
def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload
```

- 使用生成器（`yield`），逐行读取，不会一次性加载整个文件到内存。
- 跳过空行和 JSON 解析失败的行（容错）。
- 只返回 dict 类型的记录。

---

## 7. 两个代理的内存系统对比

| 方面 | 生成代理 | 验证代理 |
|------|----------|----------|
| **ID 类型** | `problem_id`（类路径，如 `algebra/modrep`） | `run_id`（时间戳哈希，如 `20260528T120000Z_a1b2c3d4e5f6`） |
| **ID 来源** | 由用户输入的文件路径派生 | 由 API 服务器自动生成 |
| **通道数** | 10 个 | 5 个 |
| **搜索方式** | BM25 语义检索（`memory_search`） | 基于过滤的精确查询（`memory_query`） |
| **Schema 验证** | 无 | `validate_verification_output` + JSON Schema |
| **输出写入** | 无（由 Codex 代理直接写文件） | `write_verification_output`（验证后写入） |
| **BM25 实现** | 有（完整实现） | 无 |
| **目录结构** | `memory/{problem_id}/` | `memory/{run_id}/` |
| **结果目录** | `results/{problem_id}/` | `results/{run_id}/` |

**代码重复度：** 两个 MCP 服务器共享约 60% 的代码（`_utc_now`、`sanitize_*`、`_append_jsonl`、`_iter_jsonl`、`memory_init`、`memory_append`、`search_arxiv_theorems`）。主要差异在于：
- 搜索工具（BM25 vs 过滤查询）
- 通道定义
- 验证工具（仅验证代理有）

---

## 8. BM25 算法深入分析

### 8.1 BM25 公式

BM25（Best Matching 25）是信息检索中的经典排序函数。对于查询 Q 和文档 D，得分为：

```
score(Q, D) = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D| / avgdl))
```

其中：
- `qi` 是查询中的第 i 个词项
- `f(qi, D)` 是词项 qi 在文档 D 中的频率（TF）
- `|D|` 是文档 D 的长度（token 数）
- `avgdl` 是所有文档的平均长度
- `k1` 和 `b` 是调节参数

IDF（逆文档频率）的计算：
```
IDF(qi) = log(1 + (N - n(qi) + 0.5) / (n(qi) + 0.5))
```
其中 N 是文档总数，n(qi) 是包含词项 qi 的文档数。

### 8.2 实际行为示例

假设有以下 `immediate_conclusions` 通道记录：

```jsonl
{"timestamp_utc":"T1","channel":"immediate_conclusions","record":{"statement":"G is cyclic","justification_type":"by_definition","confidence":0.9}}
{"timestamp_utc":"T2","channel":"immediate_conclusions","record":{"statement":"|G| = p where p is prime","justification_type":"known_fact","confidence":1.0}}
{"timestamp_utc":"T3","channel":"immediate_conclusions","record":{"statement":"Every element has order dividing p","justification_type":"calculation","confidence":0.95}}
```

查询 `"prime order cyclic"` 时：
- 文档 1 包含 `cyclic`（匹配 1 个查询 token）
- 文档 2 包含 `prime`（匹配 1 个查询 token）
- 文档 3 无匹配

三个文档都会被返回（如果分数 > 0），按 BM25 分数排序。

### 8.3 局限性

1. **无语义理解**：BM25 是词袋模型，不理解同义词、上下位关系或数学语义。"group" 和 "G" 被视为完全不同的词。
2. **中文不支持**：分词器 `[A-Za-z0-9_]+` 会丢弃中文字符。如果记录中包含中文描述，搜索将无法匹配。
3. **数学符号丢失**：`∀`、`∈`、`→`、`⊕` 等数学符号不在分词范围内。
4. **无向量化**：与现代嵌入式检索（如 BERT、sentence-transformers）相比，BM25 无法捕捉语义相似性。
5. **全量加载**：每次搜索都加载整个通道的所有记录，无增量索引。

---

## 9. 并发与一致性

### 9.1 当前状态

内存系统**没有并发控制机制**。两个潜在问题：

1. **同一代理的多次 MCP 调用**：Codex 代理的多线程设置（`max_threads = 10`）可能导致多个线程同时写入同一通道文件。JSONL 的追加写入在 POSIX 系统上通常是原子的（对于小于 PIPE_BUF 的写入），但在 Windows 上可能不是。

2. **多个子代理写入同一 `problem_id`**：`recursive-proving` 技能会生成多个子代理，它们共享同一个 `problem_id` 的内存。如果两个子代理同时调用 `memory_append`，可能导致 JSONL 行交错。

### 9.2 实际风险评估

- **JSONL 行交错**：如果两个进程同时写入，可能导致一行 JSON 被另一行截断，产生无效 JSON。`_iter_jsonl` 的 `json.JSONDecodeError` 捕获会跳过这些损坏的行，数据不会丢失（完整的那一行仍然有效），但损坏的行会静默丢失。
- **低概率**：在实际使用中，子代理通常在不同的时间点写入（一个证明步骤完成后才写入），并发写入的概率较低。
- **无恢复机制**：如果发生损坏，没有自动修复机制。

---

## 10. 性能分析

### 10.1 写入性能

`memory_append` 的写入操作是 O(1) 的（追加一行到文件末尾）。主要开销是：
- `json.dumps` 序列化
- 文件 I/O（追加写入）
- 自动 events 日志（额外一次写入）

对于单条记录，延迟在微秒级别。

### 10.2 读取性能

`memory_search` 的读取操作是 O(N) 的，其中 N 是通道中的记录数：
1. 读取整个 JSONL 文件
2. 对每条记录进行 JSON 序列化（作为搜索文档）
3. BM25 分词和评分

对于典型问题（每通道数十到数百条记录），搜索在毫秒级别完成。但对于长时间运行的复杂问题（数千条记录），可能需要数百毫秒。

### 10.3 存储效率

JSONL 格式的存储效率较低：
- 每条记录都包含完整的 `timestamp_utc` 和 `channel` 包装
- JSON 格式有大量引号、括号等结构字符
- `ensure_ascii=False` 对中文友好，但数学符号仍占较多空间

一个典型问题的内存目录可能占用数十 KB 到数百 KB。

---

## 11. 与技能系统的交互

### 11.1 技能如何使用内存

每个技能通过 MCP 工具与内存交互：

| 技能 | 读取 | 写入 |
|------|------|------|
| `obtain-immediate-conclusions` | `memory_search`（查已有结论） | `memory_append`（immediate_conclusions） |
| `search-math-results` | `memory_search`（查上下文） | `memory_append`（events） |
| `query-memory` | `memory_search`（核心操作） | `memory_append`（events） |
| `construct-toy-examples` | `memory_search`（查结论、反例） | `memory_append`（toy_examples） |
| `construct-counterexamples` | `memory_search`（查结论、示例） | `memory_append`（counterexamples、toy_examples、failed_paths） |
| `propose-subgoal-decomposition-plans` | `memory_search`（查全部） | `memory_append`（subgoals、events） |
| `direct-proving` | `memory_search`（查计划、结论） | `memory_append`（proof_steps、subgoals、failed_paths） |
| `recursive-proving` | `memory_search`（查计划、卡点） | `memory_append`（events、branch_states） |
| `identify-key-failures` | `memory_search`（查失败、反例） | `memory_append`（failed_paths、events） |
| `verify-proof` | `memory_search`（查失败报告） | `memory_append`（verification_reports、failed_paths） |

### 11.2 数据生命周期

```
问题开始
    │
    ▼
memory_init ──► 创建目录和空文件
    │
    ▼
obtain-immediate-conclusions ──► immediate_conclusions
    │
    ▼
search-math-results ──► events（搜索记录）
    │
    ▼
construct-toy-examples ──► toy_examples
    │
    ▼
construct-counterexamples ──► counterexamples / toy_examples
    │
    ▼
propose-subgoal-decomposition-plans ──► subgoals
    │
    ▼
direct-proving ──► proof_steps / subgoals（状态更新）
    │
    ├── 成功 ──► 组装证明 ──► verify-proof
    │                              │
    │                              ├── 通过 ──► blueprint_verified.md ✓
    │                              │
    │                              └── 失败 ──► verification_reports
    │                                              │
    │                                              ▼
    │                                    修改证明 ──► 重新循环
    │
    └── 失败 ──► recursive-proving
                     │
                     ▼
               identify-key-failures ──► failed_paths
                     │
                     ▼
               新一轮 propose-subgoal-decomposition-plans
                     │
                     └── ...（循环直到成功或放弃）
```

---

## 12. 设计优缺点总结

### 优点

1. **完整的审计追踪**：`events` 通道记录了所有操作，可以完整重建推理过程。
2. **通道隔离**：不同类型的数据存储在不同文件中，便于针对性查询。
3. **仅追加语义**：不会意外覆盖已有数据，天然支持回溯。
4. **幂等初始化**：`memory_init` 可以安全地多次调用。
5. **路径安全**：双重验证防止目录遍历。
6. **容错读取**：`_iter_jsonl` 跳过损坏的行，不会因单条坏数据崩溃。

### 缺点

1. **代码重复**：两个 MCP 服务器共享约 60% 的代码，应抽取为共享库。
2. **无并发控制**：多线程/多进程写入可能导致 JSONL 损坏。
3. **无索引**：BM25 搜索每次全量加载，无增量索引。
4. **中文不支持**：分词器丢弃中文字符，中文记录无法被搜索到。
5. **无压缩/清理**：长时间运行的问题可能积累大量记录，无 compaction 机制。
6. **BM25 无语义**：无法处理同义词、数学符号等。
7. **硬编码 URL**：LeanSearch 地址硬编码，无法配置。
