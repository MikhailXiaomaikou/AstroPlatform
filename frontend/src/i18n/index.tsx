/**
 * i18n with React Context — language changes trigger global re-render.
 * Supports: English, Chinese, French, Spanish.
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

export type Lang = "en" | "zh" | "fr" | "es";

const translations: Record<string, Record<Lang, string>> = {
  // Navigation
  "nav.data_browser":  { en: "Data Browser",    zh: "数据浏览",      fr: "Explorateur",     es: "Explorador" },
  "nav.pipeline":      { en: "Pipeline",        zh: "处理流水线",    fr: "Pipeline",        es: "Pipeline" },
  "nav.workspace":     { en: "Workspace",       zh: "工作区",        fr: "Espace de travail", es: "Espacio" },
  "nav.adql":          { en: "ADQL",            zh: "ADQL 查询",     fr: "ADQL",            es: "ADQL" },
  "nav.team":          { en: "Team",            zh: "团队",          fr: "Équipe",          es: "Equipo" },
  "nav.ai_assistant":  { en: "AI Assistant",    zh: "AI 助手",       fr: "Assistant IA",    es: "Asistente IA" },
  "nav.settings":      { en: "Settings",        zh: "设置",          fr: "Paramètres",      es: "Ajustes" },
  "nav.sign_in":       { en: "Sign in",         zh: "登录",          fr: "Connexion",       es: "Iniciar sesión" },
  "nav.sign_out":      { en: "Sign out",        zh: "退出",          fr: "Déconnexion",     es: "Cerrar sesión" },

  // Search
  "search.placeholder": { en: "Object name or coordinates (e.g. M31, 10.68 41.27)", zh: "天体名称或坐标（如 M31, 10.68 41.27）", fr: "Nom d'objet ou coordonnées (ex. M31)", es: "Nombre u coordenadas (ej. M31)" },
  "search.quick":       { en: "Quick Search",    zh: "快速搜索",      fr: "Recherche rapide", es: "Búsqueda rápida" },
  "search.advanced":    { en: "Advanced Search",  zh: "高级搜索",      fr: "Recherche avancée", es: "Búsqueda avanzada" },
  "search.searching":   { en: "Searching\u2026",  zh: "搜索中\u2026",   fr: "Recherche\u2026",  es: "Buscando\u2026" },
  "search.search":      { en: "Search",           zh: "搜索",          fr: "Rechercher",       es: "Buscar" },
  "search.radius":      { en: "Radius",           zh: "搜索半径",      fr: "Rayon",            es: "Radio" },
  "search.no_results":  { en: "No results found. Try broadening your search criteria.", zh: "未找到结果。请尝试放宽搜索条件。", fr: "Aucun résultat. Essayez d'élargir vos critères.", es: "Sin resultados. Intente ampliar sus criterios." },
  "search.empty":       { en: "Search for astronomical objects to see results", zh: "搜索天体以查看结果", fr: "Recherchez des objets astronomiques", es: "Busque objetos astronómicos" },
  "search.results":     { en: "results",          zh: "条结果",        fr: "résultats",        es: "resultados" },

  // Data Browser
  "data.source":       { en: "Source",           zh: "数据源",        fr: "Source",           es: "Fuente" },
  "data.name":         { en: "Name",             zh: "名称",          fr: "Nom",              es: "Nombre" },
  "data.type":         { en: "Type",             zh: "类型",          fr: "Type",             es: "Tipo" },
  "data.redshift":     { en: "Redshift",         zh: "红移",          fr: "Décalage",         es: "Corrimiento" },
  "data.magnitude":    { en: "Mag",              zh: "星等",          fr: "Mag",              es: "Mag" },
  "data.fetch_fits":   { en: "Fetch FITS",       zh: "获取 FITS",     fr: "Télécharger FITS", es: "Obtener FITS" },
  "data.my_files":     { en: "My Files",         zh: "我的文件",      fr: "Mes fichiers",     es: "Mis archivos" },

  // Pipeline
  "pipeline.run":      { en: "Run Pipeline",     zh: "运行流水线",    fr: "Exécuter",         es: "Ejecutar" },
  "pipeline.clear":    { en: "Clear All",        zh: "全部清除",      fr: "Tout effacer",     es: "Borrar todo" },
  "pipeline.save":     { en: "Save Template",    zh: "保存模板",      fr: "Sauvegarder",      es: "Guardar" },

  // AI Chat
  "chat.placeholder":  { en: "Ask about astronomical data, or drop a FITS file...", zh: "询问天文数据相关问题，或拖入 FITS 文件...", fr: "Posez une question ou déposez un fichier FITS...", es: "Pregunte sobre datos astronómicos o suelte un FITS..." },
  "chat.hint":         { en: "Enter to send, Shift+Enter for new line. Drop a FITS file to analyze.", zh: "回车发送，Shift+回车换行。拖入 FITS 文件自动分析。", fr: "Entrée pour envoyer. Déposez un FITS pour analyser.", es: "Enter para enviar. Suelte un FITS para analizar." },
  "chat.analyzing":    { en: "Analyzing spectrum (this may take 10-15 seconds)...", zh: "正在分析光谱（可能需要 10-15 秒）...", fr: "Analyse du spectre (10-15 secondes)...", es: "Analizando espectro (10-15 segundos)..." },
  "chat.analyze_btn":  { en: "Analyze with AI",  zh: "AI 分析",       fr: "Analyser avec IA", es: "Analizar con IA" },
  "chat.history":      { en: "History",           zh: "历史",          fr: "Historique",       es: "Historial" },
  "chat.save":         { en: "Save",              zh: "保存",          fr: "Sauvegarder",      es: "Guardar" },
  "chat.new_chat":     { en: "New Chat",          zh: "新对话",        fr: "Nouveau chat",     es: "Nuevo chat" },

  // Auth
  "auth.sign_in":         { en: "Sign In",         zh: "登录",          fr: "Connexion",        es: "Iniciar sesión" },
  "auth.create_account":  { en: "Create Account",  zh: "创建账号",      fr: "Créer un compte",  es: "Crear cuenta" },
  "auth.email":           { en: "Email",            zh: "邮箱",          fr: "E-mail",           es: "Correo" },
  "auth.password":        { en: "Password",         zh: "密码",          fr: "Mot de passe",     es: "Contraseña" },
  "auth.welcome":         { en: "Welcome back to Astro Platform", zh: "欢迎回到 Astro Platform", fr: "Bienvenue sur Astro Platform", es: "Bienvenido a Astro Platform" },
  "auth.start":           { en: "Start exploring the universe", zh: "开始探索宇宙", fr: "Commencez à explorer l'univers", es: "Comience a explorar el universo" },

  // Common
  "common.loading":    { en: "Loading...",        zh: "加载中...",      fr: "Chargement...",    es: "Cargando..." },
  "common.save":       { en: "Save",              zh: "保存",          fr: "Sauvegarder",      es: "Guardar" },
  "common.saved":      { en: "Saved",             zh: "已保存",        fr: "Sauvegardé",       es: "Guardado" },
  "common.delete":     { en: "Delete",            zh: "删除",          fr: "Supprimer",        es: "Eliminar" },
  "common.cancel":     { en: "Cancel",            zh: "取消",          fr: "Annuler",          es: "Cancelar" },
  "common.close":      { en: "Close",             zh: "关闭",          fr: "Fermer",           es: "Cerrar" },
  "common.export":     { en: "Export",            zh: "导出",          fr: "Exporter",         es: "Exportar" },
  "common.download":   { en: "Download",          zh: "下载",          fr: "Télécharger",      es: "Descargar" },
  "common.previous":   { en: "Previous",          zh: "上一页",        fr: "Précédent",        es: "Anterior" },
  "common.next":       { en: "Next",              zh: "下一页",        fr: "Suivant",          es: "Siguiente" },
};

// ── Language detection ──

function detectLang(): Lang {
  const saved = localStorage.getItem("astro_lang");
  if (saved && ["en", "zh", "fr", "es"].includes(saved)) return saved as Lang;
  const nav = navigator.language.toLowerCase();
  if (nav.startsWith("zh")) return "zh";
  if (nav.startsWith("fr")) return "fr";
  if (nav.startsWith("es")) return "es";
  return "en";
}

// ── Translate function ──

let currentLang: Lang = detectLang();

export function t(key: string): string {
  return translations[key]?.[currentLang] ?? translations[key]?.en ?? key;
}

// ── React Context for global re-render on language change ──

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => {},
  t: (key: string) => key,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, _setLang] = useState<Lang>(detectLang);

  const setLang = useCallback((newLang: Lang) => {
    currentLang = newLang;
    localStorage.setItem("astro_lang", newLang);
    _setLang(newLang);
  }, []);

  const translate = useCallback((key: string): string => {
    return translations[key]?.[lang] ?? translations[key]?.en ?? key;
  }, [lang]);

  return (
    <I18nContext value={{ lang, setLang, t: translate }}>
      {children}
    </I18nContext>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}

// Convenience hooks (backward compat)
export function useT(): (key: string) => string {
  const { t: translate } = useI18n();
  return translate;
}

export function useLang(): [Lang, (lang: Lang) => void] {
  const { lang, setLang } = useI18n();
  return [lang, setLang];
}

// Language display names
export const LANG_NAMES: Record<Lang, string> = {
  en: "English",
  zh: "中文",
  fr: "Français",
  es: "Español",
};

export const ALL_LANGS: Lang[] = ["en", "zh", "fr", "es"];
