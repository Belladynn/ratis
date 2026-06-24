// ratis_client/data/achievements.ts
//
// Achievements data model + V1 fixture (strict iso V5).
//
// Reference JSX : `Ratis_handoff/lib/ratis-achievements-data.jsx`. We keep
// the same RARITIES + CATEGORIES tokens (TypeScript-flavoured) and a smaller
// fixture for V1 (24 entries to match the "Débloqués X/24" header on the V5
// screenshot). Backend wiring is hors-V1 — this file is the single source
// of truth until the API lands.

export type AchievementRarityKey =
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

export type AchievementCategoryKey =
  | 'volume'
  | 'savings'
  | 'streak'
  | 'social'
  | 'exploration'
  | 'seasonal'
  | 'secret';

export type AchievementStatus = 'unlocked' | 'in_progress' | 'locked';

export interface AchievementRarity {
  key: AchievementRarityKey;
  label: string;
  /** Primary tint (text + accents). */
  color: string;
  /** Outer glow / shadow tint. */
  glow: string;
  /** Frame gradient — list of colours interpolated linearly 135deg. */
  metalGradient: readonly string[];
  /** True when the holographic shine sweep applies (palier ≥ émeraude). */
  holo: boolean;
}

export interface AchievementCategory {
  key: AchievementCategoryKey;
  label: string;
  /** Single emoji used in the chip + ribbon corner. */
  icon: string;
  color: string;
}

export interface Achievement {
  id: string;
  label: string;
  description: string;
  /** Single emoji rendered in the card icon area. */
  icon: string;
  rarity: AchievementRarityKey;
  category: AchievementCategoryKey;
  progress: number;
  target: number;
  status: AchievementStatus;
}

// ── Rarities ─────────────────────────────────────────────────────────────

export const RARITIES: Record<AchievementRarityKey, AchievementRarity> = {
  terracotta: {
    key: 'terracotta',
    label: 'Terre cuite',
    color: '#B25A3C',
    glow: 'rgba(178,90,60,0.30)',
    metalGradient: ['#6B2E1A', '#B25A3C', '#6B2E1A'],
    holo: false,
  },
  bronze: {
    key: 'bronze',
    label: 'Bronze',
    color: '#A87233',
    glow: 'rgba(168,114,51,0.35)',
    metalGradient: ['#5C3A12', '#CD7F32', '#5C3A12'],
    holo: false,
  },
  copper: {
    key: 'copper',
    label: 'Cuivre',
    color: '#D27F4A',
    glow: 'rgba(210,127,74,0.40)',
    metalGradient: ['#7A3F1A', '#E8915A', '#7A3F1A'],
    holo: false,
  },
  silver: {
    key: 'silver',
    label: 'Argent',
    color: '#C8CDD4',
    glow: 'rgba(200,205,212,0.45)',
    metalGradient: ['#6B7280', '#E5E7EB', '#6B7280'],
    holo: false,
  },
  gold: {
    key: 'gold',
    label: 'Or',
    color: '#F2C744',
    glow: 'rgba(242,199,68,0.55)',
    metalGradient: ['#92400E', '#FBBF24', '#92400E'],
    holo: false,
  },
  emerald: {
    key: 'emerald',
    label: 'Émeraude',
    color: '#34D399',
    glow: 'rgba(52,211,153,0.60)',
    metalGradient: ['#064E3B', '#34D399', '#064E3B'],
    holo: true,
  },
  sapphire: {
    key: 'sapphire',
    label: 'Saphir',
    color: '#3B82F6',
    glow: 'rgba(59,130,246,0.65)',
    metalGradient: ['#1E3A8A', '#60A5FA', '#1E3A8A'],
    holo: true,
  },
  ruby: {
    key: 'ruby',
    label: 'Rubis',
    color: '#EF4444',
    glow: 'rgba(239,68,68,0.70)',
    metalGradient: ['#7F1D1D', '#FB7185', '#7F1D1D'],
    holo: true,
  },
  crystal: {
    key: 'crystal',
    label: 'Cristal',
    color: '#A5F3FC',
    glow: 'rgba(165,243,252,0.75)',
    metalGradient: ['#0E7490', '#A5F3FC', '#0E7490'],
    holo: true,
  },
  diamond: {
    key: 'diamond',
    label: 'Diamant',
    color: '#E0F2FE',
    glow: 'rgba(224,242,254,0.85)',
    metalGradient: ['#1E293B', '#F8FAFC', '#818CF8', '#F8FAFC', '#1E293B'],
    holo: true,
  },
};

// ── Categories ───────────────────────────────────────────────────────────

export const CATEGORIES: Record<AchievementCategoryKey, AchievementCategory> = {
  volume: { key: 'volume', label: 'Scans', icon: '📷', color: '#FB923C' },
  savings: { key: 'savings', label: 'Économies', icon: '💰', color: '#FBBF24' },
  streak: { key: 'streak', label: 'Régularité', icon: '🔥', color: '#F87171' },
  social: { key: 'social', label: 'Social', icon: '👥', color: '#60A5FA' },
  exploration: { key: 'exploration', label: 'Exploration', icon: '🗺️', color: '#34D399' },
  seasonal: { key: 'seasonal', label: 'Saisonniers', icon: '🌸', color: '#F472B6' },
  secret: { key: 'secret', label: 'Secrets', icon: '❓', color: '#C084FC' },
};

// ── V1 fixture (24 entries — matches "Débloqués X/24" stat) ──────────────

export const ACHIEVEMENTS: Achievement[] = [
  // VOLUME
  { id: 'v_first', label: 'Premier scan', description: 'Scanner ton tout premier ticket', icon: '🎬', rarity: 'terracotta', category: 'volume', progress: 1, target: 1, status: 'unlocked' },
  { id: 'v_10', label: 'Habitué·e', description: 'Scanner 10 tickets', icon: '📋', rarity: 'bronze', category: 'volume', progress: 10, target: 10, status: 'unlocked' },
  { id: 'v_50', label: 'Cinquantaine', description: 'Scanner 50 tickets', icon: '📑', rarity: 'copper', category: 'volume', progress: 47, target: 50, status: 'in_progress' },
  { id: 'v_100', label: 'Centurion', description: 'Scanner 100 tickets', icon: '💯', rarity: 'silver', category: 'volume', progress: 47, target: 100, status: 'in_progress' },
  { id: 'v_500', label: 'Demi-millier', description: 'Scanner 500 tickets', icon: '📊', rarity: 'gold', category: 'volume', progress: 47, target: 500, status: 'in_progress' },
  { id: 'v_1000', label: 'Millier', description: 'Scanner 1000 tickets', icon: '🏆', rarity: 'crystal', category: 'volume', progress: 47, target: 1000, status: 'locked' },
  { id: 'v_barcode_first', label: 'Code-barres décodé', description: 'Scanner ton premier code-barres', icon: '📡', rarity: 'terracotta', category: 'volume', progress: 1, target: 1, status: 'unlocked' },
  { id: 'v_barcode_100', label: 'Lecteur acharné', description: 'Scanner 100 codes-barres', icon: '🔍', rarity: 'silver', category: 'volume', progress: 23, target: 100, status: 'in_progress' },
  { id: 'v_label_first', label: 'Étiquettes traquée', description: 'Scanner ta première étiquette magasin', icon: '🏷️', rarity: 'bronze', category: 'volume', progress: 1, target: 1, status: 'unlocked' },
  { id: 'v_label_50', label: 'Œil de lynx', description: 'Scanner 50 étiquettes', icon: '👁️', rarity: 'silver', category: 'volume', progress: 12, target: 50, status: 'in_progress' },
  { id: 'v_speed', label: 'Speedrunner', description: 'Scanner 5 tickets en moins de 2 min', icon: '⚡', rarity: 'gold', category: 'volume', progress: 0, target: 5, status: 'locked' },
  { id: 'v_marathon', label: 'Marathon', description: 'Scanner 20 tickets en une journée', icon: '🏃', rarity: 'emerald', category: 'volume', progress: 0, target: 20, status: 'locked' },
  // SAVINGS
  { id: 's_1', label: 'Première éco', description: 'Économiser ton premier euro', icon: '🪙', rarity: 'terracotta', category: 'savings', progress: 1, target: 1, status: 'unlocked' },
  { id: 's_10', label: '10 balles', description: 'Économiser 10 €', icon: '💵', rarity: 'bronze', category: 'savings', progress: 10, target: 10, status: 'unlocked' },
  { id: 's_50', label: 'Demi-bil', description: 'Économiser 50 €', icon: '💴', rarity: 'copper', category: 'savings', progress: 47.95, target: 50, status: 'in_progress' },
  { id: 's_100', label: 'Stack', description: 'Économiser 100 €', icon: '💶', rarity: 'gold', category: 'savings', progress: 47.95, target: 100, status: 'in_progress' },
  // STREAK
  { id: 'r_3', label: 'Trio', description: 'Streak de 3 jours', icon: '🔥', rarity: 'bronze', category: 'streak', progress: 3, target: 3, status: 'unlocked' },
  { id: 'r_7', label: 'Semaine pleine', description: 'Streak de 7 jours', icon: '🔥', rarity: 'copper', category: 'streak', progress: 7, target: 7, status: 'unlocked' },
  { id: 'r_14', label: 'Quinzaine', description: 'Streak de 14 jours', icon: '🔥', rarity: 'silver', category: 'streak', progress: 7, target: 14, status: 'in_progress' },
  { id: 'r_30', label: 'Mois sans rater', description: 'Streak de 30 jours', icon: '🔥', rarity: 'sapphire', category: 'streak', progress: 7, target: 30, status: 'in_progress' },
  // SOCIAL
  { id: 'soc_invite_1', label: 'Recruteur', description: 'Inviter 1 ami', icon: '🤝', rarity: 'bronze', category: 'social', progress: 0, target: 1, status: 'locked' },
  // SEASONAL
  { id: 'sea_winter', label: 'Hiver 25', description: 'Avoir participé au Pass Hiver 25', icon: '❄️', rarity: 'emerald', category: 'seasonal', progress: 1, target: 1, status: 'unlocked' },
  // SECRETS
  { id: 'sec_1euro', label: 'Le centime perdu', description: "Économiser exactement 1,00 € sur un seul ticket", icon: '🔍', rarity: 'silver', category: 'secret', progress: 1, target: 1, status: 'unlocked' },
  { id: 'sec_konami', label: '???', description: 'Succès secret', icon: '❓', rarity: 'diamond', category: 'secret', progress: 0, target: 1, status: 'locked' },
];

/** Helper : counts for the stats bar. */
export function getAchievementsStats(items: Achievement[] = ACHIEVEMENTS): {
  total: number;
  unlocked: number;
  inProgress: number;
  scorePct: number;
} {
  const total = items.length;
  const unlocked = items.filter((a) => a.status === 'unlocked').length;
  const inProgress = items.filter((a) => a.status === 'in_progress').length;
  const scorePct = total === 0 ? 0 : Math.round((unlocked / total) * 100);
  return { total, unlocked, inProgress, scorePct };
}
