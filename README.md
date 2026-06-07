# Rethlas

Rethlas is a natural-language reasoning system for mathematics built around two Codex agents:

- The generation agent reads a math problem from a markdown file and writes an informal proof blueprint.
- The verification agent checks that proof blueprint, produces a structured verdict, and serves as the generation agent's verifier.

The intended deployment order is:

1. Start the verification agent as a local HTTP service.
2. Run the generation agent through Codex.
3. Let the generation agent call the verification service during its proof-and-repair loop.

## Repository Layout

- `agents/generation`: the proof-generation agent
- `agents/verification`: the proof-verification agent
- `rethlas.bat`: Windows launcher menu, suitable for double-click use
- `rethlas.sh`: Linux/macOS launcher menu
- `rethlas.toml`: runtime, provider, and model profile configuration
- `rethlas/`: shared Python helpers for runtime planning and problem path handling

In particular, 
- Original problems are put in `agents/generation/data/`, e.g. unclassified problem `agents/generation/data/example.md`, or classfied problem `agents/generation/data/modrep/modrep.md`, `agents/generation/data/example/example1.md`.
- Zola project to render the results in a static website is in `agents/generation/site/`.

## 1. Install Codex CLI

Install the Codex CLI:

```bash
npm install -g @openai/codex
```


## 2. Clone the Repository

```bash
git clone https://github.com/frenzymath/Rethlas.git
cd Rethlas
```

## 3. Quick Start

On Windows, double-click `rethlas.bat` from File Explorer, or run it from PowerShell or Command Prompt:

```powershell
.\rethlas.bat
```

The launcher menu can:

- run a quick doctor check
- start the verification service in a separate PowerShell window
- run the included example
- run a problem by id, such as `example`, `ns/ns`, or `data/modrep/modrep.md`
- dry-run a problem before starting a long agent run

On Linux or macOS:

```bash
chmod +x ./rethlas.sh
./rethlas.sh
```

The shell launcher provides the same basic menu. The current launchers still use the existing Codex-based agents internally, but they remove the need to remember the `agents/generation` and `agents/verification` working directories.

Runtime providers and model profiles are configured in `rethlas.toml`. The current implemented runtime is `codex-cli`; `openai-compatible` and `anthropic-compatible` provider formats are represented in the configuration and runtime planner so direct API backends can be added without changing the launcher surface.

LiteLLM is the planned shared model-call layer for OpenAI and Anthropic models. Install root runtime dependencies with:

```bash
pip install -r requirements.txt
```

To inspect the effective runtime plan:

```powershell
python -m rethlas.cli doctor
python -m rethlas.cli run ns/ns --dry-run
python -m rethlas.cli status ns/ns
python -m rethlas.cli plan --role generation --problem ns/ns
python -m rethlas.cli plan --role verification --model anthropic-default
python -m rethlas.cli plan --role verification --model mock-verification-correct
```

On Windows, the scriptable wrapper is:

```powershell
.\rethlas.ps1 doctor
.\rethlas.ps1 setup --dry-run
.\rethlas.ps1 verify-server --dry-run
.\rethlas.ps1 run ns/ns --dry-run
.\rethlas.ps1 status ns/ns
```

## 4. Manual Verification Service


```bash
cd agents/verification
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.server:app --host 0.0.0.0 --port 8091
```

Using uv
```bash
cd agents/verification
uv venv 
uv pip install -r requirements.txt
uv run uvicorn api.server:app --host 0.0.0.0 --port 8091
```

On Windows PowerShell:

```powershell
cd agents/verification
.\start_server.ps1
```

## 5. Manual Generation Run


```bash
cd agents/generation
python3 -m venv .venv
source .venv/bin/activate
pip install -r mcp/requirements.txt
./tests/run_example.sh
```

On Windows PowerShell:

```powershell
cd agents/generation
.\tests\run_example.ps1
```

This script:

- reads `agents/generation/data/example.md`
- runs `codex exec` inside `agents/generation`
- writes the run log to `agents/generation/logs/example/example.md`
- writes memory artifacts to `agents/generation/memory/example/`
- writes the draft proof to `agents/generation/results/example/blueprint.md`
- writes the verified proof to `agents/generation/results/example/blueprint_verified.md` if verification succeeds

## 6. Run Your Own Problem

Put your problem in a markdown file under `agents/generation/data/`. Save that as:

```text
agents/generation/data/my_problem.md
```

Then run:

```bash
cd agents/generation
source .venv/bin/activate
PROBLEM_FILE=data/my_problem.md ./tests/run_example.sh
```

On Windows PowerShell:

```powershell
cd agents/generation
.\tests\run_example.ps1 -ProblemFile data/my_problem.md
```

You can group problems in subdirectories under `data/` and the generated artifacts preserve that structure. For example:

```bash
PROBLEM_FILE=data/modrep/modrep.md ./tests/run_example.sh
```

PowerShell equivalent:

```powershell
.\tests\run_example.ps1 -ProblemFile data/modrep/modrep.md
```

Launcher equivalent from the repository root:

```text
Open rethlas.bat or ./rethlas.sh, choose "Run a problem", then enter modrep/modrep.
```

To attach user-provided references to a problem, create a sibling reference directory with the same stem:

```text
agents/generation/data/modrep/modrep.refs/
```

When that directory exists, the generation agent reads its files before using external search.
Reference files may be markdown, LaTeX, plain text, or PDF, but markdown, LaTeX and plain text is prefered over PDF. Actually, PDFs are converted to extracted text under `.extracted/` before the agent runs.

## 7. View Results in the Browser

- `agents/generation/site`: Zola site for browsing results in the browser

Results are markdown files with LaTeX math. To render them properly, a local [Zola](https://www.getzola.org/) site using the [MATbook](https://www.getzola.org/themes/matbook/) theme is included.

### Prerequisites

Install Zola.

Zola can be easily installed using your package manager in terminal. For example, on Mac, you simply run

```bash
brew install zola
```

and on ArchLinux, run

```bash
sudo pacman -S zola
```

For other operating systems, please see [Zola installation](https://www.getzola.org/documentation/getting-started/installation/).

### Serve

From `agents/generation/`:

```bash
./site/serve.sh
```

On first run this automatically clones the [MATbook](https://www.getzola.org/themes/matbook/) theme. Then it syncs all results from `results/` into the site and starts a local server. Open http://localhost:3264 in your browser.

Each problem  in `agents/generation/data/your_category`  will be a section in a chapter called `your_category`, while problems directly in `agents/generation/data` will be under `unclassified` chapter.

### Update the MATbook Theme

```bash
./site/setup_theme.sh
```

This pulls the latest version from the [MATbook repository](https://github.com/srliu3264/MATbook).
