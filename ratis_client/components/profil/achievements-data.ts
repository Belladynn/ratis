// ratis_client/components/profil/achievements-data.ts
//
// V1 hardcoded achievements data — port of
// `Ratis_handoff/lib/ratis-achievements-data.jsx`.
//
// The achievements backend is hors-V1 (cf `chunk-6-followups.md` § 4 :
// `useAchievements` does not exist on V1). Until it lands, the V5 modal is
// fed by this static collection so the visual surface ships completed.
//
// Shape :
//   - `RARITIES`        10 rarity tiers (terracotta → diamond), each with
//                       label, color, glow rgba, metallic-frame gradient
//                       stops, holo flag, xpRange tuple.
//   - `CATEGORIES`      7 category metadata records (volume, savings,
//                       streak, social, exploration, seasonal, secret).
//   - `ACHIEVEMENTS`    a representative subset of 24 entries — the JSX
//                       source ships 80+ ; we keep the chunk 7 brief
//                       count of 24 (mixed states : 7 unlocked, 5
//                       in-progress, 12 locked) to match the
//                       `${unlocked} / ${total}` pill on the Profil
//                       Succès row (chunk 6 hardcode).
//
// Token derogation : hex literals come straight from the JSX iso source.
// They cover rarity/category palettes that don't exist in the design
// system tokens — would defeat the iso fidelity to remap them
// (cf `chunk-3-followups.md` § 10).

export type AchievementStatus = 'unlocked' | 'in_progress' | 'locked';

export type RarityKey =
  | 'terracotta'
  | 'bronze'
  | 'copper'
  | 'silver'
  | 'gold'
  | 'emerald'
  | 'sapphire'
  | 'ruby'
  | 'crystal'
  | 'diamond';

export type CategoryKey =
  | 'volume'
  | 'savings'
  | 'streak'
  | 'social'
  | 'exploration'
  | 'seasonal'
  | 'secret';

export type RarityDef = {
  /** Display label (FR). */
  label: string;
  /** Solid accent colour for borders / labels / progress. */
  color: string;
  /** Soft rgba glow for shadow + radial highlights. */
  glow: string;
  /**
   * Metallic frame gradient stops applied via `LinearGradient.colors`.
   * Keep as a tuple (≥2 entries) so RN's gradient API is happy.
   */
  metal: readonly [string, string, ...string[]];
  /** Holographic shine sweep (only emerald+ in JSX iso). */
  holo: boolean;
  /** XP reward range — kept for parity with the JSX even if unused in V1. */
  xpRange: readonly [number, number];
};

export type CategoryDef = {
  label: string;
  icon: string;
  color: string;
};

export type Achievement = {
  id: string;
  label: string;
  description: string;
  icon: string;
  rarity: RarityKey;
  category: CategoryKey;
  progress: number;
  target: number;
  status: AchievementStatus;
};

// ---------------------------------------------------------------------------
// Rarities — 10 tiers, du plus commun (terracotta) au plus rare (diamond).
// L'effet hologramme/reflet est réservé aux paliers ≥ émeraude (`holo: true`).
// ---------------------------------------------------------------------------

export const RARITIES: Readonly<Record<RarityKey, RarityDef>> = {
  terracotta: {
    label: 'Terre cuite',
    color: '#B25A3C',
    glow: 'rgba(178,90,60,0.30)',
    metal: ['#6B2E1A', '#B25A3C', '#6B2E1A'],
    holo: false,
    xpRange: [10, 25],
  },
  bronze: {
    label: 'Bronze',
    color: '#A87233',
    glow: 'rgba(168,114,51,0.35)',
    metal: ['#5C3A12', '#CD7F32', '#5C3A12'],
    holo: false,
    xpRange: [25, 50],
  },
  copper: {
    label: 'Cuivre',
    color: '#D27F4A',
    glow: 'rgba(210,127,74,0.40)',
    metal: ['#7A3F1A', '#E8915A', '#7A3F1A'],
    holo: false,
    xpRange: [50, 100],
  },
  silver: {
    label: 'Argent',
    color: '#C8CDD4',
    glow: 'rgba(200,205,212,0.45)',
    metal: ['#6B7280', '#E5E7EB', '#6B7280'],
    holo: false,
    xpRange: [100, 175],
  },
  gold: {
    label: 'Or',
    color: '#F2C744',
    glow: 'rgba(242,199,68,0.55)',
    metal: ['#92400E', '#FBBF24', '#92400E'],
    holo: false,
    xpRange: [175, 300],
  },
  emerald: {
    label: 'Émeraude',
    color: '#34D399',
    glow: 'rgba(52,211,153,0.60)',
    metal: ['#064E3B', '#34D399', '#064E3B'],
    holo: true,
    xpRange: [300, 500],
  },
  sapphire: {
    label: 'Saphir',
    color: '#3B82F6',
    glow: 'rgba(59,130,246,0.65)',
    metal: ['#1E3A8A', '#60A5FA', '#1E3A8A'],
    holo: true,
    xpRange: [500, 750],
  },
  ruby: {
    label: 'Rubis',
    color: '#EF4444',
    glow: 'rgba(239,68,68,0.70)',
    metal: ['#7F1D1D', '#FB7185', '#7F1D1D'],
    holo: true,
    xpRange: [750, 1100],
  },
  crystal: {
    label: 'Cristal',
    color: '#A5F3FC',
    glow: 'rgba(165,243,252,0.75)',
    metal: ['#0E7490', '#A5F3FC', '#0E7490'],
    holo: true,
    xpRange: [1100, 1600],
  },
  diamond: {
    label: 'Diamant',
    color: '#E0F2FE',
    glow: 'rgba(224,242,254,0.85)',
    metal: ['#1E293B', '#F8FAFC', '#818CF8', '#F8FAFC', '#1E293B'],
    holo: true,
    xpRange: [1600, 2500],
  },
};

// ---------------------------------------------------------------------------
// Categories — 7 metadata records.
// ---------------------------------------------------------------------------

export const CATEGORIES: Readonly<Record<CategoryKey, CategoryDef>> = {
  volume: { label: 'Scans', icon: '📷', color: '#FB923C' },
  savings: { label: 'Économies', icon: '💰', color: '#FBBF24' },
  streak: { label: 'Régularité', icon: '🔥', color: '#F87171' },
  social: { label: 'Social', icon: '👥', color: '#60A5FA' },
  exploration: { label: 'Exploration', icon: '🗺️', color: '#34D399' },
  seasonal: { label: 'Saisonniers', icon: '🌸', color: '#F472B6' },
  secret: { label: 'Secrets', icon: '❓', color: '#C084FC' },
};

// ---------------------------------------------------------------------------
// V1 achievements collection — 24 entries (7 unlocked + 5 in_progress + 12
// locked). Mirrors the chunk 6 Profil row hardcode "7 / 24 débloqués".
//
// @deprecated since PR 8/8 (Achievements V1 frontend). Live data now flows
// through `useAchievements()` + `flattenAchievementsList()` (see
// `components/profil/achievements-adapter.ts`). This constant is retained
// as the safety-net default for `<AchievementsModal />` so storybook /
// stale screen mounts still render something sensible while the live query
// is in flight ; production callers (profil tab) MUST pass
// `achievements={liveAchievements}` explicitly.
// ---------------------------------------------------------------------------

export const ACHIEVEMENTS: readonly Achievement[] = [
  // ── 7 unlocked ─────────────────────────────────────────────────────
  {
    id: 'v_first',
    label: 'Premier scan',
    description: 'Scanner ton tout premier ticket',
    icon: '🎬',
    rarity: 'terracotta',
    category: 'volume',
    progress: 1,
    target: 1,
    status: 'unlocked',
  },
  {
    id: 'v_10',
    label: 'Habitué·e',
    description: 'Scanner 10 tickets',
    icon: '📋',
    rarity: 'bronze',
    category: 'volume',
    progress: 10,
    target: 10,
    status: 'unlocked',
  },
  {
    id: 's_1',
    label: 'Première éco',
    description: 'Économiser ton premier euro',
    icon: '🪙',
    rarity: 'terracotta',
    category: 'savings',
    progress: 1,
    target: 1,
    status: 'unlocked',
  },
  {
    id: 's_10',
    label: '10 balles',
    description: 'Économiser 10 €',
    icon: '💵',
    rarity: 'bronze',
    category: 'savings',
    progress: 10,
    target: 10,
    status: 'unlocked',
  },
  {
    id: 'r_3',
    label: 'Trio',
    description: 'Streak de 3 jours',
    icon: '🔥',
    rarity: 'bronze',
    category: 'streak',
    progress: 3,
    target: 3,
    status: 'unlocked',
  },
  {
    id: 'r_7',
    label: 'Semaine pleine',
    description: 'Streak de 7 jours',
    icon: '🔥',
    rarity: 'copper',
    category: 'streak',
    progress: 7,
    target: 7,
    status: 'unlocked',
  },
  {
    id: 'sea_winter',
    label: 'Hiver 25',
    description: 'Avoir participé au Pass Hiver 25',
    icon: '❄️',
    rarity: 'emerald',
    category: 'seasonal',
    progress: 1,
    target: 1,
    status: 'unlocked',
  },

  // ── 5 in_progress ──────────────────────────────────────────────────
  {
    id: 'v_50',
    label: 'Cinquantaine',
    description: 'Scanner 50 tickets',
    icon: '📑',
    rarity: 'copper',
    category: 'volume',
    progress: 47,
    target: 50,
    status: 'in_progress',
  },
  {
    id: 's_50',
    label: 'Demi-bil',
    description: 'Économiser 50 €',
    icon: '💴',
    rarity: 'copper',
    category: 'savings',
    progress: 47,
    target: 50,
    status: 'in_progress',
  },
  {
    id: 'r_14',
    label: 'Quinzaine',
    description: 'Streak de 14 jours',
    icon: '🔥',
    rarity: 'silver',
    category: 'streak',
    progress: 7,
    target: 14,
    status: 'in_progress',
  },
  {
    id: 'exp_brand_5',
    label: 'Curieux·se',
    description: 'Scanner dans 5 enseignes différentes',
    icon: '🛒',
    rarity: 'bronze',
    category: 'exploration',
    progress: 3,
    target: 5,
    status: 'in_progress',
  },
  {
    id: 's_day_20',
    label: 'Grosse journée',
    description: 'Économiser 20 € en une journée',
    icon: '🌟',
    rarity: 'emerald',
    category: 'savings',
    progress: 12,
    target: 20,
    status: 'in_progress',
  },

  // ── 12 locked ──────────────────────────────────────────────────────
  {
    id: 'v_500',
    label: 'Demi-millier',
    description: 'Scanner 500 tickets',
    icon: '📊',
    rarity: 'gold',
    category: 'volume',
    progress: 0,
    target: 500,
    status: 'locked',
  },
  {
    id: 'v_1000',
    label: 'Millier',
    description: 'Scanner 1000 tickets',
    icon: '🏆',
    rarity: 'crystal',
    category: 'volume',
    progress: 0,
    target: 1000,
    status: 'locked',
  },
  {
    id: 's_500',
    label: 'Demi-millier €',
    description: 'Économiser 500 €',
    icon: '💷',
    rarity: 'sapphire',
    category: 'savings',
    progress: 0,
    target: 500,
    status: 'locked',
  },
  {
    id: 'r_30',
    label: 'Mois sans rater',
    description: 'Streak de 30 jours',
    icon: '🔥',
    rarity: 'sapphire',
    category: 'streak',
    progress: 0,
    target: 30,
    status: 'locked',
  },
  {
    id: 'r_365',
    label: 'Une année',
    description: 'Streak de 365 jours',
    icon: '🌌',
    rarity: 'diamond',
    category: 'streak',
    progress: 0,
    target: 365,
    status: 'locked',
  },
  {
    id: 'soc_invite_1',
    label: 'Recruteur',
    description: 'Inviter 1 ami',
    icon: '🤝',
    rarity: 'bronze',
    category: 'social',
    progress: 0,
    target: 1,
    status: 'locked',
  },
  {
    id: 'soc_invite_10',
    label: 'Réseau',
    description: 'Inviter 10 amis',
    icon: '🌐',
    rarity: 'gold',
    category: 'social',
    progress: 0,
    target: 10,
    status: 'locked',
  },
  {
    id: 'exp_cat_15',
    label: 'Encyclopédiste',
    description: 'Scanner dans 15 catégories différentes',
    icon: '📚',
    rarity: 'gold',
    category: 'exploration',
    progress: 0,
    target: 15,
    status: 'locked',
  },
  {
    id: 'exp_unknown_10',
    label: 'Pionnier·e',
    description: 'Découvrir 10 produits jamais vus',
    icon: '🚀',
    rarity: 'emerald',
    category: 'exploration',
    progress: 0,
    target: 10,
    status: 'locked',
  },
  {
    id: 'sea_summer',
    label: 'Été 26',
    description: 'Participer au Pass Été 26',
    icon: '☀️',
    rarity: 'gold',
    category: 'seasonal',
    progress: 0,
    target: 1,
    status: 'locked',
  },
  {
    id: 'sec_konami',
    label: '???',
    description: 'Succès secret',
    icon: '❓',
    rarity: 'diamond',
    category: 'secret',
    progress: 0,
    target: 1,
    status: 'locked',
  },
  {
    id: 'sec_3am',
    label: '???',
    description: 'Succès secret',
    icon: '❓',
    rarity: 'gold',
    category: 'secret',
    progress: 0,
    target: 1,
    status: 'locked',
  },
];
