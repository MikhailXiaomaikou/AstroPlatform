# Standard Astro

[English](./README.md) | [简体中文](./README.zh-CN.md)

> **可审计的观测宇宙学 AI 工作台。**

**研究 Alpha · 仅限宇宙学**

Standard Astro 帮助研究者规划、执行、检查和整理观测宇宙学工作。模型提出结构化
操作，后端执行已注册的数据集、似然、拟合和证据检查。

> 这不是“自动复现任意论文”的机器。超出当前能力的请求必须转化为明确的能力缺口，
> 不能靠猜测填充结果。

已知失败、限制和防编造证据见[科学诚实证据](./docs/HONESTY_EVIDENCE.md)。

## 工作原理

```text
研究问题
        ↓
模型提出工具调用
        ↓
后端执行数据、似然与验证
        ↓
结果 + 数据来源 + 引用
```

强数值结论必须能追溯到**当前轮次**的工具结果、数据集和引用。证据不足时应返回
能力缺口，不能用模型记忆补数字。

## 快速启动

需要 **Python 3.11** 和 **Node.js 20+**。

```bash
# 终端 1：后端
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock
uvicorn app.main:app --reload --port 8000

# 终端 2：前端
cd frontend
npm ci
npm run dev
```

打开：

- 应用：[http://localhost:5173/chat](http://localhost:5173/chat)
- 健康检查：[http://localhost:8000/health](http://localhost:8000/health)
- API 文档：[http://localhost:8000/docs](http://localhost:8000/docs)

本地开发默认使用 SQLite，不需要 `.env`。可在 **Account** 中添加 Anthropic、
OpenAI 或 DeepSeek key，也可启用已登录的本地 Claude Code / Codex CLI 桥接。
安全配置见[产品快速指南](./docs/QUICKSTART.md)和
[部署文档](./DEPLOYMENT.md)。

推荐先问：

- `列出当前可执行的宇宙学数据集及其执行模式。`
- `构建 DESI DR2 BAO + BBN 似然，并解释每个数据来源。`
- `计算 Planck 2018 理论 CMB TT 功率谱。`

请检查结果中的数据版本、来源、引用和致谢，不要只看聊天文字。

## 核心能力

- 研究对话、档案查询、ADQL、文献搜索和论文表格提取
- 固定版本的 BAO、SN、CMB、H(z) 和 fσ8 流程
- 显示收敛与证据状态的模型拟合
- 从论文方法中提取可复用工具能力
- 导出草稿、BibTeX、Notebook、图和证据包
- 可选的本地 `/bot` 个人研究流水线控制面板

当前有 6 个 provenance-v2 档案连接器：VizieR、Gaia DR3、SIMBAD、NED、
2MASS 和 ALMA 观测元数据。其他连接器在具备版本化来源前会明确失败。

## 科学边界

- 配置、论文摘要、旧聊天记录和用户假设都不能支持后验、拟合、显著性或张力数值。
- 文献只能支持背景和引用；测量结论必须来自提取出的数据行或可发布的工具结果。
- 合成 Python 输出不能冒充观测数据。
- 低 ESS、未收敛、探索性或仅有配置的结果必须保持可见，但不能支持强结论。
- 有重叠的数据集必须遵守 `do_not_combine_with`。
- 当前 DESI `w0wa` 任务不输出 Wilks p 值、Gaussian-equivalent sigma、
  Bayes-factor 偏好、ΛCDM 排除或“发现动态暗能量”等结论。

### 当前 DESI `w0wa` 里程碑

当前目标是按以下固定流程复现 DESI 2024 VI 表 3 中
`DESI+CMB+PantheonPlus` 的四个参数区间：

```text
preflight → generate → run → analyze → grade
```

当前状态是 **`WITHHELD`**：`A_ready_count=0`、`strict_A_count=0`。
正式链和六项模型充分性要求尚未全部完成。实现请求已经公开目标值，而冻结的协议
裁决者注册表为空，因此本地评分器不能豁免 analyst-blinding 未实现这一缺陷。
仅通过软件测试或跑完链，都不能自动授予 A-ready 或 A 状态。

完整规则见 [A-readiness 协议](./docs/DESI_W0WA_A_READINESS_PROTOCOL.md)；
历史代理结果及其限制见
[初步全 CMB 记录](./backend/scripts/cobaya/README_full_cmb_reproduction.md)。

## 项目入口

| 路径 | 作用 |
|---|---|
| `backend/app/main.py` | FastAPI 后端入口 |
| `frontend/src/main.tsx` | React 前端入口 |
| `frontend/src/App.tsx` | 页面路由与应用外壳 |
| `frontend/src/pages/Chat/ChatPage.tsx` | 主要研究界面 |
| `backend/app/services/ai_tools/` | 已注册工具与分发 |
| `render.yaml` | 参考生产拓扑 |

技术栈包括 React 19、strict TypeScript、Vite、FastAPI、Pydantic v2、
PostgreSQL、Redis/Celery、astropy、emcee、dynesty、Cobaya、CAMB 和 ArviZ。

## 开发验证

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

科学数据、似然或防编造门槛的改动还需要对应 benchmark、registry audit 和盲测。
完整规则见[贡献指南](./CONTRIBUTING.md)和[开发手册](./CLAUDE.md)。

## 文档导航

- [产品快速指南](./docs/QUICKSTART.md)
- [架构](./ARCHITECTURE.md)
- [数据源映射](./docs/SOURCE_MAPPING.md)
- [科学严谨性修复账本](./docs/SCIENTIFIC_RIGOR_REMEDIATION.md)
- [API Reference](./docs/API_REFERENCE.md)
- [运维与恢复](./docs/OPERATIONS_RUNBOOK.md)
- [安全](./SECURITY.md) · [隐私](./PRIVACY.md) ·
  [参考文献](./docs/REFERENCES.md)

## 数据与许可

研究输出必须保留具体数据版本、原始引用和所需致谢。Standard Astro 不代表其接入
的档案、机构或模型提供商。

项目许可尚未最终确定，当前仓库没有发布 `LICENSE` 文件。不要假定拥有再分发或
合并第三方贡献的权利。
