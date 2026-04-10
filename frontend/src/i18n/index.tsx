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
  "data.literature":   { en: "Literature",       zh: "文献搜索",      fr: "Littérature",      es: "Literatura" },
  "data.lit_placeholder": { en: "Search ADS/arXiv (e.g. 'high-z quasars', 'JWST galaxies')", zh: "搜索 ADS/arXiv（如 'high-z quasars'）", fr: "Rechercher ADS/arXiv (ex. 'high-z quasars')", es: "Buscar ADS/arXiv (ej. 'high-z quasars')" },
  "data.lit_search":   { en: "Search Literature", zh: "搜索文献",      fr: "Rechercher",       es: "Buscar" },

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
  "auth.welcome":         { en: "Welcome back to Standard Astro", zh: "欢迎回到 Standard Astro", fr: "Bienvenue sur Standard Astro", es: "Bienvenido a Standard Astro" },
  "auth.start":           { en: "Start exploring the universe", zh: "开始探索宇宙", fr: "Commencez à explorer l'univers", es: "Comience a explorar el universo" },

  // Research Templates
  "template.heading":         { en: "Research Templates", zh: "研究模板", fr: "Modèles de recherche", es: "Plantillas de investigación" },
  "template.hr_diagram":      { en: "HR Diagram & Stellar Evolution", zh: "HR 图与恒星演化", fr: "Diagramme HR & Évolution stellaire", es: "Diagrama HR y Evolución estelar" },
  "template.hr_desc":         { en: "Plot the HR diagram for the Pleiades, analyze evolutionary stages, and explain the distribution of main-sequence stars, red giants, and white dwarfs", zh: "帮我制作昴星团(Pleiades)的赫罗图，分析其中恒星的演化阶段，并解释主序星、红巨星和白矮星的分布", fr: "Tracez le diagramme HR des Pléiades, analysez les stades évolutifs et expliquez la distribution des étoiles", es: "Trazar el diagrama HR de las Pléyades, analizar las etapas evolutivas y explicar la distribución de estrellas" },
  "template.galaxy_redshift": { en: "Galaxy Redshift Distribution", zh: "星系红移分布", fr: "Distribution de décalage spectral", es: "Distribución de corrimiento al rojo" },
  "template.galaxy_desc":     { en: "Query galaxy redshift data from SDSS for a sky region, plot a redshift histogram, and analyze galaxy cluster signals", zh: "查询 SDSS 中一片天区的星系红移数据，绘制红移分布直方图，分析是否存在星系团的信号", fr: "Interroger les données de décalage spectral des galaxies SDSS et analyser les signaux d'amas", es: "Consultar datos de corrimiento al rojo de galaxias SDSS y analizar señales de cúmulos" },
  "template.variable_star":   { en: "Variable Star Period Detection", zh: "变星周期检测", fr: "Détection de période d'étoile variable", es: "Detección de período de estrella variable" },
  "template.variable_desc":   { en: "Find a known Cepheid variable, obtain its light curve, detect the period using Lomb-Scargle, and perform phase-folding analysis", zh: "帮我找一颗已知的造父变星，获取其光变曲线数据，用 Lomb-Scargle 方法检测周期，并进行相位折叠分析", fr: "Trouver une céphéide connue, obtenir sa courbe de lumière, détecter la période par Lomb-Scargle", es: "Encontrar una cefeida conocida, obtener su curva de luz, detectar el período con Lomb-Scargle" },
  "template.spectral":        { en: "Spectral Analysis", zh: "光谱分析与分类", fr: "Analyse spectrale", es: "Análisis espectral" },
  "template.spectral_desc":   { en: "Obtain a star's spectrum, identify absorption features, determine spectral type and radial velocity, and estimate effective temperature", zh: "获取一颗恒星的光谱数据，识别吸收线特征，测定光谱型和径向速度，估算其有效温度", fr: "Obtenir le spectre d'une étoile, identifier les raies d'absorption et déterminer le type spectral", es: "Obtener el espectro de una estrella, identificar líneas de absorción y determinar el tipo espectral" },
  "template.highz":           { en: "High-z Galaxy Selection", zh: "高红移星系筛选", fr: "Sélection de galaxies à haut z", es: "Selección de galaxias de alto z" },
  "template.highz_desc":      { en: "Use color-color diagram methods to select z>3 Lyman-break galaxy candidates from SDSS and analyze their color distribution", zh: "使用颜色-颜色图方法，从 SDSS 数据中筛选 z>3 的 Lyman-break 星系候选体，分析其颜色分布", fr: "Utiliser les diagrammes couleur-couleur pour sélectionner des galaxies Lyman-break z>3 dans SDSS", es: "Usar diagramas color-color para seleccionar galaxias Lyman-break z>3 de SDSS" },
  "template.supernova":       { en: "Supernova Follow-up", zh: "超新星后续观测", fr: "Suivi de supernova", es: "Seguimiento de supernova" },
  "template.supernova_desc":  { en: "Find recent supernovae, cross-match host galaxy info, analyze host redshift and morphology, and draft an observing proposal", zh: "查找最近一周发现的超新星，交叉匹配宿主星系信息，分析宿主星系的红移和形态类型，并撰写观测提案", fr: "Trouver les supernovae récentes, croiser les infos de galaxie hôte et rédiger une proposition", es: "Encontrar supernovas recientes, cruzar información de galaxias anfitrionas y redactar una propuesta" },
  "template.difficulty.beginner":     { en: "Beginner", zh: "入门", fr: "Débutant", es: "Principiante" },
  "template.difficulty.intermediate": { en: "Intermediate", zh: "中级", fr: "Intermédiaire", es: "Intermedio" },
  "template.difficulty.advanced":     { en: "Advanced", zh: "高级", fr: "Avancé", es: "Avanzado" },

  // Search Suggestions
  "search.suggestions_heading": { en: "Try searching for:", zh: "试试搜索：", fr: "Essayez de chercher :", es: "Prueba buscar:" },
  "search.suggestion.m31":      { en: "Andromeda Galaxy", zh: "仙女座星系", fr: "Galaxie d'Andromède", es: "Galaxia de Andrómeda" },
  "search.suggestion.sirius":   { en: "Brightest star", zh: "天狼星", fr: "Étoile la plus brillante", es: "Estrella más brillante" },
  "search.suggestion.crab":     { en: "Supernova remnant", zh: "蟹状星云", fr: "Rémanent de supernova", es: "Remanente de supernova" },
  "search.suggestion.coords":   { en: "Coordinates search", zh: "坐标搜索", fr: "Recherche par coordonnées", es: "Búsqueda por coordenadas" },

  // Column Explainer
  "columns.explainer_toggle":   { en: "What do these columns mean?", zh: "这些列是什么意思？", fr: "Que signifient ces colonnes ?", es: "Qué significan estas columnas?" },
  "columns.name_desc":          { en: "Object identifier from the catalog", zh: "来自星表的天体标识符", fr: "Identifiant de l'objet dans le catalogue", es: "Identificador del objeto en el catálogo" },
  "columns.ra_desc":            { en: "Sky coordinates (like latitude/longitude for the sky)", zh: "天球坐标（类似于天空的经纬度）", fr: "Coordonnées célestes (comme la latitude/longitude du ciel)", es: "Coordenadas celestes (como latitud/longitud del cielo)" },
  "columns.mag_desc":           { en: "Brightness (lower = brighter; Sun = -26.7, faintest visible = +6)", zh: "亮度（数值越小越亮；太阳 = -26.7，肉眼极限 = +6）", fr: "Luminosité (plus bas = plus brillant ; Soleil = -26.7)", es: "Brillo (menor = más brillante; Sol = -26.7, límite visible = +6)" },
  "columns.redshift_desc":      { en: "How fast it's receding (0 = stationary, 1 = very distant)", zh: "退行速度（0 = 静止，1 = 非常遥远）", fr: "Vitesse de récession (0 = stationnaire, 1 = très distant)", es: "Velocidad de recesión (0 = estacionario, 1 = muy distante)" },
  "columns.type_desc":          { en: "Classification (star, galaxy, quasar, etc.)", zh: "分类（恒星、星系、类星体等）", fr: "Classification (étoile, galaxie, quasar, etc.)", es: "Clasificación (estrella, galaxia, quásar, etc.)" },
  "columns.source_desc":        { en: "Which database this result came from", zh: "该结果来自哪个数据库", fr: "De quelle base de données provient ce résultat", es: "De qué base de datos proviene este resultado" },

  // Common
  // Errors
  "error.network":     { en: "Cannot reach the server. Check your connection or try again.", zh: "无法连接服务器。请检查网络连接或稍后重试。", fr: "Impossible de contacter le serveur. Vérifiez votre connexion.", es: "No se puede conectar al servidor. Verifique su conexión." },
  "error.timeout":     { en: "Request timed out. The data source may be slow. Try again.", zh: "请求超时。数据源可能较慢，请重试。", fr: "La requête a expiré. La source de données peut être lente.", es: "La solicitud ha expirado. La fuente de datos puede ser lenta." },
  "error.mast_tip":    { en: "MAST service timed out. Try searching SIMBAD or Gaia instead.", zh: "MAST 服务超时。建议改用 SIMBAD 或 Gaia 搜索。", fr: "Le service MAST a expiré. Essayez SIMBAD ou Gaia.", es: "El servicio MAST ha expirado. Intente con SIMBAD o Gaia." },
  "error.sdss_tip":    { en: "SDSS SkyServer may be overloaded. Try again in a few minutes.", zh: "SDSS SkyServer 可能过载。请几分钟后重试。", fr: "SDSS SkyServer peut être surchargé. Réessayez dans quelques minutes.", es: "SDSS SkyServer puede estar sobrecargado. Intente en unos minutos." },

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
