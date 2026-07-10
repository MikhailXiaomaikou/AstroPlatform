export interface GlossaryEntry {
  term: string;
  en: string;
  zh: string;
}

export const GLOSSARY: GlossaryEntry[] = [
  // ── Distance & Units ──
  {
    term: "Parsec",
    en: "A unit of distance equal to about 3.26 light-years, defined as the distance at which a star shows a parallax shift of one arcsecond.",
    zh: "秒差距，约等于 3.26 光年，定义为恒星视差为一角秒时对应的距离。",
  },
  {
    term: "Light-Year",
    en: "The distance light travels in one year, about 9.46 trillion kilometers. Used to express astronomical distances.",
    zh: "光年，光在一年内传播的距离，约 9.46 万亿公里，用于表示天文距离。",
  },
  {
    term: "Astronomical Unit / AU",
    en: "The average distance from the Earth to the Sun, about 150 million km. Used for distances within the solar system.",
    zh: "天文单位，地球到太阳的平均距离，约 1.5 亿公里，用于太阳系内距离。",
  },
  {
    term: "Megaparsec / Mpc",
    en: "One million parsecs. Used to measure distances between galaxies and galaxy clusters.",
    zh: "百万秒差距，用于星系和星系团之间的距离。",
  },

  // ── Coordinate & Motion ──
  {
    term: "Right Ascension",
    en: "The celestial equivalent of longitude, measured in hours (0-24h). Together with declination, it pinpoints an object on the sky.",
    zh: "赤经，天球坐标中类似于经度的量，以小时（0-24h）为单位，与赤纬一起确定天体位置。",
  },
  {
    term: "Declination",
    en: "The celestial equivalent of latitude, measured in degrees from -90 (south pole) to +90 (north pole).",
    zh: "赤纬，天球坐标中类似于纬度的量，以度为单位，范围 -90 到 +90。",
  },
  {
    term: "Proper Motion",
    en: "The apparent angular movement of a star across the sky per year, caused by its real motion through space relative to the Sun.",
    zh: "自行，恒星每年在天球上的视角位移，反映其相对于太阳的真实空间运动。",
  },
  {
    term: "Radial Velocity",
    en: "The speed at which an object moves toward or away from us along the line of sight, measured via Doppler shift.",
    zh: "径向速度，天体沿视线方向靠近或远离我们的速度，通过多普勒效应测量。",
  },
  {
    term: "Parallax",
    en: "The apparent shift in a star's position when viewed from different points in Earth's orbit. Closer stars show larger parallax.",
    zh: "视差，从地球轨道不同位置观测时恒星位置的视变化。距离越近视差越大。",
  },

  // ── Brightness ──
  {
    term: "Magnitude",
    en: "A logarithmic scale for brightness. Lower numbers mean brighter objects; each step of 1 magnitude is about 2.5x in brightness.",
    zh: "星等，亮度的对数标度。数值越小越亮，每差一个星等亮度相差约 2.5 倍。",
  },
  {
    term: "Apparent Magnitude",
    en: "How bright an object appears from Earth. The Sun is -26.7, the faintest naked-eye stars are about +6.",
    zh: "视星等，天体从地球上看的亮度。太阳为 -26.7，肉眼可见最暗恒星约 +6 等。",
  },
  {
    term: "Absolute Magnitude",
    en: "How bright an object would appear at a standard distance of 10 parsecs. Allows fair comparison of intrinsic brightness.",
    zh: "绝对星等，天体在 10 秒差距标准距离处的亮度，用于比较天体的固有亮度。",
  },
  {
    term: "Luminosity",
    en: "The total energy a star radiates per second, often expressed relative to the Sun's luminosity.",
    zh: "光度，恒星每秒辐射的总能量，通常以太阳光度为单位表示。",
  },

  // ── Spectral Properties ──
  {
    term: "Redshift",
    en: "The stretching of light to longer (redder) wavelengths, caused by an object moving away from us or the expansion of the universe.",
    zh: "红移，光波被拉伸到更长（更红）波长的现象，由天体远离我们或宇宙膨胀引起。",
  },
  {
    term: "Blueshift",
    en: "The compression of light to shorter (bluer) wavelengths, indicating an object is moving toward us.",
    zh: "蓝移，光波被压缩到更短（更蓝）波长的现象，表示天体正在向我们靠近。",
  },
  {
    term: "Spectral Type",
    en: "A classification of stars by their surface temperature (O, B, A, F, G, K, M from hottest to coolest). The Sun is a G2 star.",
    zh: "光谱型，按恒星表面温度分类（O、B、A、F、G、K、M 从高到低）。太阳是 G2 型。",
  },
  {
    term: "Effective Temperature",
    en: "The temperature of a perfect blackbody that would emit the same total energy as the star. The Sun's is about 5,778 K.",
    zh: "有效温度，与恒星辐射总能量相同的理想黑体的温度。太阳约为 5778 K。",
  },
  {
    term: "Surface Gravity",
    en: "The gravitational acceleration at a star's surface, usually expressed as log(g) in cm/s^2. Helps distinguish giants from dwarfs.",
    zh: "表面引力，恒星表面的重力加速度，通常用 log(g)（cm/s^2）表示，可区分巨星与矮星。",
  },
  {
    term: "Emission Line",
    en: "A bright line in a spectrum at a specific wavelength, produced when excited atoms release energy as photons.",
    zh: "发射线，光谱中特定波长处的亮线，由受激原子释放光子产生。",
  },
  {
    term: "Absorption Line",
    en: "A dark line in a spectrum where atoms in a cooler gas have absorbed light at a specific wavelength.",
    zh: "吸收线，光谱中特定波长处的暗线，由较冷气体中的原子吸收光产生。",
  },
  {
    term: "Equivalent Width",
    en: "A measure of how strong a spectral line is, expressed as the width of a rectangle with the same area as the line profile.",
    zh: "等值宽度，衡量谱线强度的量，用与谱线轮廓面积相等的矩形宽度来表示。",
  },
  {
    term: "Continuum",
    en: "The smooth, continuous part of a spectrum without discrete emission or absorption lines.",
    zh: "连续谱，光谱中平滑连续的部分，不包含离散的发射或吸收线。",
  },
  {
    term: "Spectral Energy Distribution / SED",
    en: "A plot of an object's brightness versus wavelength or frequency, revealing its temperature and composition.",
    zh: "光谱能量分布，天体亮度随波长或频率变化的曲线，揭示其温度和组成。",
  },

  // ── Stellar Types & Evolution ──
  {
    term: "Main Sequence",
    en: "The band on the HR diagram where stars spend most of their lives fusing hydrogen into helium. The Sun is a main-sequence star.",
    zh: "主序，赫罗图上恒星生命大部分时间所处的区域，此时恒星将氢聚变为氦。太阳是主序星。",
  },
  {
    term: "Red Giant",
    en: "An aging star that has exhausted core hydrogen and expanded to many times its original size, with a cool, reddish surface.",
    zh: "红巨星，耗尽核心氢后膨胀到原来数十倍大小的老年恒星，表面温度低呈红色。",
  },
  {
    term: "White Dwarf",
    en: "The dense, Earth-sized remnant of a low-mass star after it sheds its outer layers. No longer undergoing fusion.",
    zh: "白矮星，低质量恒星抛掉外层后留下的致密残骸，大小接近地球，不再发生核聚变。",
  },
  {
    term: "Neutron Star",
    en: "The ultra-dense remnant of a massive star's supernova, only about 20 km across but more massive than the Sun.",
    zh: "中子星，大质量恒星超新星爆发后的超致密残骸，直径仅约 20 公里但质量超过太阳。",
  },
  {
    term: "Black Hole",
    en: "A region of spacetime where gravity is so strong that nothing, not even light, can escape once past the event horizon.",
    zh: "黑洞，时空中引力极强的区域，任何物质和光一旦越过事件视界便无法逃脱。",
  },
  {
    term: "Supernova",
    en: "A catastrophic explosion of a massive star at the end of its life, briefly outshining an entire galaxy.",
    zh: "超新星，大质量恒星生命末期的灾变性爆发，短暂时间内亮度可超过整个星系。",
  },
  {
    term: "Nova",
    en: "A sudden brightening of a white dwarf caused by nuclear explosion of material accreted from a companion star.",
    zh: "新星，白矮星从伴星吸积物质并发生核爆炸导致的突然增亮现象。",
  },
  {
    term: "Variable Star",
    en: "A star whose brightness changes over time, either due to pulsation, eclipses, or eruptions.",
    zh: "变星，亮度随时间变化的恒星，原因可能是脉动、食或爆发。",
  },
  {
    term: "Cepheid",
    en: "A type of pulsating variable star whose period is directly related to its luminosity, making it a key distance indicator.",
    zh: "造父变星，一类脉动变星，其周期与光度直接相关，是重要的距离指示器。",
  },
  {
    term: "RR Lyrae",
    en: "Pulsating variable stars found in old stellar populations. Like Cepheids, they serve as standard candles for distance measurement.",
    zh: "天琴座 RR 型变星，存在于老年星族中的脉动变星，与造父变星类似可作为距离标准烛光。",
  },

  // ── Large-Scale Structures ──
  {
    term: "Galaxy",
    en: "A gravitationally bound system of billions of stars, gas, dust, and dark matter. The Milky Way is our home galaxy.",
    zh: "星系，由数十亿恒星、气体、尘埃和暗物质通过引力束缚的系统。银河系是我们的家园。",
  },
  {
    term: "Quasar",
    en: "An extremely luminous active galactic nucleus powered by a supermassive black hole accreting matter at a high rate.",
    zh: "类星体，由超大质量黑洞高速吸积物质驱动的极高光度活动星系核。",
  },
  {
    term: "AGN",
    en: "Active Galactic Nucleus — the bright central region of a galaxy powered by accretion onto a supermassive black hole.",
    zh: "活动星系核，由超大质量黑洞吸积驱动的星系明亮中心区域。",
  },
  {
    term: "Nebula",
    en: "A cloud of gas and dust in space. Some nebulae are star-forming regions; others are remnants of dying stars.",
    zh: "星云，太空中的气体和尘埃云。有些是恒星形成区域，有些是垂死恒星的残骸。",
  },
  {
    term: "Star Cluster",
    en: "A group of stars born together from the same molecular cloud. Can be open (loose) or globular (dense, spherical).",
    zh: "星团，由同一分子云诞生的恒星群。可以是疏散星团（松散）或球状星团（致密球形）。",
  },
  {
    term: "Globular Cluster",
    en: "A tightly packed, spherical collection of hundreds of thousands of old stars orbiting a galaxy's core.",
    zh: "球状星团，由数十万颗老年恒星紧密聚集成球形的星团，围绕星系核心运行。",
  },

  // ── Diagrams & Plots ──
  {
    term: "HR Diagram",
    en: "Hertzsprung-Russell diagram — a plot of stellar luminosity vs. temperature that reveals evolutionary stages of stars.",
    zh: "赫罗图，恒星光度与温度的关系图，揭示恒星的演化阶段。",
  },
  {
    term: "Color-Magnitude Diagram",
    en: "An observational version of the HR diagram using color index (e.g. B-V) instead of temperature and apparent/absolute magnitude.",
    zh: "颜色-星等图，赫罗图的观测版本，用色指数（如 B-V）代替温度，用视/绝对星等表示亮度。",
  },
  {
    term: "BPT Diagram",
    en: "A diagnostic plot using emission-line ratios to classify galaxies as star-forming, AGN (Seyfert), or LINER.",
    zh: "BPT 图，利用发射线比值将星系分类为恒星形成、AGN（赛弗特）或 LINER 的诊断图。",
  },

  // ── Observational Techniques ──
  {
    term: "Photometry",
    en: "Measuring an object's brightness through filters. Different filters (e.g. u, g, r, i, z) isolate specific wavelength ranges.",
    zh: "测光，通过滤光片测量天体亮度。不同滤光片（如 u、g、r、i、z）分离特定波长范围。",
  },
  {
    term: "Spectroscopy",
    en: "Splitting light into its component wavelengths to study composition, temperature, motion, and other physical properties.",
    zh: "光谱学，将光分解为各波长成分以研究天体的组成、温度、运动等物理性质。",
  },
  {
    term: "Astrometry",
    en: "Precisely measuring positions and motions of celestial objects. The Gaia mission is the current gold standard.",
    zh: "天体测量学，精确测量天体位置和运动的学科。盖亚（Gaia）卫星是当前最高标准。",
  },
  {
    term: "Interferometry",
    en: "Combining signals from multiple telescopes to achieve the angular resolution of a much larger telescope.",
    zh: "干涉测量，将多个望远镜的信号组合以获得更大望远镜的角分辨率。",
  },

  // ── Observational Effects ──
  {
    term: "Extinction",
    en: "The dimming of starlight by interstellar dust absorbing and scattering photons. Stronger at shorter wavelengths.",
    zh: "消光，星际尘埃吸收和散射光子导致星光变暗的现象。短波长处效应更强。",
  },
  {
    term: "Reddening",
    en: "The reddening of starlight caused by dust preferentially scattering blue light away, making stars appear redder.",
    zh: "红化，尘埃优先散射蓝光导致星光变红的现象，使恒星看起来比实际更红。",
  },
  {
    term: "E(B-V)",
    en: "Color excess — the difference between observed and intrinsic B-V color, quantifying the amount of reddening by dust.",
    zh: "色余，观测 B-V 色指数与固有值之差，量化尘埃红化程度。",
  },
  {
    term: "Angular Resolution",
    en: "The smallest angle between two objects that a telescope can distinguish. Smaller values mean sharper images.",
    zh: "角分辨率，望远镜能分辨两个天体的最小角距。数值越小图像越清晰。",
  },
  {
    term: "Seeing",
    en: "The blurring of astronomical images caused by turbulence in Earth's atmosphere. Typical good seeing is around 1 arcsecond.",
    zh: "视宁度，地球大气湍流导致天文图像模糊的程度。良好视宁度约 1 角秒。",
  },
  {
    term: "Airmass",
    en: "The amount of atmosphere light must pass through to reach the telescope. At zenith it is 1; at the horizon it is much larger.",
    zh: "大气质量，光到达望远镜需穿过的大气厚度。天顶处为 1，接近地平线时大幅增加。",
  },
  {
    term: "Signal-to-Noise Ratio / SNR",
    en: "The ratio of the desired signal to background noise. Higher SNR means cleaner, more reliable measurements.",
    zh: "信噪比，目标信号与背景噪声的比值。信噪比越高，测量越清晰可靠。",
  },

  // ── Chemical Composition ──
  {
    term: "Metallicity",
    en: "The abundance of elements heavier than helium in a star, often expressed as [Fe/H]. Higher metallicity = more heavy elements.",
    zh: "金属丰度，恒星中比氦重的元素含量，常用 [Fe/H] 表示。金属丰度越高表示重元素越多。",
  },
  {
    term: "[Fe/H]",
    en: "A logarithmic ratio of iron to hydrogen abundance compared to the Sun. [Fe/H] = 0 means solar composition.",
    zh: "[Fe/H]，铁与氢丰度比的对数值（相对于太阳）。[Fe/H] = 0 表示与太阳组成相同。",
  },

  // ── Cosmology ──
  {
    term: "Cosmological Redshift",
    en: "Redshift caused by the expansion of the universe itself stretching light waves as they travel across cosmic distances.",
    zh: "宇宙学红移，由宇宙膨胀本身拉伸光波引起的红移，发生在光穿越宇宙尺度距离时。",
  },
  {
    term: "Hubble Constant",
    en: "The rate of expansion of the universe, about 70 km/s/Mpc. Relates a galaxy's recession speed to its distance.",
    zh: "哈勃常数，宇宙膨胀速率，约 70 km/s/Mpc，将星系退行速度与距离联系起来。",
  },
  {
    term: "Dark Energy",
    en: "A mysterious force causing the expansion of the universe to accelerate. It makes up about 68% of the total energy.",
    zh: "暗能量，导致宇宙加速膨胀的神秘力量，约占宇宙总能量的 68%。",
  },
  {
    term: "Dark Matter",
    en: "Invisible matter that does not emit or absorb light but exerts gravitational effects. It makes up about 27% of the universe.",
    zh: "暗物质，不发射或吸收光但产生引力效应的不可见物质，约占宇宙的 27%。",
  },

  // ── Data Formats & Protocols ──
  {
    term: "FITS",
    en: "Flexible Image Transport System — the standard file format for astronomical images, spectra, and tables.",
    zh: "灵活图像传输系统，天文图像、光谱和表格的标准文件格式。",
  },
  {
    term: "ADQL",
    en: "Astronomical Data Query Language — an SQL-like language for querying astronomical databases through TAP services.",
    zh: "天文数据查询语言，类似 SQL 的语言，用于通过 TAP 服务查询天文数据库。",
  },
  {
    term: "TAP",
    en: "Table Access Protocol — a standard for querying astronomical catalog databases over the internet using ADQL.",
    zh: "表访问协议，通过互联网使用 ADQL 查询天文星表数据库的标准协议。",
  },
  {
    term: "VOTable",
    en: "An XML-based format for tabular astronomical data, widely used in Virtual Observatory services.",
    zh: "基于 XML 的天文表格数据格式，在虚拟天文台服务中广泛使用。",
  },

  // ── Data Operations ──
  {
    term: "Cross-match",
    en: "Finding the same astronomical object across different catalogs by matching sky positions within a tolerance radius.",
    zh: "交叉匹配，通过在容差半径内匹配天球位置来在不同星表中找到同一天体。",
  },
  {
    term: "Cone Search",
    en: "A query that returns all objects within a circle of a given radius around a sky position.",
    zh: "锥形搜索，返回以某天球位置为中心、给定半径内所有天体的查询方式。",
  },
  {
    term: "Catalog",
    en: "A systematic list of astronomical objects with measured properties like position, brightness, and redshift.",
    zh: "星表，记录天体位置、亮度、红移等测量属性的系统化列表。",
  },

  // ── Time-Series Analysis ──
  {
    term: "Lomb-Scargle",
    en: "A method for detecting periodic signals in unevenly sampled time-series data, widely used for variable star analysis.",
    zh: "Lomb-Scargle 周期图法，用于在非均匀采样时间序列数据中检测周期信号，广泛用于变星分析。",
  },
  {
    term: "Phase Folding",
    en: "Wrapping a light curve at a known period so that all cycles overlap, revealing the repeating pattern more clearly.",
    zh: "相位折叠，将光变曲线按已知周期折叠使所有周期重叠，更清晰地显示重复模式。",
  },
  {
    term: "Period",
    en: "The time it takes for a repeating event (like a variable star's brightness cycle) to complete one full cycle.",
    zh: "周期，一个重复事件（如变星亮度变化）完成一个完整循环所需的时间。",
  },
  {
    term: "Amplitude",
    en: "The size of the brightness variation in a variable star, measured as the difference between maximum and minimum magnitude.",
    zh: "振幅，变星亮度变化的大小，以最大与最小星等之差来衡量。",
  },

  // ── Line Profiles ──
  {
    term: "Voigt Profile",
    en: "A spectral line shape that combines Gaussian (thermal) and Lorentzian (natural/pressure) broadening.",
    zh: "Voigt 轮廓，结合高斯（热致）与洛伦兹（自然/压力致）展宽的谱线形状。",
  },
  {
    term: "Gaussian",
    en: "A bell-shaped curve used to model spectral lines broadened primarily by thermal (random) motions of atoms.",
    zh: "高斯分布，钟形曲线，用于模拟主要由原子热（随机）运动展宽的谱线。",
  },
  {
    term: "Lorentzian",
    en: "A line profile with broader wings than a Gaussian, arising from the natural lifetime of atomic energy levels.",
    zh: "洛伦兹分布，比高斯分布具有更宽翼部的线型，源于原子能级的自然寿命。",
  },

  // ── Galaxy Classification ──
  {
    term: "Seyfert Galaxy",
    en: "A spiral galaxy with a very bright, compact nucleus powered by an AGN. Classified into Type 1 (broad lines) and Type 2 (narrow lines).",
    zh: "赛弗特星系，具有由 AGN 驱动的非常明亮致密核心的旋涡星系，分为 1 型（宽线）和 2 型（窄线）。",
  },
  {
    term: "LINER",
    en: "Low-Ionization Nuclear Emission-line Region — a type of galactic nucleus with weak AGN activity or shock-heated gas.",
    zh: "低电离核发射线区，一类具有弱 AGN 活动或激波加热气体的星系核。",
  },
  {
    term: "Starburst",
    en: "A galaxy or region undergoing an exceptionally high rate of new star formation, often triggered by galaxy interactions.",
    zh: "星暴，正在以异常高速率形成新恒星的星系或区域，通常由星系相互作用触发。",
  },
];
