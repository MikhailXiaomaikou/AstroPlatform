#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "../../../frontend/node_modules/playwright/index.mjs";

const apiBase = process.env.DEMO_API_BASE;
const uiBase = process.env.DEMO_UI_BASE;
const repo = process.env.DEMO_REPO;
const stateDir = process.env.DEMO_STATE_DIR;
const outputDir = process.env.DEMO_OUTPUT_DIR;
const backendPython = process.env.DEMO_BACKEND_PYTHON;
const commit = process.env.GIT_COMMIT;

if (!apiBase || !uiBase || !repo || !stateDir || !outputDir || !backendPython || !commit) {
  throw new Error("The demo driver is missing its runner environment");
}

const runId = crypto.randomBytes(4).toString("hex");
const ownerUsername = `astro-owner-${runId}`;
const password = `Demo-${crypto.randomBytes(12).toString("base64url")}!`;
const workerHome = path.join(stateDir, "worker-home");
const rawVideoDir = path.join(stateDir, "video");
await fs.mkdir(rawVideoDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function step(label) {
  process.stdout.write(`[union3-demo] ${label}\n`);
}

async function api(pathname, { token, method = "GET", body } = {}) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(apiBase + pathname, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : Buffer.from(await response.arrayBuffer());
  if (!response.ok) {
    throw new Error(`${method} ${pathname} failed (${response.status}): ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function register(username) {
  return api("/api/auth/register", {
    method: "POST",
    body: { username, password },
  });
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${command} exited ${code}: ${stderr || stdout}`));
    });
  });
}

async function poll(fetchValue, predicate, label, timeoutMilliseconds = 120_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let lastValue;
  while (Date.now() < deadline) {
    lastValue = await fetchValue();
    if (predicate(lastValue)) return lastValue;
    await delay(750);
  }
  throw new Error(`Timed out waiting for ${label}: ${JSON.stringify(lastValue)}`);
}

async function addCaption(page, zh, en) {
  await page.evaluate(({ zh, en }) => {
    document.querySelector("#standard-astro-demo-caption")?.remove();
    const caption = document.createElement("div");
    caption.id = "standard-astro-demo-caption";
    caption.setAttribute("data-demo-overlay", "true");
    caption.style.cssText = [
      "position:fixed", "left:24px", "right:24px", "bottom:20px", "z-index:2147483647",
      "background:rgba(8,18,28,.94)", "color:#fff", "border:1px solid rgba(255,255,255,.2)",
      "border-radius:10px", "padding:12px 16px", "box-shadow:0 8px 30px rgba(0,0,0,.3)",
      "font:600 16px/1.4 system-ui,-apple-system,sans-serif", "pointer-events:none",
    ].join(";");
    caption.innerHTML = `<strong style="color:#8fd7c1">演示步骤 / Demo step</strong><br>${zh}<br><span style="font-size:13px;color:#cad4dd">${en}</span>`;
    document.body.appendChild(caption);
  }, { zh, en });
  await delay(1800);
}

async function showTitleCard(page) {
  await page.setContent(`<!doctype html>
    <html lang="zh-CN"><head><meta charset="utf-8"><title>Standard Astro guarded demo</title></head>
    <body style="margin:0"><section style="box-sizing:border-box;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#07131d 0%,#0b2b35 55%,#123c3a 100%);color:#fff;padding:80px;text-align:center;font-family:system-ui,-apple-system,sans-serif">
      <div style="max-width:980px">
        <div style="color:#8fd7c1;font-size:20px;font-weight:700;letter-spacing:.08em">STANDARD ASTRO</div>
        <h1 style="font-size:46px;line-height:1.15;margin:20px 0">本地受控科研演示<br><span style="font-size:32px;color:#c7e7df">Local guarded research demo</span></h1>
        <p style="font-size:21px;line-height:1.6;color:#e5f0ed;margin:0">
          这不是 Render 生产部署。本机未运行 GitHub Actions 构建并签名的 OCI Worker 镜像，<br>
          因此预期结论是 <strong style="color:#ffd27d">WITHHELD</strong>，不是 SUPPORTED。
        </p>
        <p style="font-size:16px;line-height:1.5;color:#abc8c2;margin-top:20px">
          This is not a Render production run. Without the signed OCI Worker image,<br>
          the correct expected verdict is WITHHELD, not SUPPORTED.
        </p>
      </div>
    </section></body></html>`);
  await delay(4200);
}

async function showResultPanel(page, title, output) {
  await page.evaluate(({ title, output }) => {
    document.querySelector("#standard-astro-demo-cli")?.remove();
    const panel = document.createElement("pre");
    panel.id = "standard-astro-demo-cli";
    panel.setAttribute("data-demo-overlay", "true");
    panel.textContent = `${title}\n${output}`;
    panel.style.cssText = [
      "position:fixed", "top:90px", "left:50%", "transform:translateX(-50%)", "z-index:2147483646",
      "width:min(780px,calc(100vw - 80px))", "max-height:440px", "overflow:auto",
      "background:#071018", "color:#a9ebd4", "border:1px solid #3d826d", "border-radius:10px",
      "padding:18px", "box-shadow:0 16px 50px rgba(0,0,0,.45)", "font:14px/1.5 ui-monospace,monospace",
    ].join(";");
    document.body.appendChild(panel);
  }, { title, output });
  await delay(3000);
}

const ownerAuth = await register(ownerUsername);
step("registered the private Workspace owner");
const ownerToken = ownerAuth.access_token;

const chromePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const launchOptions = { headless: true };
try {
  await fs.access(chromePath);
  launchOptions.executablePath = chromePath;
} catch {
  // Use Playwright's managed Chromium on non-macOS environments.
}

const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: rawVideoDir, size: { width: 1440, height: 900 } },
  acceptDownloads: true,
});
await context.addInitScript(({ token }) => {
  localStorage.setItem("astro_token", token);
  localStorage.setItem("astro_lang", "zh");
  localStorage.setItem("astro_onboarded", "1");
}, { token: ownerToken });
const page = await context.newPage();
const video = page.video();

let workspaceId;
let source;
let candidate;
let audit;
let primaryJob;
let primaryAnalysisPath;

try {
  await showTitleCard(page);
  step("opening the real Research Workspace UI");
  await page.goto(`${uiBase}/research`, { waitUntil: "networkidle" });
  await page.locator(".research-create-panel input").fill("Union3 论文区间复现");
  await page.locator(".research-create-panel textarea").fill(
    "真实论文读取和本地 profile-χ² 主计算；缺少签名 OCI 运行绑定时必须保持 WITHHELD。",
  );
  await addCaption(
    page,
    "创建私有 Workspace；这一步只建立科研账本，不产生科学结论。",
    "Create a private Workspace. No scientific verdict exists yet.",
  );
  await page.locator(".research-create-panel button[type=submit]").click();
  await page.waitForURL(/\/research\/workspaces\/[0-9a-f-]+$/, { timeout: 30_000 });
  workspaceId = page.url().split("/").pop();
  step(`created workspace ${workspaceId}`);

  await page.locator("#research-tab-sources").click();
  await addCaption(
    page,
    "Reader 将获取（或读取已验哈希缓存）固定版本 arXiv:2311.12098v4，再定位第 58 页 Table 9。",
    "The Reader acquires, or reads a hash-verified cache of, arXiv:2311.12098v4 before locating Table 9.",
  );
  const addSourceButton = page.locator("#research-panel-sources .btn-primary");
  await addSourceButton.waitFor({ state: "visible", timeout: 30_000 });
  step(`Reader button visible; enabled=${await addSourceButton.isEnabled()}`);
  await addSourceButton.click({ timeout: 30_000 });
  step("requested registered Union3 source acquisition");
  await page.locator(".research-source-card").waitFor({ timeout: 180_000 });
  const sourceList = await poll(
    () => api(`/api/research/workspaces/${workspaceId}/sources`, { token: ownerToken }),
    (value) => {
      const document = value.items[0];
      return document && ["COMPLETED", "FAILED_FINAL"].includes(document.lifecycle_status);
    },
    "the asynchronous registered Reader",
    240_000,
  );
  source = sourceList.items[0];
  if (
    source.lifecycle_status !== "COMPLETED"
    || source.coverage_status !== "UNION3_TABLE9_INTERVAL_READY"
    || !source.extraction
  ) {
    throw new Error(`Reader did not produce a covered source: ${JSON.stringify(source)}`);
  }
  step(`Reader completed source ${source.source_document_id}`);
  candidate = source.extraction.extraction_payload.candidates[0];

  await page.locator("#research-tab-claims").click();
  await page.locator(".research-claim-card").waitFor();
  await addCaption(
    page,
    "系统从论文原文提取 frequentist profile-χ² 区间；它不是 posterior。",
    "The paper interval is frequentist profile-chi-square, not a posterior.",
  );
  await page.locator(".research-claim-card .btn-primary").click();
  await page.locator("#research-panel-runs").waitFor();
  const auditsAfterCreate = await poll(
    () => api(`/api/research/workspaces/${workspaceId}/claim-audits`, { token: ownerToken }),
    (value) => value.items.length === 1,
    "the queued Claim Audit",
  );
  audit = auditsAfterCreate.items[0];
  if (audit.lifecycle_status !== "QUEUED" || audit.scientific_verdict !== null) {
    throw new Error(`A new Audit must be queued without a verdict: ${JSON.stringify(audit)}`);
  }
  step(`created queued Audit ${audit.audit_id}`);
  await addCaption(
    page,
    "Audit 正在等待用户电脑。QUEUED 不是科学缺口，也不是 SUPPORTED。",
    "The Audit waits for the user's computer. QUEUED is not a scientific verdict.",
  );

  const enrollment = await api("/api/compute/v1/enrollments", {
    token: ownerToken,
    method: "POST",
  });
  const workerEnvironment = {
    ...process.env,
    ENV: "dev",
    APP_ROLE: "science_worker",
    GIT_COMMIT: commit,
    TOOL_VERSION: commit,
    ASTRO_WORKER_HOME: workerHome,
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  };
  for (const variable of [
    "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
  ]) {
    delete workerEnvironment[variable];
  }
  await run(
    backendPython,
    [
      "-m", "app.worker_agent", "worker", "enroll", enrollment.enrollment_code,
      "--control-plane", apiBase, "--name", "Union3 demo local worker",
      "--home", workerHome,
    ],
    { cwd: path.join(repo, "backend"), env: workerEnvironment },
  );
  step("enrolled the signed local worker");
  await addCaption(
    page,
    "本地 Worker 用一次性码登记；它只收到签名任务信封，不接触数据库、Redis 或用户密钥。",
    "The local Worker enrolls once and receives only a signed task envelope.",
  );
  await run(
    backendPython,
    ["-m", "app.worker_agent", "worker", "start", "--once", "--home", workerHome],
    { cwd: path.join(repo, "backend"), env: workerEnvironment },
  );
  step("local worker completed the real primary calculation");

  const primaryJobId = audit.child_job_ids[0];
  primaryJob = await poll(
    () => api(`/api/jobs/${primaryJobId}`, { token: ownerToken }),
    (value) => value.status === "COMPLETED" && Boolean(value.result),
    "the durable primary-analysis record",
  );
  const primaryResult = primaryJob.result.worker_result || primaryJob.result;
  const statistics = primaryResult.statistics;
  if (
    primaryResult.primary_ready !== true
    || primaryResult.scientific_status !== "WITHHELD"
    || primaryResult.publication_ready !== false
    || statistics.omega_m_best !== "0.35592440"
    || statistics.omega_m_lower !== "0.32948065"
    || statistics.omega_m_upper !== "0.38352307"
    || statistics.chi_square_min !== "23.95789014"
    || statistics.degrees_of_freedom !== "20"
  ) {
    throw new Error(`Primary analysis crossed or missed its contract: ${JSON.stringify(primaryJob.result)}`);
  }
  primaryAnalysisPath = path.join(outputDir, "union3-primary-analysis.json");
  await fs.writeFile(primaryAnalysisPath, JSON.stringify(primaryJob.result, null, 2) + "\n");
  await showResultPanel(
    page,
    "真实本机主计算 / Real local primary calculation",
    [
      `omega_m best  ${statistics.omega_m_best}`,
      `68.3% interval [${statistics.omega_m_lower}, ${statistics.omega_m_upper}]`,
      `chi-square min ${statistics.chi_square_min}; DoF ${statistics.degrees_of_freedom}`,
      "primary_ready true; publication_ready false",
    ].join("\n"),
  );

  const machineChecked = await poll(
    async () => {
      const value = await api(`/api/research/workspaces/${workspaceId}/claim-audits`, { token: ownerToken });
      return value.items[0];
    },
    (value) => value.progress_stage === "machine_verification_withheld",
    "the fail-closed independent-verification result",
  );
  if (
    machineChecked.scientific_verdict !== "WITHHELD"
    || machineChecked.error_class !== "worker_task_binding_mismatch"
    || machineChecked.machine_support_eligible !== false
    || machineChecked.review_status !== "NOT_SUBMITTED"
    || machineChecked.reproduction_ready !== false
    || machineChecked.publication_ready !== false
    || machineChecked.evidence_pack !== null
  ) {
    throw new Error(`The missing-image gate did not fail closed: ${JSON.stringify(machineChecked)}`);
  }
  audit = machineChecked;
  step("independent verifier correctly withheld the unbound host execution");
  await page.locator(".research-header-actions .btn-secondary").click();
  await page.locator("#research-tab-runs").click();
  await addCaption(
    page,
    "独立验证拒绝了没有真实 OCI digest 的主计算。WITHHELD 说明安全门生效，不说明数值造假。",
    "Independent verification rejects a run without a real OCI digest. WITHHELD proves the gate worked; it does not label the numeric calculation fake.",
  );
  await showResultPanel(
    page,
    "科学门状态 / Scientific gate state",
    [
      "scientific_verdict  WITHHELD",
      "error_class         worker_task_binding_mismatch",
      "Evidence Pack       not created",
      "next                build + Cosign OCI image in GitHub Actions",
    ].join("\n"),
  );
  await page.screenshot({
    path: path.join(outputDir, "union3-local-worker-demo-poster.png"),
    fullPage: false,
  });

  const sha256 = async (filePath) => crypto.createHash("sha256").update(await fs.readFile(filePath)).digest("hex");
  const receipt = {
    schema_version: "standard_astro_union3_guardrail_demo_receipt_v1",
    generated_at: new Date().toISOString(),
    git_commit: commit,
    git_dirty: process.env.DEMO_GIT_DIRTY === "true",
    workspace_id: workspaceId,
    source_document_id: source.source_document_id,
    source_identifier: source.canonical_identifier,
    source_pdf_sha256: source.raw_artifact_hashes.pdf,
    source_document_hash: source.source_document_hash,
    source_extraction_hash: source.extraction.extraction_payload_hash,
    candidate_id: candidate.candidate_id,
    claim_hash: candidate.claim_hash,
    audit_id: audit.audit_id,
    primary_job_id: primaryJob.job_id,
    primary_analysis_sha256: await sha256(primaryAnalysisPath),
    primary_statistics: statistics,
    primary_ready: primaryResult.primary_ready,
    scientific_verdict: audit.scientific_verdict,
    review_status: audit.review_status,
    reproduction_ready: audit.reproduction_ready,
    publication_ready: audit.publication_ready,
    machine_support_eligible: audit.machine_support_eligible,
    independent_verification: {
      attempted: true,
      passed: false,
      fail_closed: true,
      error_class: audit.error_class,
      progress_stage: audit.progress_stage,
    },
    evidence_pack_created: false,
    evidence_pack_id: null,
    offline_verification: null,
    scientific_boundary: "reproduction_of_published_constraint",
    expected_verdict_for_this_demo: "WITHHELD",
    oci_image_digest_configured: false,
    next_required_gate: "github_actions_build_and_cosign_worker_image",
    render_deployment_claimed: false,
    production_worker_container_claimed: false,
    source_cache_preloaded: process.env.DEMO_SOURCE_CACHE_PRELOADED === "true",
  };
  await fs.writeFile(
    path.join(outputDir, "union3-demo-receipt.json"),
    JSON.stringify(receipt, null, 2) + "\n",
  );
  step("wrote the fail-closed demo receipt");
} finally {
  await context.close();
  await browser.close();
}

const rawVideo = await video.path();
const outputVideo = path.join(outputDir, "standard-astro-union3-local-worker-demo.mp4");
await run(
  "ffmpeg",
  [
    "-y", "-i", rawVideo, "-an", "-c:v", "libx264", "-preset", "medium",
    "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", outputVideo,
  ],
  { cwd: outputDir, env: process.env },
);
