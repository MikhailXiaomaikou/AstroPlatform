# Astro Platform — Desktop Admin

## astro_admin.html

桌面 admin 面板单文件. 把它拖到桌面或任何位置, **双击**用浏览器打开就能用.

### 用法

1. 在 Render dashboard 给 backend 设环境变量 `ADMIN_SECRET=<任意强随机字符串>`
   (例如 `openssl rand -hex 32`)
2. 双击 `astro_admin.html`
3. 顶部填:
   - Backend URL: 默认 `https://astro-backend-h4x1.onrender.com`, 一般不改
   - Admin Secret: 第 1 步设的那个
4. 点"保存并加载" — 配置存浏览器 localStorage, 下次打开自动 load

### 看到什么

- ① 概览: 4 张 KPI 卡 (事件总数 / 活跃用户 / AI 调用+成本 / 评论总览)
- ② 使用次数 / 方向: 工具调用排行 + 页面访问排行 (横向柱状)
- ③ 趋势: 时间桶事件量堆叠 + Token/成本趋势 + 评论趋势
- ④ 评论管理: 列表 + 删除按钮 (软删, is_visible=False)

切顶部 "时间范围" 按钮 (24h / 7d / 30d / 90d) 全部图重算.

### 隐私

- Admin secret 只存浏览器 localStorage, 不上传
- 双击打开时浏览器 origin 是 `null`, backend CORS 已经允许
- 数据全部从 Render production API 拉, 你能看到的都是真用户数据
