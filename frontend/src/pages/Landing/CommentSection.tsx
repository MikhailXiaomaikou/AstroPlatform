/**
 * Landing 页下方公开评论区.
 *
 * 任何访客填昵称 + 内容即可提交, 无需登录.  管理员在 localStorage 设
 * 键 `astro_admin_secret` 后能看到每条评论右侧的"删除"按钮 (软删除,
 * 后端置 is_visible=False).
 */
import { useCallback, useEffect, useState } from "react";
import type { CommentPublicView } from "../../api/client";
import {
  fetchComments,
  postComment,
  deleteComment,
} from "../../api/client";

// 跟后端 _NAME_MAX / _CONTENT_MAX 对齐
const NAME_MAX = 40;
const CONTENT_MAX = 500;
const PAGE_SIZE = 20;

function formatRelative(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const diffSec = Math.max(0, Math.round(diffMs / 1000));
  if (diffSec < 60) return "刚刚";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.round(diffHour / 24);
  if (diffDay < 30) return `${diffDay} 天前`;
  return new Date(iso).toLocaleDateString();
}

export default function CommentSection() {
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

  // 监听同页其他组件修改 admin secret (虽然不常见,但低成本保险)
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
        setSubmitError(`昵称长度需为 1 – ${NAME_MAX} 字`);
        return;
      }
      if (trimmedContent.length < 2 || trimmedContent.length > CONTENT_MAX) {
        setSubmitError(`内容长度需为 2 – ${CONTENT_MAX} 字`);
        return;
      }
      setSubmitting(true);
      try {
        const created = await postComment(trimmedName, trimmedContent);
        // 乐观插入顶部
        setComments((prev) => [created, ...prev]);
        setTotal((prev) => prev + 1);
        setContent("");
        setJustPostedId(created.id);
        // 淡出提示
        setTimeout(() => setJustPostedId(null), 2500);
      } catch (err: unknown) {
        // axios 错误: 读 response.data.detail / status
        const e = err as { response?: { status?: number; data?: { detail?: string } }; message?: string };
        if (e?.response?.status === 429) {
          setSubmitError("发布太快啦,请稍等一会儿再试 (后端每 IP 每分钟最多 3 条).");
        } else if (e?.response?.data?.detail) {
          setSubmitError(String(e.response.data.detail));
        } else {
          setSubmitError(e?.message || "提交失败");
        }
      } finally {
        setSubmitting(false);
      }
    },
    [name, content],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      if (!adminSecret) return;
      if (!window.confirm("删除这条评论? (软删除, 可在后台恢复)")) return;
      try {
        await deleteComment(id, adminSecret);
        setComments((prev) => prev.filter((c) => c.id !== id));
        setTotal((prev) => Math.max(0, prev - 1));
      } catch (err: unknown) {
        const e = err as { response?: { status?: number; data?: { detail?: string } } };
        if (e?.response?.status === 403) {
          window.alert("管理员密钥无效或已失效, 请检查 localStorage.astro_admin_secret");
          setAdminSecret(null);
        } else {
          window.alert("删除失败: " + (e?.response?.data?.detail || "unknown error"));
        }
      }
    },
    [adminSecret],
  );

  return (
    <section className="comment-section">
      <h2 className="section-head alt">访客留言 · Guest Comments</h2>
      <p className="comment-section-intro">
        欢迎任何访客留下一句话 — 使用体验、建议、bug 反馈都行。
        无需登录,填昵称即可。内容经管理员审核后可能被移除。
      </p>

      {/* ── 提交表单 ── */}
      <form className="comment-form" onSubmit={handleSubmit}>
        <div className="comment-form-row">
          <label>
            <span className="comment-form-label">昵称</span>
            <input
              type="text"
              className="comment-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={NAME_MAX}
              placeholder="例如 Alice"
              required
            />
          </label>
          <span className="comment-char-count">
            {name.length}/{NAME_MAX}
          </span>
        </div>
        <div className="comment-form-row">
          <label className="comment-form-row-content">
            <span className="comment-form-label">内容</span>
            <textarea
              className="comment-textarea"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              maxLength={CONTENT_MAX}
              rows={3}
              placeholder="写点什么..."
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
            {submitting ? "提交中..." : "发布"}
          </button>
        </div>
      </form>

      {/* ── 评论列表 ── */}
      <div className="comment-list-header">
        <span className="comment-list-count">
          共 {total} 条
        </span>
        {adminSecret && (
          <span className="comment-admin-badge" title="检测到 localStorage 里有 astro_admin_secret, 列表可删除">
            admin 模式
          </span>
        )}
      </div>

      {loading ? (
        <div className="comment-loading">加载中...</div>
      ) : error ? (
        <div className="comment-error">加载失败: {error}</div>
      ) : comments.length === 0 ? (
        <div className="comment-empty">还没有人留言 — 做第一个吧。</div>
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
                    title="管理员软删除"
                  >
                    删除
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
            {loadingMore ? "加载中..." : "加载更多"}
          </button>
        </div>
      )}
    </section>
  );
}
