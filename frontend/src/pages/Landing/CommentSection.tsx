/**
 * Landing 页下方公开评论区.
 *
 * 任何访客填昵称 + 内容即可提交, 无需登录.  管理员在 localStorage 设
 * 键 `astro_admin_secret` 后能看到每条评论右侧的"删除"按钮 (软删除,
 * 后端置 is_visible=False).
 *
 * i18n: 所有用户可见字符串走 t() 函数, 支持 en/zh/fr/es (key 前缀
 * comments.*).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CommentPublicView } from "../../api/client";
import {
  fetchComments,
  postComment,
  deleteComment,
} from "../../api/client";
import { useI18n } from "../../i18n";

// 跟后端 _NAME_MAX / _CONTENT_MAX 对齐
const NAME_MAX = 40;
const CONTENT_MAX = 500;
const PAGE_SIZE = 20;

function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    key in values ? String(values[key]) : `{${key}}`,
  );
}

export default function CommentSection() {
  const { t, lang } = useI18n();  // lang 在 deps 里让切语言时触发重排 format

  // 用 lang 作 useMemo dep: 切换语言时 formatRelative 输出跟着换.
  const formatRelative = useMemo(
    () => (iso: string): string => {
      if (!iso) return "";
      const then = new Date(iso).getTime();
      if (Number.isNaN(then)) return iso;
      const diffMs = Date.now() - then;
      const diffSec = Math.max(0, Math.round(diffMs / 1000));
      if (diffSec < 60) return t("comments.time.just_now");
      const diffMin = Math.round(diffSec / 60);
      if (diffMin < 60) return fill(t("comments.time.minutes_ago"), { n: diffMin });
      const diffHour = Math.round(diffMin / 60);
      if (diffHour < 24) return fill(t("comments.time.hours_ago"), { n: diffHour });
      const diffDay = Math.round(diffHour / 24);
      if (diffDay < 30) return fill(t("comments.time.days_ago"), { n: diffDay });
      return new Date(iso).toLocaleDateString();
    },
    [t, lang],  // eslint-disable-line react-hooks/exhaustive-deps
  );

  const [comments, setComments] = useState<CommentPublicView[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 提交表单 state
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [justPostedId, setJustPostedId] = useState<string | null>(null);

  // admin 判断: localStorage 里存了 admin secret 就算 admin
  const [adminSecret, setAdminSecret] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const v = window.localStorage.getItem("astro_admin_secret");
    return v && v.trim() ? v.trim() : null;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onStorage = () => {
      const v = window.localStorage.getItem("astro_admin_secret");
      setAdminSecret(v && v.trim() ? v.trim() : null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchComments(PAGE_SIZE, 0);
      setComments(res.comments);
      setTotal(res.total);
      setHasMore(res.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  const handleLoadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const res = await fetchComments(PAGE_SIZE, comments.length);
      setComments((prev) => [...prev, ...res.comments]);
      setTotal(res.total);
      setHasMore(res.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [comments.length]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setSubmitError(null);
      const trimmedName = name.trim();
      const trimmedContent = content.trim();
      if (trimmedName.length < 1 || trimmedName.length > NAME_MAX) {
        setSubmitError(t("comments.err.name_length"));
        return;
      }
      if (trimmedContent.length < 2 || trimmedContent.length > CONTENT_MAX) {
        setSubmitError(t("comments.err.content_length"));
        return;
      }
      setSubmitting(true);
      try {
        const created = await postComment(trimmedName, trimmedContent);
        setComments((prev) => [created, ...prev]);
        setTotal((prev) => prev + 1);
        setContent("");
        setJustPostedId(created.id);
        setTimeout(() => setJustPostedId(null), 2500);
      } catch (err: unknown) {
        const e = err as { response?: { status?: number; data?: { detail?: string } }; message?: string };
        if (e?.response?.status === 429) {
          setSubmitError(t("comments.err.rate_limit"));
        } else if (e?.response?.data?.detail) {
          setSubmitError(String(e.response.data.detail));
        } else {
          setSubmitError(e?.message || t("comments.err.submit_failed"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [name, content, t],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      if (!adminSecret) return;
      if (!window.confirm(t("comments.delete_confirm"))) return;
      try {
        await deleteComment(id, adminSecret);
        setComments((prev) => prev.filter((c) => c.id !== id));
        setTotal((prev) => Math.max(0, prev - 1));
      } catch (err: unknown) {
        const e = err as { response?: { status?: number; data?: { detail?: string } } };
        if (e?.response?.status === 403) {
          window.alert(t("comments.admin_invalid"));
          setAdminSecret(null);
        } else {
          const msg = e?.response?.data?.detail || "unknown error";
          window.alert(fill(t("comments.delete_failed"), { err: msg }));
        }
      }
    },
    [adminSecret, t],
  );

  return (
    <section className="comment-section">
      <h2 className="section-head alt">{t("comments.title")}</h2>
      <p className="comment-section-intro">{t("comments.intro")}</p>

      {/* ── 提交表单 ── */}
      <form className="comment-form" onSubmit={handleSubmit}>
        <div className="comment-form-row">
          <label>
            <span className="comment-form-label">{t("comments.form.name_label")}</span>
            <input
              type="text"
              className="comment-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={NAME_MAX}
              placeholder={t("comments.form.name_placeholder")}
              required
            />
          </label>
          <span className="comment-char-count">
            {name.length}/{NAME_MAX}
          </span>
        </div>
        <div className="comment-form-row">
          <label className="comment-form-row-content">
            <span className="comment-form-label">{t("comments.form.content_label")}</span>
            <textarea
              className="comment-textarea"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              maxLength={CONTENT_MAX}
              rows={3}
              placeholder={t("comments.form.content_placeholder")}
              required
            />
          </label>
          <span className="comment-char-count">
            {content.length}/{CONTENT_MAX}
          </span>
        </div>
        {submitError && (
          <div className="comment-form-error">{submitError}</div>
        )}
        <div className="comment-form-actions">
          <button
            type="submit"
            className="comment-submit-btn"
            disabled={submitting}
          >
            {submitting ? t("comments.form.submitting") : t("comments.form.submit")}
          </button>
        </div>
      </form>

      {/* ── 评论列表 ── */}
      <div className="comment-list-header">
        <span className="comment-list-count">
          {fill(t("comments.count"), { n: total })}
        </span>
        {adminSecret && (
          <span
            className="comment-admin-badge"
            title={t("comments.admin_badge_title")}
          >
            {t("comments.admin_badge")}
          </span>
        )}
      </div>

      {loading ? (
        <div className="comment-loading">{t("comments.loading")}</div>
      ) : error ? (
        <div className="comment-error">
          {fill(t("comments.load_failed"), { err: error })}
        </div>
      ) : comments.length === 0 ? (
        <div className="comment-empty">{t("comments.empty")}</div>
      ) : (
        <ul className="comment-list">
          {comments.map((c) => (
            <li
              key={c.id}
              className={`comment-item${justPostedId === c.id ? " comment-item-fresh" : ""}`}
            >
              <div className="comment-item-head">
                <span className="comment-author">{c.author_name}</span>
                <span className="comment-time" title={c.created_at}>
                  {formatRelative(c.created_at)}
                </span>
                {adminSecret && (
                  <button
                    type="button"
                    className="comment-delete-btn"
                    onClick={() => handleDelete(c.id)}
                    title={t("comments.delete_title")}
                  >
                    {t("comments.delete")}
                  </button>
                )}
              </div>
              <div className="comment-content">{c.content}</div>
            </li>
          ))}
        </ul>
      )}

      {hasMore && !loading && (
        <div className="comment-load-more-wrap">
          <button
            type="button"
            className="comment-load-more-btn"
            onClick={handleLoadMore}
            disabled={loadingMore}
          >
            {loadingMore ? t("comments.loading_more") : t("comments.load_more")}
          </button>
        </div>
      )}
    </section>
  );
}
