# 站点渲染 - 子系统分析

## 1. 概述

Rethlas 包含一个基于 Zola 的静态站点，用于在浏览器中浏览证明结果，支持正确的 LaTeX 数学渲染。站点使用 MATbook 主题，自动从 `results/` 目录同步结果。

**关键文件：**
- `agents/generation/site/config.toml`（27 行）— Zola 配置
- `agents/generation/site/serve.sh`（128 行）— 内容同步与服务器启动
- `agents/generation/site/setup_theme.sh`（17 行）— 主题安装/更新
- `agents/generation/site/transform_math.py`（110 行）— 数学分隔符转换器
- `agents/generation/site/templates/index.html`（140 行）— 主模板
- `agents/generation/site/content/_index.md`（5 行）— 着陆页

## 2. 架构

```
results/
├── example/
│   └── blueprint_verified.md
└── algebra/
    └── modrep/
        └── blueprint.md
         │
         ▼ (serve.sh sync_content)
site/content/
├── _index.md
├── unclassified/
│   ├── _index.md
│   └── example.md
└── algebra/
    ├── _index.md
    └── modrep/
        ├── _index.md
        └── modrep.md
         │
         ▼ (zola serve)
http://localhost:3264
```

## 3. 内容同步（`serve.sh`）

### 3.1 同步过程

`sync_content` 函数的工作流程：

1. 清除已生成的内容（保留 `_index.md`）。
2. 扫描 `results/` 中包含 `blueprint_verified.md` 或 `blueprint.md` 的目录。
3. 对每个问题目录：
   - 确定分类（父目录）或对根级问题使用 `unclassified`。
   - 为每个分类创建 section `_index.md` 文件。
   - 使用 `transform_math.py` 转换数学分隔符。
   - 添加 Zola frontmatter（标题、日期、权重、`math = true`）。
4. 生成带适当权重的 section 索引用于排序。

### 3.2 文件选择优先级

- `blueprint_verified.md`（优先 — 已验证的证明）
- `blueprint.md`（回退 — 未验证的草稿）

### 3.3 分类映射

- `results/example/` -> `content/unclassified/example.md`
- `results/algebra/modrep/` -> `content/algebra/modrep/modrep.md`

## 4. 数学转换（`transform_math.py`）

### 4.1 问题

Zola 的 markdown 引擎（CommonMark）在 MathJax 之前处理内容，导致三类破坏：

1. `\(` 和 `\[` 被视为 CommonMark 转义序列，分隔符消失。
2. 数学内部的 `\!`、`\,`、`\;`、`\:`、`\{`、`\}` 是 CommonMark 转义 — 反斜杠被剥离。
3. 数学中的下划线（`_`）可能被误解析为强调标记。

### 4.2 解决方案

1. **分隔符转换**：`\(...\) -> $...$` 和 `\[...\] -> $$...$$`（不是 CommonMark 转义）。
2. **反斜杠加倍**：`\!` -> `\\!`，这样 CommonMark 为 MathJax 生成 `\!`。
3. **下划线保护**：`_x` -> `_ x`（添加空格防止强调解析）。

### 4.3 处理顺序

1. 先处理已有的 `$...$` 和 `$$...$$` 区域。
2. 转换 `\(...\)` 和 `\[...\]` 分隔符。
3. 转换反引号数学（标签引用如 `lem:xxx` 除外）。

## 5. Zola 配置（`config.toml`）

```toml
base_url = "http://localhost:3264"
theme = "MATbook"
title = "rethlas results"
description = "Proof generation results"

[extra]
tikzjax = false
mathjax = true

[extra.booktheme]
home_url = "/"
book_number_chapters = true
```

- 启用 MathJax 进行 LaTeX 渲染。
- MATbook 主题提供类书籍导航。
- GitHub-dark 语法高亮。

## 6. 模板（`index.html`）

模板包含：

- **MathJax 3**：配置了 AMS、cancel 和 amscd 包。支持行内（`$...$`）和行间（`$$...$$`）数学。
- **TikZJax**：已包含但禁用（配置中 `tikzjax = false`）。
- **深色/浅色主题**：系统偏好检测，localStorage 持久化。
- **侧边栏折叠**：可折叠侧边栏，localStorage 持久化。
- **搜索**：基于 ElasticLunr 的搜索索引。
- **Font Awesome**：图标库。

## 7. 主题管理（`setup_theme.sh`）

- 如果主题不存在，从 GitHub 克隆 MATbook 主题。
- 如果已存在，通过 `git pull --ff-only` 更新。
- 主题存储在 `site/themes/MATbook/`。

## 8. 观察

1. **自动同步**：`serve.sh` 脚本自动处理所有内容同步，用户只需运行 `./site/serve.sh`。
2. **数学兼容性**：`transform_math.py` 解决了 CommonMark 和 MathJax 之间的实际兼容性问题，这是静态站点生成器的常见痛点。
3. **基于章节的组织**：问题按分类组织为章节，未分类问题作为默认章节。
4. **已验证证明优先**：站点优先显示 `blueprint_verified.md` 而非 `blueprint.md`，默认展示已验证结果。
5. **无构建步骤**：站点直接使用 `zola serve`，同时处理构建和提供服务，无需单独的构建步骤。
