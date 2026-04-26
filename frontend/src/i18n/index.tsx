/**
 * i18n with React Context — language changes trigger global re-render.
 * Supports: English, Chinese, French, Spanish.
 *
 * PART Y Q3: this file deliberately co-exports the I18nProvider component
 * with companion hooks (useT/useLang/useI18n), the t() raw lookup, and
 * the LANG_NAMES / ALL_LANGS metadata constants. That mixed-export shape
 * is the canonical React Context idiom — splitting it across multiple
 * files would force every consumer (~50 components) to update imports
 * for no observable user benefit. HMR fast-refresh penalty for these
 * exports is accepted.
 */
/* eslint-disable react-refresh/only-export-components */

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
  "nav.home":          { en: "Home",            zh: "首页",          fr: "Accueil",         es: "Inicio" },
  "nav.browse":        { en: "Browse",          zh: "数据浏览",      fr: "Parcourir",       es: "Explorar" },
  "nav.sessions":      { en: "Sessions",        zh: "会话存档",      fr: "Sessions",        es: "Sesiones" },
  "nav.papers":        { en: "Papers",          zh: "论文",          fr: "Articles",        es: "Artículos" },
  "nav.settings":      { en: "Settings",        zh: "设置",          fr: "Paramètres",      es: "Ajustes" },
  "nav.account":       { en: "Account",         zh: "账户",          fr: "Compte",          es: "Cuenta" },
  "nav.alerts":        { en: "Alerts",          zh: "警报",          fr: "Alertes",         es: "Alertas" },
  "nav.anomalies":     { en: "Anomalies",       zh: "异常探测",      fr: "Anomalies",       es: "Anomalias" },
  "nav.observations":  { en: "Observations",    zh: "观测",          fr: "Observations",    es: "Observaciones" },
  "nav.sign_in":       { en: "Sign in",         zh: "登录",          fr: "Connexion",       es: "Iniciar sesión" },
  "nav.sign_out":      { en: "Sign out",        zh: "退出",          fr: "Déconnexion",     es: "Cerrar sesión" },

  // Landing Page
  "landing.subtitle":        { en: "AI-powered research platform \u2014 query 19 databases, analyze spectra, build pipelines, and draft papers with one conversation.", zh: "AI 驱动的研究平台 \u2014 对话即可查询 19 个数据库、分析光谱、构建流水线、撰写论文。", fr: "Plateforme de recherche pilotée par l'IA \u2014 interrogez 19 bases de données, analysez des spectres et rédigez des articles en une conversation.", es: "Plataforma de investigación impulsada por IA \u2014 consulte 19 bases de datos, analice espectros, construya pipelines y redacte artículos en una conversación." },
  "landing.feat_ai":         { en: "AI Research Assistant",   zh: "AI 研究助手",         fr: "Assistant de recherche IA",   es: "Asistente de investigación IA" },
  "landing.feat_ai_desc":    { en: "Chat with an AI that queries 21 databases, writes ADQL, builds pipelines, runs Python, and drafts papers.", zh: "与 AI 对话：查询 21 个数据库、编写 ADQL、构建流水线、运行 Python、撰写论文。", fr: "Discutez avec une IA qui interroge 21 bases de données, écrit des requêtes ADQL, construit des pipelines et rédige des articles.", es: "Converse con una IA que consulta 21 bases de datos, escribe ADQL, construye pipelines, ejecuta Python y redacta artículos." },
  "landing.feat_search":     { en: "Multi-Source Search",     zh: "多源搜索",            fr: "Recherche multi-sources",     es: "Búsqueda multifuente" },
  "landing.feat_search_desc": { en: "Search SDSS, Gaia, SIMBAD, and 18 more databases simultaneously with natural language or coordinates.", zh: "使用自然语言或坐标同时搜索 SDSS、Gaia、SIMBAD 等 18+ 个数据库。", fr: "Recherchez simultanément dans SDSS, Gaia, SIMBAD et 18 autres bases de données en langage naturel ou par coordonnées.", es: "Busque simultáneamente en SDSS, Gaia, SIMBAD y 18+ bases de datos con lenguaje natural o coordenadas." },
  "landing.feat_pipeline":   { en: "Visual Pipeline Builder", zh: "可视化流水线构建器",    fr: "Constructeur visuel de pipeline", es: "Constructor visual de pipelines" },
  "landing.feat_pipeline_desc": { en: "Build analysis workflows by connecting nodes \u2014 denoise, fit spectra, cross-match catalogs, and plot results.", zh: "通过连接节点构建分析工作流 \u2014 降噪、光谱拟合、交叉匹配、绘制结果。", fr: "Construisez des workflows d'analyse en connectant des n\u0153uds \u2014 débruitez, ajustez des spectres, croisez des catalogues et tracez les résultats.", es: "Construya flujos de análisis conectando nodos \u2014 elimine ruido, ajuste espectros, cruce catálogos y grafique resultados." },
  "landing.feat_adql":       { en: "ADQL Direct Query",       zh: "ADQL 直接查询",       fr: "Requête ADQL directe",        es: "Consulta ADQL directa" },
  "landing.feat_adql_desc":  { en: "Write TAP queries with syntax highlighting against Gaia, SIMBAD, VizieR, and CADC services.", zh: "使用语法高亮对 Gaia、SIMBAD、VizieR 和 CADC 服务编写 TAP 查询。", fr: "Écrivez des requêtes TAP avec coloration syntaxique sur les services Gaia, SIMBAD, VizieR et CADC.", es: "Escriba consultas TAP con resaltado de sintaxis en los servicios Gaia, SIMBAD, VizieR y CADC." },
  "landing.cta_ai":          { en: "Start with AI Assistant",  zh: "开始使用 AI 助手",   fr: "Commencer avec l'assistant IA", es: "Empezar con el asistente IA" },
  "landing.cta_browse":      { en: "Browse Data",             zh: "浏览数据",            fr: "Explorer les données",         es: "Explorar datos" },
  "landing.stat_ai_tools":   { en: "AI Tools",                zh: "AI 工具",             fr: "Outils IA",                    es: "Herramientas IA" },
  "landing.stat_databases":  { en: "Databases",               zh: "数据库",              fr: "Bases de données",             es: "Bases de datos" },
  "landing.stat_pipeline_nodes": { en: "Pipeline Nodes",      zh: "流水线节点",          fr: "N\u0153uds de pipeline",       es: "Nodos de pipeline" },
  "landing.stat_languages":  { en: "Languages",               zh: "语言",                fr: "Langues",                      es: "Idiomas" },

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

  // Alerts
  "alerts.title":      { en: "Alert Dashboard",  zh: "警报仪表板",    fr: "Tableau d'alertes", es: "Panel de alertas" },
  "alerts.stats":      { en: "Total Alerts",     zh: "警报总数",      fr: "Total des alertes", es: "Total de alertas" },
  "alerts.no_alerts":  { en: "No alerts found for this time window.", zh: "在此时间窗口中未找到警报。", fr: "Aucune alerte trouvée pour cette période.", es: "No se encontraron alertas para este periodo." },

  // Command Palette
  "cmd.placeholder":     { en: "Type a command...",     zh: "输入命令...",         fr: "Tapez une commande...",   es: "Escriba un comando..." },
  "cmd.no_match":        { en: "No matching commands",  zh: "无匹配命令",          fr: "Aucune commande trouvée", es: "Sin comandos coincidentes" },
  "cmd.cat_nav":         { en: "Navigation",            zh: "导航",               fr: "Navigation",              es: "Navegación" },
  "cmd.cat_action":      { en: "Actions",               zh: "操作",               fr: "Actions",                 es: "Acciones" },
  "cmd.search_objects":  { en: "Search Objects",        zh: "搜索天体",            fr: "Rechercher des objets",   es: "Buscar objetos" },
  "cmd.ai_assistant":    { en: "AI Assistant",          zh: "AI 助手",             fr: "Assistant IA",            es: "Asistente IA" },
  "cmd.pipeline_studio": { en: "Pipeline Studio",      zh: "流水线工作室",         fr: "Studio Pipeline",         es: "Estudio Pipeline" },
  "cmd.adql_query":      { en: "ADQL Query",           zh: "ADQL 查询",           fr: "Requête ADQL",            es: "Consulta ADQL" },
  "cmd.workspace":       { en: "Workspace",            zh: "工作区",              fr: "Espace de travail",       es: "Espacio de trabajo" },
  "cmd.team":            { en: "Team",                  zh: "团队",               fr: "Équipe",                  es: "Equipo" },
  "cmd.alerts":          { en: "Transient Alerts",      zh: "瞬变警报",            fr: "Alertes transitoires",    es: "Alertas transitorias" },
  "cmd.anomalies":       { en: "Anomaly Explorer",     zh: "异常探测器",           fr: "Explorateur d'anomalies", es: "Explorador de anomalías" },
  "cmd.settings":        { en: "Settings",             zh: "设置",               fr: "Paramètres",              es: "Ajustes" },
  "cmd.account":         { en: "Account",              zh: "账户",               fr: "Compte",                  es: "Cuenta" },
  "cmd.help":            { en: "Help & Docs",          zh: "帮助与文档",           fr: "Aide & Documentation",    es: "Ayuda y Documentación" },
  "cmd.new_chat":        { en: "New Chat Session",     zh: "新建对话",             fr: "Nouvelle conversation",   es: "Nueva conversación" },
  "cmd.new_pipeline":    { en: "New Pipeline",         zh: "新建流水线",           fr: "Nouveau pipeline",        es: "Nuevo pipeline" },
  "cmd.navigate":        { en: "Navigate",             zh: "导航",               fr: "Naviguer",                es: "Navegar" },
  "cmd.select":          { en: "Select",               zh: "选择",               fr: "Sélectionner",            es: "Seleccionar" },

  // Onboarding
  "onboard.step_of":          { en: "Step",              zh: "步骤",          fr: "Étape",       es: "Paso" },
  "onboard.of":               { en: "of",                zh: "/",             fr: "sur",         es: "de" },
  "onboard.skip":             { en: "Skip Tour",         zh: "跳过引导",      fr: "Passer",      es: "Omitir" },
  "onboard.back":             { en: "Back",              zh: "上一步",        fr: "Retour",      es: "Atrás" },
  "onboard.next":             { en: "Next",              zh: "下一步",        fr: "Suivant",     es: "Siguiente" },
  "onboard.start":            { en: "Get Started",       zh: "开始使用",      fr: "Commencer",   es: "Empezar" },
  "onboard.search_title":     { en: "Search the Universe", zh: "搜索宇宙",   fr: "Explorer l'Univers", es: "Explorar el Universo" },
  "onboard.search_desc":      { en: "Query 19+ astronomical databases simultaneously. Enter any object name, coordinates, or use natural language like 'galaxies with redshift > 0.5'.", zh: "同时查询 19+ 个天文数据库。输入天体名称、坐标，或使用自然语言如「红移大于 0.5 的星系」。", fr: "Interrogez 19+ bases de données astronomiques simultanément. Saisissez un nom d'objet, des coordonnées ou du langage naturel.", es: "Consulte 19+ bases de datos astronómicas simultáneamente. Ingrese un nombre de objeto, coordenadas o lenguaje natural." },
  "onboard.chat_title":       { en: "AI Research Assistant", zh: "AI 研究助手", fr: "Assistant de Recherche IA", es: "Asistente de Investigación IA" },
  "onboard.chat_desc":        { en: "Chat with an AI that understands astronomy. It can search catalogs, run ADQL queries, generate pipelines, and help write papers.", zh: "与理解天文学的 AI 对话。它可以搜索星表、运行 ADQL 查询、生成流水线并协助撰写论文。", fr: "Discutez avec une IA qui comprend l'astronomie. Elle peut interroger des catalogues, exécuter des requêtes ADQL et générer des pipelines.", es: "Hable con una IA que entiende astronomía. Puede buscar catálogos, ejecutar consultas ADQL, generar pipelines y ayudar a escribir artículos." },
  "onboard.pipeline_title":   { en: "Visual Pipeline Builder", zh: "可视化流水线构建器", fr: "Constructeur visuel de Pipeline", es: "Constructor visual de Pipeline" },
  "onboard.pipeline_desc":    { en: "Build data processing workflows by dragging nodes. Denoise, fit spectra, cross-match catalogs, and more — all connected in a visual DAG.", zh: "通过拖拽节点构建数据处理工作流。降噪、光谱拟合、交叉匹配等——全部以可视化 DAG 连接。", fr: "Construisez des workflows de traitement de données en glissant-déposant des nœuds. Dénoisez, ajustez des spectres, croisez des catalogues.", es: "Construya flujos de trabajo de procesamiento de datos arrastrando nodos. Elimine ruido, ajuste espectros, cruce catálogos." },
  "onboard.adql_title":       { en: "Direct Database Access", zh: "直接数据库访问", fr: "Accès direct aux bases de données", es: "Acceso directo a bases de datos" },
  "onboard.adql_desc":        { en: "Write ADQL queries against Gaia, SIMBAD, VizieR, and CADC TAP services with syntax highlighting and template queries.", zh: "使用语法高亮和模板查询，直接对 Gaia、SIMBAD、VizieR 和 CADC TAP 服务编写 ADQL 查询。", fr: "Écrivez des requêtes ADQL sur les services Gaia, SIMBAD, VizieR et CADC TAP avec coloration syntaxique.", es: "Escriba consultas ADQL en los servicios Gaia, SIMBAD, VizieR y CADC TAP con resaltado de sintaxis." },

  // Column management
  "col.toggle":       { en: "Columns",         zh: "列管理",        fr: "Colonnes",          es: "Columnas" },
  "col.toggle_title":  { en: "Toggle columns",  zh: "切换列显示",    fr: "Basculer colonnes", es: "Alternar columnas" },
  "col.all":           { en: "All",             zh: "全选",          fr: "Tout",              es: "Todo" },
  "col.none":          { en: "None",            zh: "清除",          fr: "Aucun",             es: "Ninguno" },
  "col.core":          { en: "Core",            zh: "核心",          fr: "Principal",         es: "Principal" },
  "col.extra":         { en: "Extra",           zh: "附加",          fr: "Supplémentaire",    es: "Extra" },

  // Pipeline result panel
  "pipeline.node_result": { en: "Node:",          zh: "节点:",         fr: "Nœud :",           es: "Nodo:" },

  // Chat import
  "chat.import":           { en: "Import",                        zh: "导入",               fr: "Importer",              es: "Importar" },
  "chat.import_title":     { en: "Import session from JSON file", zh: "从 JSON 文件导入对话", fr: "Importer depuis un fichier JSON", es: "Importar sesión desde archivo JSON" },

  // Object Detail Panel
  "odp.overview":         { en: "Overview",            zh: "概览",            fr: "Aperçu",             es: "Resumen" },
  "odp.surveys":          { en: "Surveys",             zh: "巡天数据",        fr: "Relevés",            es: "Sondeos" },
  "odp.references":       { en: "References",          zh: "参考文献",        fr: "Références",         es: "Referencias" },
  "odp.raw_data":         { en: "Raw Data",            zh: "原始数据",        fr: "Données brutes",     es: "Datos crudos" },
  "odp.spectral_type":    { en: "Spectral Type",       zh: "光谱型",          fr: "Type spectral",      es: "Tipo espectral" },
  "odp.morphology":       { en: "Morphology",          zh: "形态",            fr: "Morphologie",        es: "Morfología" },
  "odp.redshift":         { en: "Redshift",            zh: "红移",            fr: "Décalage vers le rouge", es: "Corrimiento al rojo" },
  "odp.radial_velocity":  { en: "Radial Velocity",     zh: "径向速度",        fr: "Vitesse radiale",    es: "Velocidad radial" },
  "odp.parallax":         { en: "Parallax",            zh: "视差",            fr: "Parallaxe",          es: "Paralaje" },
  "odp.pm_ra":            { en: "PM RA",               zh: "自行 RA",         fr: "MP AD",              es: "MP AR" },
  "odp.pm_dec":           { en: "PM Dec",              zh: "自行 Dec",        fr: "MP Déc",             es: "MP Dec" },
  "odp.cross_ids":        { en: "Cross-Identifications", zh: "交叉证认",      fr: "Identifications croisées", es: "Identificaciones cruzadas" },
  "odp.loading":          { en: "Loading object data...", zh: "正在加载天体数据...", fr: "Chargement des données...", es: "Cargando datos del objeto..." },
  "odp.failed_to_load":   { en: "Failed to load",      zh: "加载失败",        fr: "Échec du chargement", es: "Error al cargar" },
  "odp.save_to_bookmarks": { en: "Save to bookmarks",  zh: "保存到收藏夹",    fr: "Sauvegarder dans les favoris", es: "Guardar en favoritos" },
  "odp.saved_to_bookmarks": { en: "Saved to bookmarks", zh: "已保存到收藏夹",  fr: "Sauvegardé dans les favoris", es: "Guardado en favoritos" },
  "odp.no_survey_data":   { en: "No survey data queried yet.", zh: "尚未查询巡天数据。", fr: "Aucune donnée de relevé interrogée.", es: "Aún no se han consultado datos de sondeos." },
  "odp.no_data":          { en: "No data",             zh: "无数据",          fr: "Aucune donnée",      es: "Sin datos" },
  "odp.no_refs":          { en: "No references found. (ADS API key may not be configured.)", zh: "未找到参考文献。（ADS API 密钥可能未配置。）", fr: "Aucune référence trouvée. (La clé API ADS n'est peut-être pas configurée.)", es: "No se encontraron referencias. (La clave API de ADS puede no estar configurada.)" },
  "odp.no_raw_data":      { en: "No raw data available.", zh: "无原始数据。",   fr: "Aucune donnée brute disponible.", es: "No hay datos crudos disponibles." },
  "odp.results":          { en: "results",             zh: "条结果",          fr: "résultats",          es: "resultados" },

  // Help Drawer
  "help.drawer_title":    { en: "Help & Documentation", zh: "帮助与文档",     fr: "Aide et Documentation", es: "Ayuda y Documentación" },
  "help.button_label":    { en: "Help",                zh: "帮助",            fr: "Aide",               es: "Ayuda" },

  // FITS Browser
  "fits.manager_title":   { en: "FITS File Manager",   zh: "FITS 文件管理器", fr: "Gestionnaire de fichiers FITS", es: "Gestor de archivos FITS" },
  "fits.drop_files":      { en: "Drop FITS files here or click to browse", zh: "拖放 FITS 文件到此处或点击浏览", fr: "Déposez des fichiers FITS ici ou cliquez pour parcourir", es: "Suelte archivos FITS aquí o haga clic para explorar" },
  "fits.uploading":       { en: "Uploading...",        zh: "上传中...",       fr: "Téléversement...",    es: "Subiendo..." },
  "fits.upload_failed":   { en: "Upload failed",       zh: "上传失败",        fr: "Échec du téléversement", es: "Error al subir" },
  "fits.delete_confirm":  { en: "Delete this file?",   zh: "删除此文件？",    fr: "Supprimer ce fichier ?", es: "¿Eliminar este archivo?" },
  "fits.delete_failed":   { en: "Delete failed",       zh: "删除失败",        fr: "Échec de la suppression", es: "Error al eliminar" },
  "fits.sign_in_required": { en: "Sign in to manage your FITS files.", zh: "请登录以管理您的 FITS 文件。", fr: "Connectez-vous pour gérer vos fichiers FITS.", es: "Inicie sesión para gestionar sus archivos FITS." },
  "fits.failed_to_load":  { en: "Failed to load files", zh: "加载文件失败",   fr: "Échec du chargement des fichiers", es: "Error al cargar archivos" },
  "fits.all_sources":     { en: "All sources",         zh: "所有来源",        fr: "Toutes les sources",  es: "Todas las fuentes" },
  "fits.uploaded":        { en: "Uploaded",            zh: "已上传",          fr: "Téléversé",           es: "Subido" },
  "fits.refresh":         { en: "Refresh",             zh: "刷新",            fr: "Actualiser",          es: "Actualizar" },
  "fits.no_files":        { en: "No FITS files yet. Upload one to get started.", zh: "暂无 FITS 文件。上传一个以开始。", fr: "Aucun fichier FITS. Téléversez-en un pour commencer.", es: "Aún no hay archivos FITS. Suba uno para empezar." },
  "fits.use":             { en: "Use",                 zh: "使用",            fr: "Utiliser",            es: "Usar" },
  "fits.preview":         { en: "Preview",             zh: "预览",            fr: "Aperçu",              es: "Vista previa" },
  "fits.loading_viz":     { en: "Loading visualization...", zh: "正在加载可视化...", fr: "Chargement de la visualisation...", es: "Cargando visualización..." },

  // Auth Page
  "auth.setup_key":       { en: "Setup Key",           zh: "设置密钥",        fr: "Clé de configuration", es: "Clave de configuración" },
  "auth.enter_setup_key": { en: "Enter your setup key to get started", zh: "输入您的设置密钥以开始", fr: "Entrez votre clé de configuration pour commencer", es: "Ingrese su clave de configuración para empezar" },
  "auth.activate":        { en: "Activate",            zh: "激活",            fr: "Activer",             es: "Activar" },
  "auth.username":        { en: "Username",            zh: "用户名",          fr: "Nom d'utilisateur",   es: "Nombre de usuario" },
  "auth.your_password":   { en: "Your password",       zh: "您的密码",        fr: "Votre mot de passe",  es: "Su contraseña" },
  "auth.min_8_chars":     { en: "Min 8 characters",    zh: "至少 8 个字符",   fr: "8 caractères minimum", es: "Mínimo 8 caracteres" },
  "auth.no_account":      { en: "Don't have an account? Sign up", zh: "没有账号？注册", fr: "Pas de compte ? Inscrivez-vous", es: "¿No tiene cuenta? Regístrese" },
  "auth.has_account":     { en: "Already have an account? Sign in", zh: "已有账号？登录", fr: "Déjà un compte ? Connectez-vous", es: "¿Ya tiene cuenta? Inicie sesión" },
  "auth.use_password":    { en: "Use username & password instead", zh: "改用用户名和密码", fr: "Utiliser nom d'utilisateur et mot de passe", es: "Usar nombre de usuario y contraseña" },
  "auth.have_setup_key":  { en: "Have a setup key?",   zh: "有设置密钥？",    fr: "Vous avez une clé de configuration ?", es: "¿Tiene una clave de configuración?" },
  "auth.or":              { en: "or",                  zh: "或",              fr: "ou",                  es: "o" },
  "auth.failed":          { en: "Authentication failed", zh: "认证失败",      fr: "Échec de l'authentification", es: "Error de autenticación" },
  "auth.google_failed":   { en: "Google login failed",  zh: "Google 登录失败", fr: "Échec de la connexion Google", es: "Error de inicio de sesión con Google" },

  // Pipeline Node Types
  "pipeline.node_query_data":      { en: "Query Data",         zh: "查询数据",        fr: "Requête de données",  es: "Consultar datos" },
  "pipeline.node_import_workspace": { en: "Import Workspace",  zh: "导入工作区",      fr: "Importer l'espace de travail", es: "Importar espacio de trabajo" },
  "pipeline.node_load_data":       { en: "Load Data",          zh: "加载数据",        fr: "Charger les données", es: "Cargar datos" },
  "pipeline.node_bias_subtract":   { en: "Bias Subtract",      zh: "偏置扣除",        fr: "Soustraction du biais", es: "Sustracción de sesgo" },
  "pipeline.node_dark_correct":    { en: "Dark Correct",       zh: "暗场校正",        fr: "Correction du noir",  es: "Corrección de oscuridad" },
  "pipeline.node_flat_field":      { en: "Flat Field",         zh: "平场校正",        fr: "Champ plat",          es: "Campo plano" },
  "pipeline.node_cosmic_ray_reject": { en: "Cosmic Ray Reject", zh: "宇宙射线剔除",   fr: "Rejet de rayons cosmiques", es: "Rechazo de rayos cósmicos" },
  "pipeline.node_astrometric_solve": { en: "Astrometric Solve", zh: "天体测量求解",   fr: "Résolution astrométrique", es: "Resolución astrométrica" },
  "pipeline.node_source_extract":  { en: "Source Extract",     zh: "源提取",          fr: "Extraction de source", es: "Extracción de fuente" },
  "pipeline.node_denoise":         { en: "Denoise",            zh: "降噪",            fr: "Dénoisage",           es: "Eliminación de ruido" },
  "pipeline.node_spectral_fit":    { en: "Spectral Fit",       zh: "光谱拟合",        fr: "Ajustement spectral", es: "Ajuste espectral" },
  "pipeline.node_coord_transform": { en: "Coord Transform",    zh: "坐标变换",        fr: "Transformation de coordonnées", es: "Transformación de coordenadas" },
  "pipeline.node_plot":            { en: "Plot",               zh: "绘图",            fr: "Graphique",           es: "Gráfico" },
  "pipeline.node_redshift_estimate": { en: "Redshift Estimate", zh: "红移估计",       fr: "Estimation du décalage", es: "Estimación de corrimiento" },
  "pipeline.node_equivalent_width": { en: "Equivalent Width",  zh: "等值宽度",        fr: "Largeur équivalente", es: "Ancho equivalente" },
  "pipeline.node_sed_fit":         { en: "SED Fit",            zh: "SED 拟合",        fr: "Ajustement SED",      es: "Ajuste SED" },
  "pipeline.node_cross_match":     { en: "Cross Match",        zh: "交叉匹配",        fr: "Correspondance croisée", es: "Correspondencia cruzada" },
  "pipeline.node_phot_calibrate":  { en: "Phot Calibrate",     zh: "测光校准",        fr: "Calibration photométrique", es: "Calibración fotométrica" },
  "pipeline.node_image_stack":     { en: "Image Stack",        zh: "图像叠加",        fr: "Empilement d'images", es: "Apilamiento de imágenes" },
  "pipeline.node_interactive_plot": { en: "Interactive Plot",   zh: "交互式绘图",     fr: "Graphique interactif", es: "Gráfico interactivo" },
  "pipeline.node_flux_calibrate":  { en: "Flux Calibrate",     zh: "流量校准",        fr: "Calibration de flux",  es: "Calibración de flujo" },
  "pipeline.node_telluric_correct": { en: "Telluric Correct",  zh: "大气吸收校正",    fr: "Correction tellurique", es: "Corrección telúrica" },
  "pipeline.node_spectra_stack":   { en: "Spectra Stack",      zh: "光谱叠加",        fr: "Empilement de spectres", es: "Apilamiento de espectros" },
  "pipeline.node_photoz_pro":      { en: "Photo-z (Pro)",      zh: "光度红移（专业）", fr: "Photo-z (Pro)",        es: "Photo-z (Pro)" },
  "pipeline.node_bayesian_fit":    { en: "Bayesian Fit",       zh: "贝叶斯拟合",      fr: "Ajustement bayésien",  es: "Ajuste bayesiano" },
  "pipeline.node_transit_fit":     { en: "Transit Fit",        zh: "凌星拟合",        fr: "Ajustement de transit", es: "Ajuste de tránsito" },
  "pipeline.node_gp_detrend":      { en: "GP Detrend",         zh: "高斯过程去趋势",  fr: "Détendance GP",        es: "Eliminación de tendencia GP" },
  "pipeline.node_reproject":       { en: "Reproject",          zh: "重投影",          fr: "Reprojection",         es: "Reproyección" },
  "pipeline.node_mosaic":          { en: "Mosaic",             zh: "拼接",            fr: "Mosaïque",             es: "Mosaico" },
  "pipeline.node_psf_match":       { en: "PSF Match",          zh: "PSF 匹配",        fr: "Correspondance PSF",   es: "Correspondencia PSF" },
  "pipeline.node_deblend":         { en: "Deblend",            zh: "源分离",          fr: "Déconvolution",        es: "Separación de fuentes" },
  "viz.spectrum_viewer":           { en: "Spectrum Viewer",    zh: "光谱查看器",      fr: "Visionneuse de spectre", es: "Visor de espectro" },
  "viz.light_curve_viewer":        { en: "Light Curve Viewer", zh: "光变曲线查看器",  fr: "Visionneuse de courbe de lumière", es: "Visor de curva de luz" },
  "viz.mcmc_diagnostics":          { en: "MCMC Diagnostics",   zh: "MCMC 诊断",       fr: "Diagnostics MCMC",     es: "Diagnósticos MCMC" },
  "viz.image_cutout":              { en: "Image Cutout",       zh: "图像裁切",        fr: "Découpe d'image",      es: "Recorte de imagen" },
  "viz.provenance":                { en: "Data Lineage",       zh: "数据溯源",        fr: "Lignée des données",   es: "Linaje de datos" },
  "viz.loading":                   { en: "Loading viewer...",  zh: "加载查看器...",    fr: "Chargement...",        es: "Cargando visor..." },
  "viz.no_image_data":             { en: "No image data",      zh: "无图像数据",      fr: "Aucune donnée d'image", es: "Sin datos de imagen" },
  "viz.no_provenance":             { en: "No provenance data", zh: "无溯源数据",      fr: "Aucune donnée de provenance", es: "Sin datos de procedencia" },
  "pipeline.nodes_title":          { en: "Nodes",              zh: "节点",            fr: "Nœuds",               es: "Nodos" },

  // ADQL Templates
  "adql.title":                { en: "ADQL Query",         zh: "ADQL 查询",       fr: "Requête ADQL",         es: "Consulta ADQL" },
  "adql.run_query":            { en: "Run Query",          zh: "执行查询",        fr: "Exécuter la requête",  es: "Ejecutar consulta" },
  "adql.running":              { en: "Running\u2026",      zh: "执行中\u2026",    fr: "Exécution\u2026",      es: "Ejecutando\u2026" },
  "adql.download_csv":         { en: "Download CSV",       zh: "下载 CSV",        fr: "Télécharger CSV",      es: "Descargar CSV" },
  "adql.rows":                 { en: "rows",               zh: "行",              fr: "lignes",               es: "filas" },
  "adql.columns":              { en: "columns",            zh: "列",              fr: "colonnes",             es: "columnas" },
  "adql.rows_from":            { en: "rows from",          zh: "行来自",          fr: "lignes de",            es: "filas de" },
  "adql.jupyter_notebook":     { en: "Jupyter Notebook",   zh: "Jupyter 笔记本",  fr: "Notebook Jupyter",     es: "Notebook Jupyter" },
  "adql.send_to_ai":           { en: "Send to AI",         zh: "发送到 AI",       fr: "Envoyer à l'IA",       es: "Enviar a IA" },
  "adql.visualize":            { en: "Visualize",          zh: "可视化",          fr: "Visualiser",           es: "Visualizar" },
  "adql.hide_chart":           { en: "Hide Chart",         zh: "隐藏图表",        fr: "Masquer le graphique", es: "Ocultar gráfico" },
  "adql.prev":                 { en: "Prev",               zh: "上一页",          fr: "Préc",                 es: "Ant" },
  "adql.recent":               { en: "Recent:",            zh: "最近：",          fr: "Récent :",             es: "Reciente:" },
  "adql.query_failed":         { en: "Query failed",       zh: "查询失败",        fr: "Échec de la requête",  es: "Error en la consulta" },
  "adql.generating_notebook":  { en: "Generating notebook\u2026", zh: "生成笔记本\u2026", fr: "Génération du notebook\u2026", es: "Generando notebook\u2026" },
  "adql.notebook_exported":    { en: "Notebook exported successfully", zh: "笔记本导出成功", fr: "Notebook exporté avec succès", es: "Notebook exportado exitosamente" },
  "adql.notebook_export_failed": { en: "Notebook export failed", zh: "笔记本导出失败", fr: "Échec de l'exportation du notebook", es: "Error al exportar el notebook" },
  "adql.placeholder":          { en: "Enter ADQL query...", zh: "输入 ADQL 查询...", fr: "Entrez une requête ADQL...", es: "Ingrese consulta ADQL..." },
  "adql.template_photometry":  { en: "Photometry",         zh: "测光",            fr: "Photométrie",          es: "Fotometría" },
  "adql.template_stellar_params": { en: "Stellar params",  zh: "恒星参数",        fr: "Paramètres stellaires", es: "Parámetros estelares" },
  "adql.template_radial_velocity": { en: "Radial velocity", zh: "径向速度",       fr: "Vitesse radiale",      es: "Velocidad radial" },
  "adql.template_nearby":      { en: "Nearby (plx>50)",    zh: "邻近 (plx>50)",   fr: "Proches (plx>50)",     es: "Cercanos (plx>50)" },
  "adql.template_z4_galaxies": { en: "z>4 galaxies",       zh: "z>4 星系",        fr: "Galaxies z>4",         es: "Galaxias z>4" },
  "adql.template_qsos":        { en: "QSOs",               zh: "类星体",          fr: "QSOs",                 es: "QSOs" },
  "adql.template_seyfert":     { en: "Seyfert galaxies",   zh: "赛弗特星系",      fr: "Galaxies de Seyfert",  es: "Galaxias Seyfert" },
  "adql.template_2mass":       { en: "2MASS catalog",      zh: "2MASS 星表",      fr: "Catalogue 2MASS",      es: "Catálogo 2MASS" },
  "adql.template_sdss_photo":  { en: "SDSS DR16 photo",    zh: "SDSS DR16 测光",  fr: "Photo SDSS DR16",      es: "Foto SDSS DR16" },
  "adql.template_jwst":        { en: "JWST observations",  zh: "JWST 观测",       fr: "Observations JWST",    es: "Observaciones JWST" },
  "adql.template_by_target":   { en: "By target",          zh: "按目标",          fr: "Par cible",            es: "Por objetivo" },

  // API Client errors
  "error.request_timed_out":     { en: "request timed out", zh: "请求超时", fr: "la requête a expiré", es: "la solicitud ha expirado" },
  "error.mast_jwst_failed":     { en: "MAST/JWST search failed before the server returned per-source results. Try again, reduce the search radius, or add SIMBAD to resolve coordinates first.", zh: "MAST/JWST 搜索在服务器返回各源结果之前失败。请重试、缩小搜索半径，或先添加 SIMBAD 解析坐标。", fr: "La recherche MAST/JWST a échoué avant le retour des résultats. Réessayez, réduisez le rayon ou ajoutez SIMBAD.", es: "La búsqueda MAST/JWST falló antes de obtener resultados. Reintente, reduzca el radio o añada SIMBAD." },
  "error.search_failed":        { en: "Search failed before the server returned per-source results. Try again, reduce the search radius, or add SIMBAD / coordinates for name resolution.", zh: "搜索在服务器返回各源结果之前失败。请重试、缩小搜索半径，或添加 SIMBAD / 坐标进行名称解析。", fr: "La recherche a échoué avant le retour des résultats. Réessayez, réduisez le rayon ou ajoutez SIMBAD / coordonnées.", es: "La búsqueda falló antes de obtener resultados. Reintente, reduzca el radio o añada SIMBAD / coordenadas." },
  "error.streaming_unsupported": { en: "Streaming not supported by browser", zh: "浏览器不支持流式传输", fr: "Le streaming n'est pas pris en charge par le navigateur", es: "El navegador no admite transmisión en tiempo real" },
  "error.ai_connection_interrupted": { en: "Connection to AI provider was interrupted. This may be a timeout — try a shorter prompt.", zh: "与 AI 提供商的连接中断。可能是超时——请尝试更简短的提示。", fr: "La connexion au fournisseur IA a été interrompue. Essayez un prompt plus court.", es: "La conexión con el proveedor de IA se interrumpió. Intente un prompt más corto." },
  "error.backend_unreachable":   { en: "Could not reach the backend server. Check whether the API service is up.", zh: "无法连接后端服务器。请检查 API 服务是否正在运行。", fr: "Impossible de joindre le serveur backend. Vérifiez que le service API fonctionne.", es: "No se puede conectar al servidor backend. Verifique si el servicio API está activo." },
  "error.alert_timed_out":       { en: "Alert request timed out. The transient service may be slow; try again in a moment.", zh: "警报请求超时。瞬变事件服务可能较慢，请稍后重试。", fr: "La requête d'alerte a expiré. Le service transitoire peut être lent.", es: "La solicitud de alertas ha expirado. El servicio de transitorios puede ser lento." },
  "error.alert_bad_response":    { en: "The backend did not return a valid alerts response. Check the alerts API route and server logs.", zh: "后端未返回有效的警报响应。请检查警报 API 路由和服务器日志。", fr: "Le backend n'a pas renvoyé de réponse d'alertes valide. Vérifiez la route API.", es: "El backend no devolvió una respuesta de alertas válida. Verifique la ruta API." },
  "error.alert_backend_unreachable": { en: "Could not reach the backend server while loading alerts. Check whether the API service is up and the frontend API URL is correct.", zh: "加载警报时无法连接后端服务器。请检查 API 服务是否运行以及前端 API URL 是否正确。", fr: "Impossible de joindre le backend lors du chargement des alertes. Vérifiez le service API et l'URL.", es: "No se pudo conectar al backend al cargar alertas. Verifique el servicio API y la URL del frontend." },

  // Data Browser actions
  "data.batch_mode":       { en: "Batch Mode",         zh: "批量模式",        fr: "Mode lot",             es: "Modo lote" },
  "data.save_search":      { en: "Save Search",        zh: "保存搜索",        fr: "Sauvegarder la recherche", es: "Guardar búsqueda" },
  "data.saved_searches":   { en: "Saved Searches...",   zh: "已保存的搜索...", fr: "Recherches sauvegardées...", es: "Búsquedas guardadas..." },
  "data.quick_plot":       { en: "Quick Plot",          zh: "快速绘图",        fr: "Graphique rapide",     es: "Gráfico rápido" },
  "data.dossier":          { en: "Dossier",             zh: "档案",            fr: "Dossier",              es: "Expediente" },
  "data.cross_match":      { en: "Cross-match...",      zh: "交叉匹配...",     fr: "Correspondance croisée...", es: "Correspondencia cruzada..." },
  "data.cross_match_title": { en: "Cross-match selected objects", zh: "交叉匹配选中的天体", fr: "Croiser les objets sélectionnés", es: "Cruzar los objetos seleccionados" },
  "data.run_cross_match":  { en: "Run Cross-match",    zh: "执行交叉匹配",    fr: "Exécuter la correspondance", es: "Ejecutar correspondencia" },
  "data.matching":         { en: "Matching...",         zh: "匹配中...",       fr: "En cours...",          es: "Buscando..." },
  "data.retry":            { en: "Retry",               zh: "重试",            fr: "Réessayer",            es: "Reintentar" },
  "data.retrying":         { en: "Retrying...",         zh: "重试中...",       fr: "Nouvelle tentative...", es: "Reintentando..." },
  "data.export_notebook":  { en: "Export Notebook",     zh: "导出笔记本",      fr: "Exporter le notebook", es: "Exportar notebook" },
  "data.share_to_friend":  { en: "Share to Friend",    zh: "分享给好友",      fr: "Partager avec un ami", es: "Compartir con amigo" },
  "data.share_with_team":  { en: "Share with Team",    zh: "分享给团队",      fr: "Partager avec l'équipe", es: "Compartir con equipo" },
  "data.sharing":          { en: "Sharing...",          zh: "分享中...",       fr: "Partage en cours...",  es: "Compartiendo..." },
  "data.share":            { en: "Share",               zh: "分享",            fr: "Partager",             es: "Compartir" },
  "data.share_objects":    { en: "Share objects to a friend", zh: "将天体分享给好友", fr: "Partager des objets avec un ami", es: "Compartir objetos con un amigo" },
  "data.select_all":       { en: "Select All",          zh: "全选",            fr: "Tout sélectionner",    es: "Seleccionar todo" },
  "data.deselect_all":     { en: "Deselect All",        zh: "取消全选",        fr: "Tout désélectionner",  es: "Deseleccionar todo" },
  "data.send_to_pipeline": { en: "Send to Pipeline",   zh: "发送到流水线",    fr: "Envoyer au pipeline",  es: "Enviar al pipeline" },
  "data.copy_link":        { en: "Copy Link",           zh: "复制链接",        fr: "Copier le lien",       es: "Copiar enlace" },
  "data.selected":         { en: "selected",            zh: "已选择",          fr: "sélectionné(s)",       es: "seleccionado(s)" },

  // Pipeline templates
  "pipeline.quick_templates":  { en: "Quick Templates",      zh: "快速模板",          fr: "Modèles rapides",      es: "Plantillas rápidas" },
  "pipeline.template_spectrum": { en: "Spectrum Analysis",   zh: "光谱分析",          fr: "Analyse spectrale",    es: "Análisis espectral" },
  "pipeline.template_ccd":    { en: "CCD Photometry",       zh: "CCD 测光",          fr: "Photométrie CCD",      es: "Fotometría CCD" },
  "pipeline.template_transient": { en: "Transient Triage",  zh: "瞬变事件分类",      fr: "Triage de transitoires", es: "Triaje de transitorios" },
  "pipeline.template_photoz": { en: "Photo-z Estimation",   zh: "光度红移估计",      fr: "Estimation photo-z",   es: "Estimación photo-z" },
  "pipeline.template_transit": { en: "Transit Search",      zh: "凌星搜索",          fr: "Recherche de transits", es: "Búsqueda de tránsitos" },
  "pipeline.template_open_cluster": { en: "Open Cluster HR + Isochrone Age", zh: "疏散星团 HR 图 + 等龄线测年", fr: "Amas ouvert HR + isochrone", es: "Cúmulo abierto HR + isócrona" },
  "pipeline.pub_package":     { en: "Publication Package",  zh: "发布包",            fr: "Package de publication", es: "Paquete de publicación" },

  // Chat next steps
  "chat.next_steps":       { en: "Next Steps",          zh: "下一步",          fr: "Prochaines étapes",    es: "Próximos pasos" },
  "chat.generate_paper":   { en: "Generate Paper",      zh: "生成论文",        fr: "Générer un article",   es: "Generar artículo" },
  "chat.export_notebook":  { en: "Export Notebook",      zh: "导出笔记本",     fr: "Exporter le notebook", es: "Exportar notebook" },
  "chat.sensitivity":      { en: "Sensitivity Analysis", zh: "敏感性分析",     fr: "Analyse de sensibilité", es: "Análisis de sensibilidad" },

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

  // ── Journal edition: Home page ─────────────────────────────────────────
  "home.eyebrow":  { en: "An open platform · peer-review ready by default", zh: "开放平台 · 默认符合同行评审标准", fr: "Une plateforme ouverte · prête pour l'évaluation par les pairs", es: "Una plataforma abierta · lista para revisión por pares" },
  "home.title":    { en: "An AI co-author for observational astronomy.", zh: "面向观测天文学的 AI 科研助手。", fr: "Un co-auteur IA pour l'astronomie observationnelle.", es: "Un co-autor IA para la astronomía observacional." },
  "home.subtitle": { en: "A research environment that refuses to fabricate.", zh: "拒绝编造的研究环境。", fr: "Un environnement de recherche qui refuse de fabriquer.", es: "Un entorno de investigación que se niega a fabricar." },
  "home.lead":     { en: "Chat your way through 23 archive connectors, 35 pipeline nodes and 52 scientific tools. Ask for an HR diagram, a Bayesian fit, or a LaTeX draft — the assistant calls real archive APIs, records every seed and run id, and every number it cites traces back to the tool that produced it.",
                     zh: "通过一次对话调动 23 个档案馆连接器、35 个流水线节点和 52 个科研工具。让它绘制 HR 图、跑贝叶斯拟合或生成 LaTeX 初稿 —— 助手调用的是真实的档案 API，每一个种子、每一个 run id 都记录在案，引用的每一个数字都能追溯到生成它的那次工具调用。",
                     fr: "Conversez avec 23 connecteurs d'archives, 35 nœuds de pipeline et 52 outils scientifiques. Demandez un diagramme HR, un ajustement bayésien ou une ébauche LaTeX — l'assistant appelle les vraies API d'archives, enregistre chaque graine et chaque run id, et chaque chiffre qu'il cite remonte jusqu'à l'outil qui l'a produit.",
                     es: "Conversa con 23 conectores de archivos, 35 nodos de pipeline y 52 herramientas científicas. Pídele un diagrama HR, un ajuste bayesiano o un borrador LaTeX — el asistente llama a las APIs de archivo reales, registra cada semilla y cada run id, y cada número que cita se rastrea hasta la herramienta que lo produjo." },
  "home.cta1":     { en: "Begin a session",             zh: "开始会话",           fr: "Démarrer une session",           es: "Iniciar una sesión" },
  "home.cta2":     { en: "Read the architecture brief →", zh: "阅读架构简报 →",    fr: "Lire la note d'architecture →",  es: "Leer el resumen de arquitectura →" },
  "home.stat.archives":    { en: "Archive connectors", zh: "档案馆连接器",  fr: "Connecteurs d'archives",  es: "Conectores de archivos" },
  "home.stat.nodes":       { en: "Pipeline nodes",     zh: "流水线节点",    fr: "Nœuds de pipeline",       es: "Nodos de pipeline" },
  "home.stat.tools":       { en: "AI tools",           zh: "AI 工具",       fr: "Outils IA",               es: "Herramientas IA" },
  "home.stat.fabricated":  { en: "Fabricated values",  zh: "编造值",        fr: "Valeurs fabriquées",      es: "Valores fabricados" },
  "home.stat.tests":       { en: "Passing tests",      zh: "通过测试",      fr: "Tests réussis",           es: "Pruebas aprobadas" },
  "home.toc.head":         { en: "In this issue",      zh: "本期要目",      fr: "Dans ce numéro",          es: "En este número" },
  "home.cat.method":         { en: "Method",         zh: "方法",        fr: "Méthode",         es: "Método" },
  "home.cat.instrument":     { en: "Instrument",     zh: "仪器",        fr: "Instrument",      es: "Instrumento" },
  "home.cat.statistics":     { en: "Statistics",     zh: "统计",        fr: "Statistiques",    es: "Estadística" },
  "home.cat.pipeline":       { en: "Pipeline",       zh: "流水线",      fr: "Pipeline",        es: "Pipeline" },
  "home.cat.reproducibility":{ en: "Reproducibility",zh: "可复现性",    fr: "Reproductibilité",es: "Reproducibilidad" },
  "home.cat.community":      { en: "Community",      zh: "社群",        fr: "Communauté",      es: "Comunidad" },
  "home.editorial.head":     { en: "Editorial principles", zh: "编辑原则", fr: "Principes éditoriaux", es: "Principios editoriales" },
  "home.ed.1":     { en: "Every number must be citable.",                   zh: "每一个数字都必须可引用。",            fr: "Chaque chiffre doit être citable.",              es: "Cada número debe ser citable." },
  "home.ed.1.body":{ en: "The claim validator rejects replies containing values it cannot find in the tool chain.", zh: "零编造校验器会拒绝任何引用了工具链中不存在的数字的回复。", fr: "Le validateur rejette toute réponse contenant des valeurs absentes de la chaîne d'outils.", es: "El validador rechaza respuestas con valores que no aparecen en la cadena de herramientas." },
  "home.ed.2":     { en: "No silent truncation.",                           zh: "绝不静默截断。",                      fr: "Aucune troncature silencieuse.",                 es: "Sin truncamientos silenciosos." },
  "home.ed.2.body":{ en: "Results that would be trimmed carry \"truncated\": true with the original row count.",  zh: "任何被截断的结果都会带上 \"truncated\": true 并附上原始行数。",                           fr: "Les résultats tronqués portent \"truncated\": true avec le nombre de lignes d'origine.",         es: "Los resultados recortados llevan \"truncated\": true con el número original de filas." },
  "home.ed.3":     { en: "Instruments name themselves.",                    zh: "仪器自报家门。",                      fr: "Les instruments se nomment eux-mêmes.",          es: "Los instrumentos se nombran a sí mismos." },
  "home.ed.3.body":{ en: "Gaia DR3, SDSS DR18, PARSEC 3.9 — the archive version travels with every datum.",    zh: "Gaia DR3、SDSS DR18、PARSEC 3.9 —— 每一条数据都带着它来自的档案版本。",                           fr: "Gaia DR3, SDSS DR18, PARSEC 3.9 — la version de l'archive voyage avec chaque donnée.",    es: "Gaia DR3, SDSS DR18, PARSEC 3.9 — la versión del archivo viaja con cada dato." },
  "home.ed.4":     { en: "Determinism is recorded, not assumed.",           zh: "确定性是被记录下来的，不是被假设的。",  fr: "Le déterminisme est enregistré, pas supposé.",   es: "El determinismo se registra, no se supone." },
  "home.ed.4.body":{ en: "Seeds, library versions and lockfiles ship with the paper appendix.",                 zh: "种子值、依赖版本和 lockfile 会随论文附录一起交付。",                           fr: "Graines, versions de bibliothèques et lockfiles sont livrés dans l'annexe du papier.", es: "Semillas, versiones de librerías y lockfiles se entregan con el apéndice del artículo." },

  // ── Journal edition (Phase J1: masthead + footer) ─────────────────────
  "brand.sub":        { en: "Reproducible workflows for observational astronomy", zh: "可复现的观测天文学工作流",                    fr: "Flux de travail reproductibles pour l'astronomie observationnelle",               es: "Flujos de trabajo reproducibles para la astronomía observacional" },
  "issue.line1":      { en: "Volume 4 · Issue 18 · April 2026", zh: "第 4 卷 · 第 18 期 · 2026 年 4 月", fr: "Volume 4 · Numéro 18 · Avril 2026", es: "Volumen 4 · Número 18 · Abril de 2026" },
  "footer.sub":       { en: "Reproducible workflows for observational astronomy · Est. 2026", zh: "可复现的观测天文学工作流 · 自 2026 年创建", fr: "Flux de travail reproductibles pour l'astronomie observationnelle · Depuis 2026", es: "Flujos de trabajo reproducibles para la astronomía observacional · Desde 2026" },
  "footer.col.platform":  { en: "Platform",   zh: "平台",  fr: "Plateforme",  es: "Plataforma" },
  "footer.col.science":   { en: "Science",    zh: "科学",  fr: "Science",     es: "Ciencia" },
  "footer.col.community": { en: "Community",  zh: "社群",  fr: "Communauté",  es: "Comunidad" },
  "footer.link.docs":     { en: "Docs",       zh: "文档",  fr: "Docs",        es: "Docs" },

  // Comments section (公开评论区, Landing 底部)
  "comments.title":              { en: "Guest Comments",                                               zh: "访客留言",                                                  fr: "Commentaires des visiteurs",                                              es: "Comentarios de visitantes" },
  "comments.intro":              { en: "Leave a one-liner — usage feedback, suggestions, or bug reports. No login required; just pick a nickname. Comments may be removed by moderators.", zh: "欢迎任何访客留下一句话 — 使用体验、建议、bug 反馈都行。无需登录,填昵称即可。内容经管理员审核后可能被移除。", fr: "Laissez un mot — retour d'usage, suggestions ou bugs. Pas de compte requis ; choisissez un pseudo. Les commentaires peuvent être supprimés par les modérateurs.", es: "Deje un comentario — opiniones, sugerencias o reportes de errores. Sin registro; elija un apodo. Los comentarios pueden ser eliminados por moderadores." },
  "comments.form.name_label":    { en: "Nickname",      zh: "昵称",         fr: "Pseudo",          es: "Apodo" },
  "comments.form.content_label": { en: "Content",       zh: "内容",         fr: "Contenu",         es: "Contenido" },
  "comments.form.name_placeholder":    { en: "e.g. Alice",   zh: "例如 Alice", fr: "p. ex. Alice", es: "por ej. Alice" },
  "comments.form.content_placeholder": { en: "Write something...", zh: "写点什么...", fr: "Écrivez quelque chose...", es: "Escriba algo..." },
  "comments.form.submit":        { en: "Publish",       zh: "发布",         fr: "Publier",         es: "Publicar" },
  "comments.form.submitting":    { en: "Publishing...", zh: "提交中...",    fr: "Envoi...",        es: "Enviando..." },
  "comments.count":              { en: "{n} total",     zh: "共 {n} 条",    fr: "{n} au total",    es: "{n} en total" },
  "comments.admin_badge":        { en: "admin mode",    zh: "admin 模式",   fr: "mode admin",      es: "modo admin" },
  "comments.loading":            { en: "Loading...",    zh: "加载中...",    fr: "Chargement...",   es: "Cargando..." },
  "comments.load_failed":        { en: "Load failed: {err}", zh: "加载失败: {err}", fr: "Échec du chargement : {err}", es: "Error al cargar: {err}" },
  "comments.empty":              { en: "No comments yet — be the first.", zh: "还没有人留言 — 做第一个吧。", fr: "Pas encore de commentaires — soyez le premier.", es: "Aún no hay comentarios — sea el primero." },
  "comments.load_more":          { en: "Load more",     zh: "加载更多",     fr: "Charger plus",    es: "Cargar más" },
  "comments.loading_more":       { en: "Loading...",    zh: "加载中...",    fr: "Chargement...",   es: "Cargando..." },
  "comments.delete":             { en: "Delete",        zh: "删除",         fr: "Supprimer",       es: "Eliminar" },
  "comments.delete_title":       { en: "Admin soft-delete", zh: "管理员软删除", fr: "Suppression logique (admin)", es: "Eliminación lógica (admin)" },
  "comments.admin_badge_title":  { en: "astro_admin_secret is set in localStorage; list is deletable", zh: "检测到 localStorage 里有 astro_admin_secret, 列表可删除", fr: "astro_admin_secret présent dans localStorage ; suppression activée", es: "astro_admin_secret presente en localStorage; eliminación habilitada" },
  "comments.delete_confirm":     { en: "Delete this comment? (soft delete, recoverable from backend)", zh: "删除这条评论? (软删除, 可在后台恢复)", fr: "Supprimer ce commentaire ? (logique, réversible)", es: "¿Eliminar este comentario? (lógica, reversible)" },
  "comments.admin_invalid":      { en: "Admin key invalid or expired; please re-set localStorage.astro_admin_secret", zh: "管理员密钥无效或已失效, 请检查 localStorage.astro_admin_secret", fr: "Clé admin invalide ou expirée ; réinitialisez localStorage.astro_admin_secret", es: "Clave admin inválida o vencida; restablezca localStorage.astro_admin_secret" },
  "comments.delete_failed":      { en: "Delete failed: {err}", zh: "删除失败: {err}", fr: "Échec de la suppression : {err}", es: "Error al eliminar: {err}" },
  "comments.time.just_now":      { en: "just now",      zh: "刚刚",         fr: "à l'instant",     es: "justo ahora" },
  "comments.time.minutes_ago":   { en: "{n} min ago",   zh: "{n} 分钟前",   fr: "il y a {n} min",  es: "hace {n} min" },
  "comments.time.hours_ago":     { en: "{n} h ago",     zh: "{n} 小时前",   fr: "il y a {n} h",    es: "hace {n} h" },
  "comments.time.days_ago":      { en: "{n} d ago",     zh: "{n} 天前",     fr: "il y a {n} j",    es: "hace {n} d" },
  "comments.err.name_length":    { en: "Nickname must be 1 – 40 characters", zh: "昵称长度需为 1 – 40 字", fr: "Le pseudo doit faire 1 – 40 caractères", es: "El apodo debe tener 1 – 40 caracteres" },
  "comments.err.content_length": { en: "Content must be 2 – 500 characters", zh: "内容长度需为 2 – 500 字", fr: "Le contenu doit faire 2 – 500 caractères", es: "El contenido debe tener 2 – 500 caracteres" },
  "comments.err.rate_limit":     { en: "Too fast! Please wait a moment (backend limits 3 posts / minute / IP).", zh: "发布太快啦,请稍等一会儿再试 (后端每 IP 每分钟最多 3 条).", fr: "Trop rapide ! Patientez un instant (backend limite 3 messages / min / IP).", es: "¡Demasiado rápido! Espere un momento (backend limita 3 mensajes / min / IP)." },
  "comments.err.submit_failed":  { en: "Submit failed", zh: "提交失败",     fr: "Échec de l'envoi", es: "Error al enviar" },
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
