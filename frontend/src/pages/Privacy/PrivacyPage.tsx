import { useEffect, useState } from "react";

import { getRuntimeConfig, type RuntimeConfig } from "../../api/client";

type Notice = NonNullable<RuntimeConfig["privacy_notice"]>;

export default function PrivacyPage() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((config) => {
        if (!cancelled) {
          const candidate = config.privacy_notice;
          const complete = Boolean(
            candidate
            && candidate.operator_name.trim()
            && candidate.contact.trim()
            && candidate.jurisdiction.trim()
          );
          setNotice(complete ? candidate || null : null);
          setFailed(!complete);
        }
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="settings-page" style={{ maxWidth: 900, margin: "0 auto" }}>
      <h1>Privacy Notice / 隐私说明</h1>
      {notice ? (
        <section className="settings-section" aria-labelledby="operator-heading">
          <h2 id="operator-heading">Hosted operator / 线上运营方</h2>
          <dl style={{ display: "grid", gridTemplateColumns: "minmax(140px, 220px) 1fr", gap: "8px 18px" }}>
            <dt>Operator / 运营主体</dt><dd>{notice.operator_name}</dd>
            <dt>Privacy contact / 隐私联系</dt><dd>{notice.contact}</dd>
            <dt>Jurisdiction / 适用法域</dt><dd>{notice.jurisdiction}</dd>
          </dl>
        </section>
      ) : failed ? (
        <p className="error-banner" role="alert">
          This instance did not publish its required operator details. Do not submit personal or unpublished research data.
          本实例未能公开必需的运营方信息，请勿提交个人数据或未公开研究资料。
        </p>
      ) : <p>Loading operator details…</p>}

      <section className="settings-section">
        <h2>What is processed / 处理哪些数据</h2>
        <p>
          The service processes account details, research chats and files, tool provenance, Claim Audits,
          Evidence Packs, and encrypted user-provided model keys as needed to provide the requested features.
          服务会为完成用户请求而处理账号资料、研究对话与文件、工具来源记录、主张审计、证据包，以及加密保存的用户模型密钥。
        </p>
        <p>
          Research content may be sent to the model or archive provider selected for a task. Do not place secrets
          or unauthorized data in public comments or shared results. 研究内容可能发送给任务所选的模型或档案服务；请勿在公开或共享内容中放入密钥和未授权数据。
        </p>
      </section>

      <section className="settings-section">
        <h2>Analytics and retention / 分析与保留</h2>
        <p>
          Optional product analytics are off by default and require explicit consent. They use a strict coarse-event
          allowlist and exclude claims, prompts, paper identifiers, tool parameters, raw errors, and scientific values.
          可选产品分析默认关闭并需要明确同意；它不收集主张、提示词、论文标识、工具参数、错误原文或科研数值。
        </p>
        <p>
          Separate coarse inference operations metrics (provider/model class, token counts, latency, cost, success,
          and an allowlisted error class) are needed for reliability and billing and are not controlled by the optional
          analytics toggle. Both stores have a 30-day production retention target and an hourly purge with retries;
          scheduler or database outages may delay physical deletion and must be monitored by the operator.
          与可选产品分析分开，系统会为可靠性和计费记录粗粒度推理运行指标（Provider/模型类别、token 数、耗时、成本、成功状态和白名单错误类别）。两类记录的生产保留目标均为 30 天，每小时重试清理；调度器或数据库故障可能延迟物理删除，运营方必须监控。
        </p>
        <p>
          Research records and private Evidence Packs remain until the user deletes them or the account, unless the
          operator publishes a shorter period. 研究记录和私有证据包默认保留到用户删除记录或账号，除非运营方另行公布更短期限。
        </p>
      </section>

      <section className="settings-section">
        <h2>Deletion and rights / 删除与用户权利</h2>
        <p>
          Account deletion immediately disables login and schedules removal of owned database records, caches, and
          all versions of owned objects. A restore-safe tombstone prevents an older backup from reviving the account;
          backup expiry is targeted at 30 days. 删除账号会立即停用登录，并安排清理归属数据库记录、缓存和对象的所有版本；恢复安全墓碑会阻止旧备份复活账号，备份到期目标为 30 天。
        </p>
        <p>Use the privacy contact above for access, correction, deletion, or other rights requests. 如需访问、更正、删除或行使其他权利，请联系上方隐私联系方式。</p>
      </section>

      <section className="settings-section">
        <h2>Deployment-specific disclosures / 部署专属披露</h2>
        <p>
          Before inviting users, the operator must publish the actually enabled model, OAuth, archive, hosting,
          object-storage and logging subprocessors; infrastructure-log/IP retention; and the tested database/object
          backup and version-expiry schedule. If this instance has not supplied that list through the operator contact,
          its privacy release gate is not complete and users should not submit personal or unpublished research data.
          邀请用户前，运营方必须公开实际启用的模型、OAuth、档案、托管、对象存储和日志服务，基础设施日志/IP 保留期，以及经验证的数据库与对象备份/版本到期计划。若本实例尚未通过上方联系方式提供这些信息，则隐私发布门尚未完成，用户不应提交个人或未公开研究数据。
        </p>
        <p>
          Ordinary sign-out removes authentication and account-scoped analytics state and unloads protected pages,
          but downloaded files, browser caches, and some non-sensitive UI preferences may remain. External providers
          apply their own retention/deletion processes. 普通退出会移除认证和账号分析状态并卸载受保护页面，但下载文件、浏览器缓存和部分非敏感界面偏好可能保留；外部服务有各自的保留与删除流程。
        </p>
      </section>
    </div>
  );
}
