# 技能系统 - 子系统分析

## 1. 概述

Rethlas 使用基于技能的架构，每种数学推理能力封装为一个技能。技能由 `SKILL.md` 文件（代理指令）和 `agents/openai.yaml` 文件（Codex 代理配置）定义。生成代理有 10 个技能，验证代理有 3 个。

**关键目录：**
- `agents/generation/.agents/skills/` — 10 个生成技能
- `agents/verification/.agents/skills/` — 3 个验证技能

## 2. 技能结构

每个技能由以下文件组成：

```
.agents/skills/{skill-name}/
├── SKILL.md              # 技能定义（指令、合约、流程）
└── agents/
    └── openai.yaml       # Codex 代理接口配置
```

### 2.1 SKILL.md 结构

每个 SKILL.md 遵循统一模板：

```markdown
---
name: {skill-name}
description: {一行描述}
---

# {技能标题}

{目的段落}

## Input Contract（输入合约）
{技能从内存/上下文中读取什么}

## Procedure（流程）
{编号的逐步指令}

## Output Contract（输出合约）
{要追加到内存的 JSON Schema}

## MCP Tools（MCP 工具）
{技能使用的 MCP 工具列表}

## Failure Logging（失败日志）
{技能失败时记录什么}
```

### 2.2 openai.yaml 结构

```yaml
interface:
  display_name: "人类可读名称"
  short_description: "简短描述"
  default_prompt: "使用 ${skill-name} 来..."

policy:
  allow_implicit_invocation: true
```

## 3. 生成技能（10 个）

### 3.1 `obtain-immediate-conclusions`（获取直接结论）

**目的**：从定理声明中提取直接数学结论，先于推测性推理。

**流程：**
1. 规范化记号，用等价形式重述。
2. 列出从定义和基本操作直接推出的结论。
3. 分为必要条件和候选充分条件。
4. 标记置信度和证明类型。
5. 标记脆弱结论以供反例测试。

**输出**：写入 `immediate_conclusions`，包含 `is_fragile`、`fragility_reason`、`suggested_followup`。

### 3.2 `search-math-results`（搜索数学结果）

**目的**：数学背景和相关结果的默认检索工作流。

**流程：**
1. 用完整数学声明查询 `search_arxiv_theorems`。
2. 如果找到有用结果，下载论文、提取文本、阅读证明。
3. 从论文上下文中展开定义。
4. 提取技巧和证明模式。
5. 如果需要，回退到 Codex 内置网络搜索。

**输出**：写入 `events`，包含 `useful_references`，其中有 `expanded_definitions`、`applicability_check`、`proof_insights`。

### 3.3 `query-memory`（查询内存）

**目的**：从内存通道中检索之前保存的产物。

**流程：**
1. 形成具体的自然语言查询。
2. 选择最小的相关通道列表。
3. 调用 `memory_search`。
4. 总结有用的命中结果。

**输出**：写入 `events`，包含 `useful_hits` 和 `results_summary`。

### 3.4 `construct-toy-examples`（构造玩具示例）

**目的**：生成满足假设和结论的更简单示例。

**流程：**
1. 构造更简单的情况（低次、小维、标准对象）。
2. 验证所有假设成立。
3. 验证结论成立。
4. 研究每个假设生效的位置。
5. 识别模式、不变量、证明思路。

**输出**：写入 `toy_examples`，包含 `where_assumptions_take_effect`、`observed_pattern`。

### 3.5 `construct-counterexamples`（构造反例）

**目的**：通过找到满足假设但违反结论的示例来主动证伪提议的猜想。

**流程：**
1. 识别需要保持的假设和需要失败的结论。
2. 搜索标准障碍、病态构造。
3. 决定状态：`refuted`（已证伪）、`not_refuted`（未证伪）或 `inconclusive`（不确定）。
4. 如果已证伪，标记受影响的分支为无效。
5. 如果未证伪，视为证据（非证明）。

**输出**：写入 `counterexamples`，包含 `status`、`assumptions_satisfied`、`failed_conclusion`、`impact`。

### 3.6 `propose-subgoal-decomposition-plans`（提出子目标分解计划）

**目的**：提出多个实质不同的分解计划。

**流程：**
1. 收集约束信息（示例、失败、障碍）。
2. 提出实质不同的计划。
3. 对每个计划：主要思路、有序子目标、可行性、避免的失败。
4. 将每个计划交给 `$direct-proving`。

**输出**：写入 `subgoals`，包含 `plan_summary`、`subgoals`、`motivation`、`uses_information_from`。

### 3.7 `direct-proving`（直接证明）

**目的**：通过尝试直接证明所有子目标来筛选分解计划。

**流程：**
1. 每次处理一个计划。
2. 对每个子目标，使用搜索结果、示例、反例。
3. 尝试适应类似定理的证明思路。
4. 记录状态：`solved`（已解决）、`partial`（部分）或 `stuck`（卡住）。
5. 如果卡住，识别关键失败模式。

**输出**：写入 `proof_steps`，包含 `attempt_summary`、`status`、`key_stuck_points`、`migration_failures`。

### 3.8 `recursive-proving`（递归证明）

**目的**：在直接筛选后，为每个分解计划生成一个子代理。

**流程：**
1. 确认所有计划都已筛选。
2. 生成子代理（每个计划一个）。
3. 每个子代理获得：完整定理、分配的计划、卡点、AGENTS.md 指令。
4. 子代理可以生成自己的子代理（递归）。
5. 收集报告；如果任何计划成功，组装证明。

**输出**：写入 `events`，包含 `plan_ids`、`subagent_ids`、`successful_plan_ids`、`failed_plan_ids`。

### 3.9 `identify-key-failures`（识别关键失败）

**目的**：综合失败计划中的共同卡点。

**流程：**
1. 收集所有失败计划的报告。
2. 列出每个计划的关键卡点。
3. 识别跨失败的共同模式。
4. 总结对下一轮规划的影响。

**输出**：写入 `failed_paths`，包含 `plan_failures`、`common_failures`、`implications_for_next_plans`。

### 3.10 `verify-proof`（验证证明）

**目的**：使用验证服务验证候选证明。

**流程：**
1. 读取 `blueprint.md` 文本。
2. 检查是否包含完整证明（非部分）。
3. 调用 `verify_proof_service` MCP 工具。
4. 读取并持久化验证报告。
5. 如果通过，重命名为 `blueprint_verified.md`。

**输出**：写入 `verification_reports`，包含完整验证输出。

## 4. 验证技能（3 个）

### 4.1 `verify-sequential-statements`（顺序验证声明）

**目的**：按文本顺序检查每个声明/子证明。

**流程：**
1. 从 Statement 提取假设。
2. 按顺序遍历证明。
3. 对每项：检查推理有效性、定理应用、缺失假设。
4. 仔细审计未使用的假设。
5. 分类为 `critical_error` 或 `gap`。

**输出**：写入 `statement_checks`，包含 `critical_errors` 和 `gaps` 数组。

### 4.2 `check-referenced-statements`（检查引用声明）

**目的**：验证每个外部论文引用。

**流程：**
1. 用完整声明文本查询 `search_arxiv_theorems`。
2. 将返回结果与引用声明比较。
3. 从引用论文的上下文中展开定义。
4. 检查跨上下文的术语不匹配。
5. 如果未找到则回退到网络搜索。
6. 如果引用未验证则发出关键错误。

**输出**：写入 `reference_checks`，包含 `arxiv_match_found`、`web_match_found`、`context_expansion`。

### 4.3 `synthesize-verification-report`（综合验证报告）

**目的**：汇总发现并生成最终判定。

**流程：**
1. 收集所有关键错误和间隙。
2. 构建 `verification_report` 对象。
3. 应用严格判定规则。
4. 如果错误则生成修复提示。
5. 根据 JSON Schema 验证。
6. 写入 `verification.json`。

**输出**：最终 `verification.json` 文件。

## 5. 技能调用流程

```
AGENTS.md（控制循环）
     │
     ├── 评估状态
     │
     ├── 根据启发式选择技能
     │
     ├── 调用技能
     │   ├── 读取 Input Contract（从内存）
     │   ├── 执行 Procedure
     │   ├── 写入 Output Contract（到内存）
     │   └── 记录失败到 events
     │
     ├── 更新分支状态
     │
     └── 循环或停止
```

## 6. 观察

1. **覆盖全面**：10 个生成技能覆盖了从初始探索到最终验证的完整数学推理生命周期。
2. **合约清晰**：每个技能都有明确的输入/输出合约和 JSON Schema，使系统可预测且可调试。
3. **失败感知**：每个技能都有失败日志部分，确保即使不成功的尝试也能为未来规划产生有用信息。
4. **可组合**：技能可以调用其他技能（如 `propose-subgoal-decomposition-plans` 将计划交给 `direct-proving`），创建灵活的推理管道。
5. **递归能力**：`recursive-proving` 技能通过子代理委派实现多证明策略的并行探索。
