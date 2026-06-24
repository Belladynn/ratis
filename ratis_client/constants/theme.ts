/**
 * Design system tokens — Ratis client.
 *
 * Pivot Duolingo / Clash Royale (acté 2026-05-03 — voir
 * `ARCH_design_system.md` § Décisions notables). Référence visuelle :
 * `ratis_client/Ratis Design Pattern v2.html`.
 *
 * Surface des exports :
 *
 *   - `Colors` (NEW)        palette pivotée : bg/surface, terracotta, gold,
 *                           jarPink, accents secondaires, texte.
 *   - `RingColors` (NEW)    cycle de 10 couleurs des ROI rings.
 *   - `RewardTiers` (NEW)   gradients bronze → diamond (V2 achievements,
 *                           palette posée dès V1).
 *   - `Rarity` (NEW)        échelle common → legendary (V2 badges).
 *   - `Spacing` (NEW)       4px base, xs..xxl.
 *   - `Radii` (NEW)         icon/badge/btn/card/modal.
 *   - `Shadows` (NEW)       relief 3D dur (card / buttonPrimary / buttonClaim).
 *   - `Typography` (NEW)    Inter weights 400-900, niveaux 9/11/13/15/22/28.
 *
 *   - `LegacyColors` (compat)  l'ancien color scheme light/dark consommé par
 *                              `useThemeColor` + `Collapsible`. Migré en PR3/PR4
 *                              quand les composants concernés rejoindront le
 *                              nouveau design system. Ne plus l'utiliser dans
 *                              du nouveau code.
 *   - `Fonts` (compat)         fontFamily map plateforme — utilisé par
 *                              `app/(tabs)/explore.tsx` (tab dev). Dépréciée :
 *                              `Typography.*` est la voie canonique pour les
 *                              nouveaux composants.
 *   - `Design` (compat)        anciens tokens consommés par `roi-rings.tsx` /
 *                              `missions-card.tsx` du Dashboard V0. Refonte
 *                              programmée PR4.
 */

import { Platform } from 'react-native';

// ---------------------------------------------------------------------------
// Pivot palette (2026-05-03) — source de vérité unique pour tout NEW code.
// ---------------------------------------------------------------------------

export const Colors = {
  // Backgrounds
  bg: '#1a2428', // Fond écran — JAMAIS autre chose (règle cardinale)
  surface: '#27293A', // Cards / surfaces standard
  overlay: '#0F1419', // Modals / bottom sheets

  // Rôles sémantiques (max 2 accents par écran)
  terracotta: '#DA7756', // Action principale — CTA, navbar scan, optimiser
  terracottaHi: '#E8896A', // Top du gradient bouton (180deg, hi → terracotta)
  terracottaLo: '#A8562E', // Bordure bouton primary
  terracottaSh: '#6B3218', // Ombre dure 3D (0 4px 0)

  gold: '#FFB800', // Claim / récompense — XP, +CAB, prix
  goldHi: '#FFE066',
  goldLo: '#B47800',
  goldSh: '#7E5300',

  jarPink: '#FF6B9D', // Économies — émotionnel positif (jar / total liste)
  jarPinkHi: '#FF8FB3',
  jarPinkBg1: '#2A1A1A', // Card économies (gradient 160deg, bg1 → bg2)
  jarPinkBg2: '#1F1212',

  // Accents secondaires (par feature, jamais 2 sur le même écran)
  violet: '#A78BFA', // Missions hebdo
  violetText: '#C4B5FD',
  orange: '#FF6B35', // Missions daily, Jack streak
  orangeText: '#FFB89D',
  cyan: '#0EA5E9', // Scan fullscreen, battlepass header
  cyanText: '#67E8F9',
  amber: '#F59E0B', // Saison, progression
  amberText: '#FCD34D',
  coral: '#EF4444', // Alertes, reset, danger
  coralText: '#FCA5A5',

  // Texte (sur fond #1a2428)
  textPrimary: '#FFFFFF',
  textSecondary: 'rgba(255,255,255,0.45)',
  textTertiary: 'rgba(255,255,255,0.30)',
  textMuted: 'rgba(255,255,255,0.40)',
} as const;

/**
 * ROI rings — 10 couleurs cyclées (gaming Duolingo style, dashboard hero).
 * L'ordre est significatif : indexe les rings dans l'ordre de complétion.
 */
export const RingColors = [
  '#22D3EE', //  1  cyan
  '#2DD4BF', //  2  teal
  '#34D399', //  3  green
  '#A3E635', //  4  lime
  '#FACC15', //  5  yellow
  '#FBBF24', //  6  amber
  '#F97316', //  7  orange
  '#EF4444', //  8  red
  '#EC4899', //  9  pink
  '#A855F7', // 10  purple
] as const;

/**
 * Tiers de récompenses (achievements V2 — palette posée dès V1).
 * Tuple `[hi, lo]` consommé par `linear-gradient(135deg, hi, lo)`.
 */
export const RewardTiers = {
  bronze: ['#CD7F32', '#8B4513'],
  silver: ['#C0C0C0', '#808080'],
  gold: ['#FFD700', '#FFA500'],
  platinum: ['#E5E4E2', '#B0C4DE'],
  diamond: ['#FF6B9D', '#A855F7'], // gradient jar pink → purple
} as const;

/**
 * Jar prestige tiers — 5 paliers cyclés via `prestigeLevel % 5`.
 *
 * Pivot game design 2026-05-03 (PR4.1) : remplace les RoiRings par un seul
 * bocal-tirelire dont la teinte change à chaque transition de prestige.
 *
 * Ordre :
 *   0 → terre cuite (terracotta-ish, palette douce du design system)
 *   1 → bronze
 *   2 → cuivre
 *   3 → argent
 *   4 → or (dernier tier avant cycle)
 *
 * Chaque tier est un tuple `[hi, mid, lo, sh]` :
 *   - `hi`  highlight haut du fill (gradient 0%)
 *   - `mid` couleur "principale" du tier (utilisée pour bord + halo)
 *   - `lo`  bas du fill / ombre intérieure (gradient 100%)
 *   - `sh`  shadow / outline foncé pour relief
 */
export const JarTiers = [
  // 0 — terre cuite (cohérent avec terracotta du design system, plus doux)
  { hi: '#E8A179', mid: '#C97D5C', lo: '#9A5436', sh: '#5C2F1B' },
  // 1 — bronze
  { hi: '#E8A35F', mid: '#CD7F32', lo: '#8B4513', sh: '#4A2509' },
  // 2 — cuivre
  { hi: '#E0925A', mid: '#B87333', lo: '#7A4A1F', sh: '#3D2310' },
  // 3 — argent
  { hi: '#F5F5F5', mid: '#C0C0C0', lo: '#808080', sh: '#3F3F3F' },
  // 4 — or (Colors.gold + nuances)
  { hi: '#FFE066', mid: '#FFB800', lo: '#B47800', sh: '#7E5300' },
] as const;

export type JarTier = (typeof JarTiers)[number];

/** Sélecteur safe : `prestigeLevel % JarTiers.length`. */
export function getJarTier(prestigeLevel: number): JarTier {
  const idx = ((prestigeLevel % JarTiers.length) + JarTiers.length) % JarTiers.length;
  return JarTiers[idx];
}

/**
 * Rarity badges (V2 achievements). Couleur de glow / accent par tier.
 */
export const Rarity = {
  common: 'rgba(255,255,255,0.40)',
  rare: '#22D3EE',
  epic: '#A855F7',
  legendary: '#FFB800', // + holo shine overlay au unlock
} as const;

// ---------------------------------------------------------------------------
// Spacing / Radii — base 4px.
// ---------------------------------------------------------------------------

export const Spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const Radii = {
  icon: 10, // icône dans bouton secondaire / chips internes
  badge: 8, // badges, pills, rules
  btn: 14, // boutons (primary + secondary)
  btnSm: 12, // bouton claim gold (taille sm)
  card: 20, // cards (standard + accent)
  modal: 24, // bottom sheets
} as const;

// ---------------------------------------------------------------------------
// Shadows — relief 3D Clash Royale.
//
// RN ne supporte pas `box-shadow` CSS multi-layer ni `inset` natif. Pour
// reproduire l'empilement dur+diffus+inset : `View` racine avec
// `shadowOffset` dur (Android `elevation`) + `View` interne avec border-top
// blanc 1px (simule `inset 0 1px 0`) + wrapper externe optionnel pour la
// diffuse plus large. Pattern documenté dans `card.tsx` (PR3).
//
// `insetTop` est exposé en string-rgba — les composants l'utilisent via
// `borderTopColor` / `borderTopWidth: 1` ou un `View` overlay en pointer-
// events="none".
// ---------------------------------------------------------------------------

export const Shadows = {
  // Card : 0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4),
  //        inset 0 1px 0 rgba(255,255,255,0.08)
  card: {
    hard: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 5 },
      shadowRadius: 0,
      shadowOpacity: 0.35,
      elevation: 5,
    },
    diffuse: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 12 },
      shadowRadius: 22,
      shadowOpacity: 0.4,
      elevation: 8,
    },
    insetTop: 'rgba(255,255,255,0.08)' as const,
  },
  // Button primary : 0 4px 0 #6B3218, inset 0 1px 0 rgba(255,255,255,0.35)
  buttonPrimary: {
    hard: {
      shadowColor: '#6B3218',
      shadowOffset: { width: 0, height: 4 },
      shadowRadius: 0,
      shadowOpacity: 1,
      elevation: 4,
    },
    insetTop: 'rgba(255,255,255,0.35)' as const,
  },
  // Button claim (gold) : 0 3px 0 #7E5300
  buttonClaim: {
    hard: {
      shadowColor: '#7E5300',
      shadowOffset: { width: 0, height: 3 },
      shadowRadius: 0,
      shadowOpacity: 1,
      elevation: 3,
    },
    insetTop: 'rgba(255,255,255,0.40)' as const,
  },
} as const;

// ---------------------------------------------------------------------------
// Typography — Inter uniquement (weights 400-900 chargés via expo-font).
//
// Les `fontFamily` correspondent aux noms exposés par `@expo-google-fonts/
// inter`. Tant que `useFonts` n'a pas résolu, RN tombera silencieusement sur
// la fonte système (graceful fallback) — c'est pourquoi le splash screen
// reste actif jusqu'au fontsLoaded (cf `_layout.tsx`).
// ---------------------------------------------------------------------------

export const Typography = {
  label: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 9,
    letterSpacing: 0.8,
    textTransform: 'uppercase' as const,
  },
  body: {
    fontFamily: 'Inter_400Regular',
    fontSize: 14,
    letterSpacing: 0,
  },
  bodySm: {
    fontFamily: 'Inter_600SemiBold',
    fontSize: 11,
    letterSpacing: 0,
  },
  itemTitle: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 13,
    letterSpacing: -0.2,
  },
  cardTitle: {
    fontFamily: 'Inter_900Black',
    fontSize: 15,
    letterSpacing: -0.3,
  },
  hero: {
    fontFamily: 'Inter_900Black',
    fontSize: 22,
    letterSpacing: -0.6,
  },
  metric: {
    fontFamily: 'Inter_900Black',
    fontSize: 28,
    letterSpacing: -1.2,
  },
} as const;

// ---------------------------------------------------------------------------
// Backward-compat exports (DEPRECATED — refonte PR3/PR4).
//
// Ces exports persistent uniquement pour ne pas casser les call-sites
// existants pendant la migration. Tout NEW code doit consommer la pivot
// palette ci-dessus. À supprimer quand les call-sites listés dans le
// commentaire de chaque export auront été migrés.
// ---------------------------------------------------------------------------

const tintColorLight = '#0a7ea4';
const tintColorDark = '#fff';

/**
 * @deprecated Used by `hooks/use-theme-color.ts` + `components/ui/collapsible.tsx`.
 * Migrate to `Colors` (pivot palette) when those components are refactored
 * (PR3/PR4). Cf `ARCH_design_system.md`.
 */
export const LegacyColors = {
  light: {
    text: '#11181C',
    background: '#fff',
    tint: tintColorLight,
    icon: '#687076',
    tabIconDefault: '#687076',
    tabIconSelected: tintColorLight,
  },
  dark: {
    text: '#ECEDEE',
    background: '#151718',
    tint: tintColorDark,
    icon: '#9BA1A6',
    tabIconDefault: '#9BA1A6',
    tabIconSelected: tintColorDark,
  },
} as const;

/**
 * @deprecated Plateforme fontFamily map — consommé par `app/(tabs)/explore.tsx`
 * uniquement (un onglet dev/template). Préférer `Typography` pour tout
 * nouveau composant.
 */
export const Fonts = Platform.select({
  ios: {
    sans: 'system-ui',
    serif: 'ui-serif',
    rounded: 'ui-rounded',
    mono: 'ui-monospace',
  },
  default: {
    sans: 'normal',
    serif: 'serif',
    rounded: 'normal',
    mono: 'monospace',
  },
  web: {
    sans: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    serif: "Georgia, 'Times New Roman', serif",
    rounded:
      "'SF Pro Rounded', 'Hiragino Maru Gothic ProN', Meiryo, 'MS PGothic', sans-serif",
    mono: "SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  },
});

/**
 * @deprecated Anciens tokens consommés par le Dashboard V0
 * (`components/dashboard/{roi-rings,missions-card}.tsx`). La refonte PR4 les
 * remplacera par `Colors` (pivot palette) + `RingColors`.
 */
export const Design = {
  colors: {
    bg: '#1a2e38',
    bgBrick1: '#1e3a3f',
    bgBrick2: '#1a3439',
    bgBrickStroke: '#152d32',
    text: '#2A2A2A',
    textMuted: '#5A5A5A',
    textOnDark: '#FFFFFF',
    textOnDarkMuted: 'rgba(255,255,255,0.8)',
    gold: '#FFB800',
    teal: '#00D9B5',
    tealDark: '#008f78',
    green: '#10B981',
    orange: '#FF6B35',
    orangeDark: '#E63E11',
    pink: '#EC4899',
    purple: '#A855F7',
    purpleDark: '#7C3BAD',
    paper: '#D4A574',
    divider: 'rgba(0,0,0,0.1)',
    overlay5: 'rgba(0,0,0,0.05)',
    overlay8: 'rgba(0,0,0,0.08)',
    pinRed: '#E63946',
    pinYellow: '#FFB800',
    pinTeal: '#2A9D8F',
    pinPurple: '#A855F7',
    surface: '#2a3f4e',
    surfaceDeep: '#374e5d',
    slate: '#78909C',
    goldFaint: 'rgba(255,183,0,0.07)',
    goldFaintBorder: 'rgba(255,183,0,0.35)',
    goldBadgeBg: 'rgba(255,183,0,0.15)',
    goldBadgeBorder: 'rgba(255,183,0,0.40)',
    tealFaint: 'rgba(0,217,181,0.15)',
    tealFaintBorder: 'rgba(0,217,181,0.40)',
    purpleFaint: 'rgba(168,85,247,0.15)',
    purpleFaintBorder: 'rgba(168,85,247,0.40)',
    dividerDark: 'rgba(255,255,255,0.08)',
  },
  spacing: {
    xs: 4,
    sm: 8,
    md: 16,
    lg: 24,
    xl: 32,
  },
  radius: {
    card: 0,
    badge: 6,
    icon: 8,
    pill: 999,
  },
} as const;

/**
 * @deprecated Premium dark palette utilisée temporairement par certains écrans
 * (refonte planifiée PR4). Ne pas l'utiliser dans du nouveau code — la pivot
 * palette `Colors` exposée ci-dessus la remplace intégralement.
 */
export const DarkTheme = {
  bg: {
    base: '#1a2428',
    card: 'rgba(255,255,255,0.03)',
    cardBorder: 'rgba(255,255,255,0.08)',
    headerOverlay: 'rgba(10,14,16,0.85)',
  },
  glow: {
    teal: 'rgba(77, 212, 179, 0.18)',
    amber: 'rgba(255, 184, 0, 0.10)',
  },
  text: {
    primary: '#FFFFFF',
    secondary: 'rgba(255,255,255,0.45)',
    muted: 'rgba(255,255,255,0.3)',
  },
  accent: {
    teal: '#4DD4B3',
    tealSoft: 'rgba(77,212,179,0.15)',
    gold: '#FFB800',
    goldHighlight: '#FFD860',
    red: '#EF4444',
    redSoft: '#F87171',
    violet: '#8B5CF6',
    violetSoft: '#A78BFA',
    orange: '#FB923C',
    orangeSoft: '#FED7AA',
    cyan: '#22D3EE',
    coral: '#FB7185',
    coralDark: '#E11D48',
    royalViolet: '#7C3AED',
    royalVioletLight: '#A78BFA',
    royalVioletDark: '#5B21B6',
  },
  ring: {
    1: '#22D3EE',
    2: '#2DD4BF',
    3: '#34D399',
    4: '#A3E635',
    5: '#FACC15',
    6: '#FBBF24',
    7: '#F97316',
    8: '#EF4444',
    9: '#EC4899',
    10: '#A855F7',
  } as const,
} as const;
