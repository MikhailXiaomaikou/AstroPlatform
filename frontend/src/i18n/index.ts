/**
 * Lightweight i18n — no external dependency.
 * Usage: const t = useT(); t('search') → "Search" or "搜索"
 */

type Lang = "en" | "zh";

const translations: Record<string, Record<Lang, string>> = {
  // Navigation
  "nav.data_browser": { en: "Data Browser", zh: "数据浏览" },
  "nav.pipeline": { en: "Pipeline", zh: "处理流水线" },
  "nav.workspace": { en: "Workspace", zh: "工作区" },
  "nav.adql": { en: "ADQL", zh: "ADQL 查询" },
  "nav.team": { en: "Team", zh: "团队" },
  "nav.ai_assistant": { en: "AI Assistant", zh: "AI 助手" },
  "nav.settings": { en: "Settings", zh: "设置" },
  "nav.sign_in": { en: "Sign in", zh: "登录" },
  "nav.sign_out": { en: "Sign out", zh: "退出" },

  // Search
  "search.placeholder": { en: "Object name or coordinates (e.g. M31, 10.68 41.27)", zh: "天体名称或坐标（如 M31, 10.68 41.27）" },
  "search.quick": { en: "Quick Search", zh: "快速搜索" },
  "search.advanced": { en: "Advanced Search", zh: "高级搜索" },
  "search.searching": { en: "Searching\u2026", zh: "搜索中\u2026" },
  "search.search": { en: "Search", zh: "搜索" },
  "search.radius": { en: "Radius", zh: "搜索半径" },
  "search.no_results": { en: "No results found. Try broadening your search criteria.", zh: "未找到结果。请尝试放宽搜索条件。" },
  "search.empty": { en: "Search for astronomical objects to see results", zh: "搜索天体以查看结果" },
  "search.results": { en: "results", zh: "条结果" },

  // Data Browser
  "data.source": { en: "Source", zh: "数据源" },
  "data.name": { en: "Name", zh: "名称" },
  "data.type": { en: "Type", zh: "类型" },
  "data.redshift": { en: "Redshift", zh: "红移" },
  "data.magnitude": { en: "Mag", zh: "星等" },
  "data.fetch_fits": { en: "Fetch FITS", zh: "获取 FITS" },
  "data.my_files": { en: "My Files", zh: "我的文件" },

  // Pipeline
  "pipeline.run": { en: "Run Pipeline", zh: "运行流水线" },
  "pipeline.clear": { en: "Clear All", zh: "全部清除" },
  "pipeline.save": { en: "Save Template", zh: "保存模板" },

  // AI Chat
  "chat.placeholder": { en: "Ask about astronomical data, or drop a FITS file...", zh: "询问天文数据相关问题，或拖入 FITS 文件..." },
  "chat.hint": { en: "Enter to send, Shift+Enter for new line. Drop a FITS file to analyze.", zh: "回车发送，Shift+回车换行。拖入 FITS 文件自动分析。" },
  "chat.analyzing": { en: "Analyzing spectrum (this may take 10-15 seconds)...", zh: "正在分析光谱（可能需要 10-15 秒）..." },
  "chat.analyze_btn": { en: "Analyze with AI", zh: "AI 分析" },

  // Auth
  "auth.sign_in": { en: "Sign In", zh: "登录" },
  "auth.create_account": { en: "Create Account", zh: "创建账号" },
  "auth.email": { en: "Email", zh: "邮箱" },
  "auth.password": { en: "Password", zh: "密码" },

  // Common
  "common.loading": { en: "Loading...", zh: "加载中..." },
  "common.save": { en: "Save", zh: "保存" },
  "common.saved": { en: "Saved", zh: "已保存" },
  "common.delete": { en: "Delete", zh: "删除" },
  "common.cancel": { en: "Cancel", zh: "取消" },
  "common.close": { en: "Close", zh: "关闭" },
  "common.export": { en: "Export", zh: "导出" },
  "common.download": { en: "Download", zh: "下载" },
};

let currentLang: Lang = (() => {
  const saved = localStorage.getItem("astro_lang");
  if (saved === "zh" || saved === "en") return saved;
  return navigator.language.startsWith("zh") ? "zh" : "en";
})();

export function getLang(): Lang {
  return currentLang;
}

export function setLang(lang: Lang): void {
  currentLang = lang;
  localStorage.setItem("astro_lang", lang);
}

export function t(key: string): string {
  return translations[key]?.[currentLang] ?? translations[key]?.en ?? key;
}

// React hook (re-renders on lang change via forced update)
import { useState, useCallback } from "react";

export function useT(): (key: string) => string {
  const [, setTick] = useState(0);
  const translate = useCallback((key: string) => t(key), []);
  // Expose a toggle that forces re-render
  void setTick; // suppress unused warning in non-toggle usage
  return translate;
}

export function useLang(): [Lang, (lang: Lang) => void] {
  const [lang, _setLang] = useState(currentLang);
  const toggle = useCallback((newLang: Lang) => {
    setLang(newLang);
    _setLang(newLang);
  }, []);
  return [lang, toggle];
}
