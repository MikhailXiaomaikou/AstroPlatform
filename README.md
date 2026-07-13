# Standard Astro

> **可审计的观测宇宙学 AI 工作台。**
>
> **An auditable AI workbench for observational cosmology.**

**研究 Alpha · 仅限宇宙学 / Research alpha · Cosmology only**

Standard Astro 让 AI 协助规划、执行、检查和整理观测宇宙学研究，同时阻止模型把“记忆里的数字”冒充真实数据。你用自然语言提问，模型提出工具调用，后端执行已注册的数据集、似然、拟合和证据检查。

Standard Astro helps plan, run, check, and write up observational-cosmology research. The model proposes actions; the backend executes registered tools and verifies the evidence.

> **先说清楚 / Important:** 这是一个研究 Alpha 工作台，不是“自动复现任意论文”的机器。超出当前能力时，系统应明确说明缺少什么，而不是猜测结果。
>
> This is a research-alpha workbench, not a general “reproduce any paper” system. Unsupported requests should become explicit capability gaps, never guessed results.

想先检查它是否真的防止编造？直接阅读 [科学诚实证据 / Honesty Evidence](./docs/HONESTY_EVIDENCE.md)，其中也公开记录了失败案例与未解决问题。

## 一句话原理 / The core idea

**模型提出，后端执行，证据决定能否下结论。**

**The model proposes; the backend executes; evidence decides what may be claimed.**

```text
研究问题 Research question
        ↓
模型提出结构化工具调用 Model proposes tool calls
        ↓
后端执行数据、似然与验证 Backend runs data, likelihoods, and checks
        ↓
结果 + 数据来源 + 引用 Result + provenance + citations
```

强数值结论必须能追溯到**当前轮次**的工具结果、数据集和引用。空结果、不可执行的似然或仅有论文摘要时，平台应返回“尝试了什么 / 缺少什么 / 下一步能做什么”。

Strong numerical claims must trace to a **current-turn** tool result, dataset, and citation. Missing evidence produces a clear gap report, not model-memory filler.

## 最短本地启动 / Minimal local start

需要 **Python 3.11** 和 **Node.js 20+**。从仓库根目录打开两个终端。

Prerequisites: **Python 3.11** and **Node.js 20+**. Open two terminals at the repository root.

**终端 1：后端 / Terminal 1: backend**

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock
uvicorn app.main:app --reload --port 8000
```

**终端 2：前端 / Terminal 2: frontend**

```bash
cd frontend
npm ci
npm run dev
```

然后打开 / Then open:

- 应用 / App: [http://localhost:5173/chat](http://localhost:5173/chat)
- 后端健康检查 / Backend health: [http://localhost:8000/health](http://localhost:8000/health)
- 交互式 API 文档 / Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

本地开发无需 `.env`：默认使用开发模式和仓库 `data/` 目录下的 SQLite 数据库。首次启动会生成临时开发密钥，因此登录令牌和已保存的 API 密钥可能在重启后失效。

Local development needs no `.env`: it defaults to dev mode and SQLite under the repository `data/` directory. Startup-generated development secrets are temporary, so tokens and saved API keys may not survive a restart.

## 第一次使用 / First run

1. 打开 **Account**，添加 Anthropic、OpenAI 或 DeepSeek API key（BYOK，由后端加密保存）；也可以使用下面的本地订阅 CLI。
2. 打开 **Chat**，先试一个小而可验证的问题：
   - `列出当前可执行的宇宙学数据集及其执行模式。`
   - `Build a DESI DR2 BAO + BBN likelihood and explain every data source.`
   - `Compute the Planck 2018 theory CMB TT power spectrum.`
3. 检查结果卡片里的数据版本、引用和 **Copy Acknowledgement**，不要只看聊天文字。

1. Add an Anthropic, OpenAI, or DeepSeek key under **Account**, or use a local subscription CLI.
2. Ask a small, verifiable question in **Chat**.
3. Inspect the result provenance, citations, and acknowledgement—not only the prose answer.

更多产品操作示例见 [快速使用指南 / Product Quick Start](./docs/QUICKSTART.md)。

## 能做什么 / What it can do

- **对话式研究 / Research chat** — 查询档案、运行 ADQL、搜索文献、提取论文表格、分析、拟合和起草论文。
- **受控宇宙学流程 / Controlled cosmology workflows** — 使用固定版本的 BAO、SN、CMB、H(z)、fσ8 等数据产品；可执行项运行真实后端，不可执行项保留为明确缺口。
- **论文工具化 / Paper-to-tool mining** — 把方法章节转成带引用、可复用的能力规格。
- **导出 / Export** — 生成论文草稿、BibTeX、致谢文本、Notebook、图和可复现包；草稿默认保持私有，直到用户主动发布。
- **科学防护 / Scientific guardrails** — 检查数据来源、合成数据、数值、引用、链诊断、模型比较有效性和重叠数据集。

The main surface is one research conversation backed by registered tools, provenance-aware execution, guarded inference, and reproducible exports.

## 科学边界 / Scientific boundaries

以下规则比“顺利给出答案”更重要：

- `CONFIG_READY`、论文摘要、旧聊天记录和用户假设都**不能**支持后验、拟合、显著性或张力数值。
- 文献搜索只能支持背景与引用；测量结论必须来自提取出的表格行或可发布的工具结果。
- 合成 `run_python` 输出不能冒充观测数据。
- 低 ESS、未收敛、探索性或仅配置的数据必须保持可见，但不能被包装成可靠结论。
- 有重叠的数据集必须遵守 `do_not_combine_with`，不能不安全地叠加。
- 当前仓库只维护**观测宇宙学**模块；太阳系、系外行星和其他垂直方向已移至相邻的 `standard-astro-verticals` 仓库。

These rules are non-negotiable: configuration is not evidence, literature is not a measurement table, synthetic output is not observation, and failed or incomplete inference must remain visibly preliminary.

<details>
<summary><strong>当前 Alpha 验收目标 / Current alpha contract</strong></summary>

当前严格里程碑是 DESI 2024 VI `DESI+CMB+PantheonPlus` 四个参数区间的
已知目标、预注册复现。它不是模型偏好或“发现动态暗能量”的检验。离线流程
必须按 `preflight → generate → run → analyze → grade` 执行，并且只有论文
匹配的 PR3 `plik` + ACT/PR4 lensing + DESI DR1 BAO + Pantheon+ profile 有资格
进入验收：

- 识别科学流程和可能适用的已注册数据集；
- 有受控压缩版或初步基线时真实运行；
- 否则生成可审计的缺失能力矩阵；
- 避免无依据的后验、拟合、显著性、异常或引用结论；
- 清楚说明达到论文级一致性还需要什么。

这不是“95% 论文复现率”的声明。最高自动状态只能是
`A_READY_PENDING_EXTERNAL_REVIEW`；`strict_A_count` 在独立外部复核完成前始终
为零。当前状态仍为 `WITHHELD`：正式链和六项模型充分性证据尚未全部实跑，
而且本次实现提示已公开目标值，不能声称 analyst-blinded。完整门禁与偏差披露
见 [DESI `w0wa` A-readiness protocol](./docs/DESI_W0WA_A_READINESS_PROTOCOL.md)。

The current strict milestone is a known-target, preregistered reproduction of
the four DESI+CMB+PantheonPlus parameter intervals. It remains `WITHHELD` until
the formal chains and all six adequacy requirements have actually run; software
tests or generated configs are not scientific completion.

</details>

<details>
<summary><strong>初步复现记录与限制 / Preliminary reproduction record and limits</strong></summary>

一次独立的全 CMB Cobaya + CAMB 本地运行（DESI DR1 BAO + Pantheon+ + 无 `clik` 的 Planck 2018 组合）对 DESI 2024 VI 做出了有用的**初步参数交叉检查**。它不是经过验证的复现，也不能用于报告探测显著性：链只达到 R-1(means)=0.047、R-1(bounds)=0.13，且没有运行匹配、校准后的固定 ΛCDM 比较。

因此，历史后验均值的 Mahalanobis 位移不能被称为 DESI 的模型比较统计量。完整数值和限制见 [全 CMB 复现记录](./backend/scripts/cobaya/README_full_cmb_reproduction.md)。这仍是离线脚本流程，不是自主 Chat 路径；两条路径在通过四条独立链、rank-normalized R-hat、ESS、数据与似然保真度门槛之前都只能称为初步结果。

A dedicated full-CMB local run produced a useful **preliminary parameter cross-check**, not a validated reproduction or detection-significance result. See the linked record for the exact limitations.

</details>

## 模型接入 / Model access

### 方式 A：自带 API key / Option A: BYOK

在 **Account** 页面保存 Anthropic、OpenAI 或 DeepSeek key。参考生产配置默认关闭平台付费共享 key，公开聊天为 BYOK-only；运维人员如需配置其他服务端集成，请使用 [Deployment](./DEPLOYMENT.md) 中的显式环境变量。

Save a provider key under **Account**. Operator-owned integrations use the
explicit environment settings in [Deployment](./DEPLOYMENT.md); those settings
do not turn public Chat into a shared-key path. The reference production setup
keeps platform-funded shared chat disabled by default.

### 方式 B：本地订阅 CLI / Option B: local subscription CLI

如果本机已经登录 Claude Code 或 OpenAI Codex CLI，可在 `backend/` 中启动：

```bash
CLAUDE_CLI_ENABLED=1 uvicorn app.main:app --reload --port 8000
# 或 / or
OPENAI_CLI_ENABLED=1 uvicorn app.main:app --reload --port 8000
```

然后在 Chat 的模型选择器中选择 **Local → Claude CLI** 或 **Local → OpenAI CLI**。这会消耗对应订阅额度。两种桥接都只适用于可信任的单用户本机，并在生产环境中被拒绝。

Choose **Local → Claude CLI** or **Local → OpenAI CLI** in Chat. These bridges use the corresponding subscription allowance and are restricted to trusted, single-user local development.

<details>
<summary><strong>CLI 隔离说明 / CLI isolation notes</strong></summary>

CLI 子进程从空的临时目录启动，只接收精简环境，不会继承数据库、JWT、对象存储、加密或模型 API 密钥。Claude 的内置工具、设置和会话被禁用；Codex 默认忽略用户配置与规则，并且无条件关闭内置 shell/exec 工具、运行在只读沙箱中。它们仍是本地进程，不是操作系统级隔离的模型服务，因此不要把它们暴露为多人远程服务。

The child process starts in an empty temporary directory with a minimal environment. This reduces secret exposure but is not an OS-level security boundary.

</details>

## 本地 Bot 控制台 / Local Bot Console

`/bot` 是**可选的本地自托管控制面板**：它把一个本地 OpenAI CLI 对话窗口，与仓库外的个人每周研究自动化流水线连接起来，可查看状态、读取报告和触发 macOS `launchd` 任务。

`/bot` is an optional local dashboard for a self-hoster's external weekly research pipeline and a chat window pinned to the local OpenAI CLI.

最小聊天配置 / Minimum chat setup:

```bash
cd backend
OPENAI_CLI_ENABLED=1 \
BOT_CONSOLE_MODEL_ID=your-openai-cli-model-id \
uvicorn app.main:app --reload --port 8000
```

如需研究状态、报告和手动触发，把这些变量加到同一个启动命令中：

```bash
cd backend
OPENAI_CLI_ENABLED=1 \
BOT_CONSOLE_MODEL_ID=your-openai-cli-model-id \
COSMO_SECOND_ORDER_ROOT=/absolute/path/to/your/pipeline \
RESEARCH_LAUNCHD_LABEL=com.example.research-weekly \
BOT_LAUNCHD_LABEL=com.example.notification-bot \
uvicorn app.main:app --reload --port 8000
```

`BOT_LAUNCHD_LABEL` 只用于显示通知 Bot 的在线状态，可按需省略。

它要求已登录用户、浏览器来源与请求端都在本机回环地址；生产环境直接禁用。前端开发模式默认显示 `/bot`，生产构建只有显式设置 `VITE_BOT_CONSOLE_ENABLED=1` 才显示入口，但后端仍会在生产环境拒绝请求。

The console requires an authenticated loopback user and an approved local browser origin. It is disabled server-side in production even if a frontend link is accidentally enabled.

仓库默认要求登录。可信任的单用户开发机可以显式同时设置
`ENV=dev LOCAL_DEV_NO_AUTH=1`，此时后端使用本地开发身份而不验证 JWT；安全边界只剩回环绑定和请求端地址，浏览器请求在带有 Origin 时还必须精确匹配。无 Origin 的本机 CLI 请求仍可访问，因此不要在共享电脑或可被远程访问的服务上启用这个例外。

Authentication is the default. `ENV=dev LOCAL_DEV_NO_AUTH=1` is an explicit
single-user exception protected by loopback transport; browser Origin is
checked when present, while local non-browser requests may omit it. Never
enable this exception on a shared or remotely reachable host.

## 数据、范围与技术栈 / Data, scope, and stack

当前有 6 个 provenance-v2 档案连接器：**VizieR、Gaia DR3、SIMBAD、NED、2MASS、ALMA Science Archive**（ALMA 仅限观测元数据）。另外 17 个连接器键在具备自己的版本化来源信息前返回 `UNAVAILABLE` 维护提示，不会伪造数据。

Six provenance-v2 archives are live. Other connector keys fail closed with an `UNAVAILABLE` maintenance state until versioned provenance ships.

宇宙学似然数据与档案连接器分开管理；执行模式和可支持的结论范围见 [数据源映射 / Source Mapping](./docs/SOURCE_MAPPING.md)。运行时研究范围由 `ASTRO_RESEARCH_FOCUS` 控制，默认并收敛到 `cosmology`。

<details>
<summary><strong>技术栈 / Technology stack</strong></summary>

| 层 / Layer | 技术 / Stack |
|---|---|
| 前端 / Frontend | React 19, strict TypeScript, Vite, Plotly |
| 后端 / Backend | FastAPI, async SQLAlchemy, Pydantic v2, SSE |
| AI | Claude, OpenAI, DeepSeek, local OpenAI-compatible servers, Claude Code / Codex CLI |
| 科学计算 / Science | astropy, astroquery, emcee, dynesty, Cobaya, CAMB, ArviZ |
| 存储与任务 / Storage & jobs | SQLite (dev), PostgreSQL (prod), local or S3-compatible object storage, Redis/Celery |

</details>

## 项目入口 / Project map

| 路径 / Path | 作用 / Purpose |
|---|---|
| `backend/app/main.py` | FastAPI 后端入口 / backend entrypoint |
| `frontend/src/main.tsx` | React 前端入口 / frontend entrypoint |
| `frontend/src/App.tsx` | 页面路由与应用壳 / routes and application shell |
| `frontend/src/pages/Chat/ChatPage.tsx` | 主要用户界面 / primary research surface |
| `backend/app/services/ai_tools/` | 注册工具与分发 / registered tools and dispatch |
| `render.yaml` | 参考生产拓扑 / reference production topology |
| `docker-compose.yml` | 本地完整服务栈 / local full-service stack |

## 开发与验证 / Development and validation

```bash
# Backend
cd backend
./venv/bin/ruff check app tests
./venv/bin/pytest tests -q

# Frontend
cd ../frontend
npm run lint
npm run test
npm run build
```

科学数据、似然或防编造门槛的改动还需要运行对应 benchmark、registry audit 和盲测。完整规则见 [贡献指南 / Contributing](./CONTRIBUTING.md) 与 [开发手册 / Agent Handbook](./CLAUDE.md)。

Scientific-data, likelihood, and anti-fabrication changes require additional benchmarks, registry audits, and blind tests. The contribution guide is currently pre-contribution guidance because licensing and DCO/CLA policy are not finalized.

<details>
<summary><strong>本地安全默认值与启动故障 / Local safety defaults and startup failures</strong></summary>

- 任意 `run_python` 执行默认禁用。内置进程内与子进程执行器只是崩溃隔离，不是操作系统安全沙箱。只有可信任的单用户开发机可显式设置 `SANDBOX_BACKEND=subprocess`；托管或多人环境禁止启用。
- 如果启动报错 `Provenance registry freshness check failed`，说明 `backend/app/services/provenance_v2/fallback_registry.yaml` 中至少一个来源超过 180 天未验证。请重新核验并更新日期；不要削弱这个门槛。流程见 [Deployment 的 Provenance-v2 Startup Guard](./DEPLOYMENT.md)。
- 生产所需的持久密钥、PostgreSQL、Redis、对象存储和完整切换步骤见部署文档，不要直接照搬开发默认值。

Arbitrary Python execution is disabled by default. The provenance freshness gate also applies to local development and must not be bypassed. Production requires durable secrets and infrastructure described in the deployment docs.

</details>

## 文档导航 / Documentation

### 开始使用 / Start here

- [产品快速指南 / Product Quick Start](./docs/QUICKSTART.md)
- [观测宇宙学 Beta / Observational Cosmology Beta](./docs/OBSERVATIONAL_COSMOLOGY_BETA.md)
- [API Reference](./docs/API_REFERENCE.md)

### 科学可信度 / Scientific integrity

- [防编造证据 / Honesty Evidence](./docs/HONESTY_EVIDENCE.md)
- [数据源映射 / Source Mapping](./docs/SOURCE_MAPPING.md)
- [盲测目标 / Blind-test Target](./docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md)
- [盲测记录 / Blind-test Log](./docs/BLIND_RESEARCH_TESTING_LOG.md)
- [科学严谨性修复账本 / Scientific-rigor Ledger](./docs/SCIENTIFIC_RIGOR_REMEDIATION.md)
- [参考文献 / References](./docs/REFERENCES.md)

### 架构与运维 / Architecture and operations

- [Architecture](./ARCHITECTURE.md)
- [Deployment](./DEPLOYMENT.md)
- [生产切换清单 / Production Cutover Checklist](./docs/PRODUCTION_CUTOVER_CHECKLIST.md)
- [运维与恢复 / Operations and Recovery](./docs/OPERATIONS_RUNBOOK.md)
- [Changelog](./CHANGELOG.md)

### 项目政策 / Project policies

- [Security](./SECURITY.md)
- [Privacy](./PRIVACY.md)
- [Contributing](./CONTRIBUTING.md)
- [Agent / Development Handbook](./CLAUDE.md)

## 数据来源与致谢 / Data sources and acknowledgements

Standard Astro 连接或引用 VizieR、Gaia、SIMBAD、NED、2MASS、ALMA Science Archive 及相关论文数据产品。研究输出应保留具体数据发布版本、原始引用和服务要求的致谢文本；平台的 **Copy Acknowledgement** 与论文导出功能会根据本轮 provenance 生成相应内容。

Standard Astro integrates or cites major astronomy archives and paper-derived data products. Research outputs should retain release-specific citations and required acknowledgements; generated text must still be reviewed against each source's current terms.

项目不代表这些档案、机构或模型提供商，也不以本仓库说明替代它们各自的使用条款。详细引用见 [References](./docs/REFERENCES.md)。

## 许可 / License

项目许可尚未最终确定，当前仓库没有发布 `LICENSE` 文件。不要假定拥有再分发或第三方贡献合并权；状态更新见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

Licensing is not yet finalized, and this repository currently publishes no `LICENSE` file. Do not assume redistribution or contribution-merge rights.
