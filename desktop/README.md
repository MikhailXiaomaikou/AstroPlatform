# Astro Platform — Admin Dashboard

## 推荐用法 (自动更新版)

**浏览器打开 → bookmark → 以后永远是最新:**

```
https://astro-backend-h4x1.onrender.com/admin
```

第一次用: Render 后台 env var 加 `ADMIN_SECRET=<rand-hex-32>`, 页面顶部输入同样的值 → "保存并加载" → 数据自动加载.  以后每次打开页面 backend 会返回最新 HTML (Cache-Control: no-store), 不用 cp 文件不用重新下载.

原 HTML 源在 `backend/app/static/astro_admin.html`, 每次 push 到 main + Render 部署完成就自动同步到这个 URL.

## 离线 fallback (旧的 file:// 双击用法)

若你不想每次联网 (或 backend 挂了想看 cache), 仍可:

```bash
curl -o ~/Desktop/astro_admin.html https://astro-backend-h4x1.onrender.com/admin
open ~/Desktop/astro_admin.html
```

双击打开时浏览器 origin 是 `null`。开发环境默认允许；生产环境为避免
opaque-origin 滥用不再自动允许。只有明确接受该风险且必须使用旧流程时，
才在生产 `CORS_ORIGINS` 末尾显式加入 `,null`。推荐始终使用上面的
`/admin` 同源页面。

## 看到什么

- ① 概览: 4 张 KPI 卡
- ② 使用次数 / 方向: 工具调用排行 + 页面访问排行
- ③ 趋势: 时间桶事件量堆叠 + Token/成本趋势 + 评论趋势
- ④ 评论管理: 列表 + 软删除
- ⑤ 科研趋势: 热门对象 / 数据源 / 上升趋势 + 公开开关
- ⑥ Sandbox 诊断: Popen health / 生产链路 / repro-imports (定位 child crash)

## 隐私

- Admin secret 只存浏览器 localStorage, 不上传
- 数据从 production API 拉, 是真用户行为
