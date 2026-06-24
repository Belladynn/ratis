/**
 * Animation tokens — Ratis client design system.
 *
 * Référence : `ARCH_design_system.md` § Animations & Micro-interactions
 * (mapping CSS keyframes → Reanimated 4). Source visuelle :
 * `Ratis Design Pattern v2.html`.
 *
 * Pourquoi des constantes plutôt que de hardcoder dans chaque composant :
 *
 *   1. **Cohérence** — tous les composants partagent le même rythme (les
 *      pulsations s'alignent visuellement entre eux).
 *   2. **Tunable centralisé** — quand le design pattern v2 évolue, on n'a
 *      qu'un fichier à éditer.
 *   3. **Tests** — l'équipe peut asserter `Durations.loop.jackPulse === 2000`
 *      sans avoir à instrumenter chaque composant.
 *
 * On expose des **valeurs scalaires** (durée en ms, bezier en tuple), pas
 * des objets Reanimated `Easing` instanciés. Raison : importer `Easing` ici
 * tirerait la worklet runtime dans le bundle des tests. Les composants
 * importent `Easing` depuis `react-native-reanimated` et le construisent à
 * partir de ces presets via `EasingPresets.bouncy → Easing.bezier(...preset)`.
 */

// ---------------------------------------------------------------------------
// Durations (ms).
// ---------------------------------------------------------------------------

export const Durations = {
  /** Feedback tactile (pressed scale, haptic). */
  instant: 100,
  /** Transitions UI courantes (fade in/out, color flash). */
  fast: 200,
  /** Max pour transitions de page (hard rule § Animations). */
  normal: 300,
  /** Célébrations (coin collect, level up). */
  slow: 500,

  /**
   * Boucles infinies (`withRepeat(..., -1)`). Voir le mapping détaillé
   * `ARCH_design_system.md` § Mapping CSS keyframes → Reanimated 4.
   */
  loop: {
    /** JackMascot — boxShadow breathing glow. 2s. */
    jackPulse: 2000,
    /** ROI rings — halo pulse derrière chaque anneau actif. 2.4s. */
    roiHaloPulse: 2400,
    /** ROI rings — gradient lumineux qui tourne autour du ring actif. 3.2s. */
    roiLightSpin: 3200,
    /** ROI rings — fossiles (markers) qui blinkent en cascade. 1.6s par fossile. */
    roiFossilBlink: 1600,
    /** Jar — halo rose pulsant. 2.4s (parité avec roiHaloPulse). */
    jarHaloPulse: 2400,
    /** Jar — rayons radiaux qui tournent (rotation lente). 14s. */
    jarRayspin: 14000,
    /** Jar — étincelles ✨ stagger (4-6 instances, 5-7s) — moyenne 5.5s. */
    jarSparkle: 5500,
    /** Jar — pièces 🪙 qui tombent (3 instances, 3-4s) — moyenne 3.5s. */
    jarCoinFall: 3500,
    /** Jar Skia — surface du fill ondulée (sin wave). 4s. */
    jarSurfaceWave: 4000,
  },

  /**
   * Jar particles (PR4.1 — Skia rendering).
   *
   * Durations one-shot des systèmes de particules pilotés par `useFrameCallback`
   * dans `<JarParticles />`. Centralisées ici pour tuning unique.
   */
  particles: {
    /** Coin drop (un coin individuel). 1500ms timeline (gravité + fade-out). */
    coinDrop: 1500,
    /** Sparkle ambient — période d'oscillation opacity. 1800ms. */
    sparkleOscillate: 1800,
    /** Tier transition burst — durée totale du burst radial. 800ms. */
    tierBurst: 800,
    /** Fill lerp — animation de la prop currentFill quand elle change. 600ms. */
    fillLerp: 600,
  },
} as const;

// ---------------------------------------------------------------------------
// Easings — noms symboliques + tuples bezier.
//
// Côté composant :
//
//   import { Easing } from 'react-native-reanimated';
//   import { EasingPresets } from '@/constants/animations';
//
//   const ease = Easing.bezier(...EasingPresets.bouncy);  // bouncy slideUp
//   const ease = Easing.out(Easing.cubic);                // EasingPresets.out
//   const ease = Easing.linear;                           // EasingPresets.linear
// ---------------------------------------------------------------------------

export const EasingPresets = {
  /** Sortie de cubic — `Easing.out(Easing.cubic)`. Default UI. */
  out: 'cubicOut',
  /** In-out cubic — `Easing.inOut(Easing.cubic)`. Pulses, transitions. */
  inOut: 'cubicInOut',
  /** Pop élastique — pour iconPop, achUnlockSlideIn (V2). */
  elasticOut: 'elasticOut',
  /** Linéaire — rotations infinies (roiLightSpin, jarRayspin). */
  linear: 'linear',
  /**
   * Bouncy slideUp — bezier(0.2, 0.9, 0.3, 1.2) tiré du design pattern v2.
   * À passer à `Easing.bezier(...EasingPresets.bouncy)` côté composant.
   */
  bouncy: [0.2, 0.9, 0.3, 1.2] as const,
} as const;

// ---------------------------------------------------------------------------
// Patterns réutilisables (documentation inline — pas d'instanciation worklet
// ici, sinon on tire `Easing` dans le bundle de tests).
// ---------------------------------------------------------------------------

/**
 * Reference patterns — à copier-coller dans les composants de PR3+.
 *
 * Pulse (ex: jackPulse, roiHaloPulse) :
 *
 *   const v = useSharedValue(0);
 *   useEffect(() => {
 *     v.value = withRepeat(
 *       withTiming(1, { duration: Durations.loop.jackPulse / 2,
 *                       easing: Easing.inOut(Easing.cubic) }),
 *       -1, true,  // reversed loop
 *     );
 *   }, []);
 *
 * Spin infini (ex: jarRayspin) :
 *
 *   v.value = withRepeat(
 *     withTiming(360, { duration: Durations.loop.jarRayspin,
 *                       easing: Easing.linear }),
 *     -1, false,  // non-reversed
 *   );
 *
 * Stagger fossile (cascade) :
 *
 *   fossils.forEach((_, i) => {
 *     v[i].value = withDelay(
 *       (i * Durations.loop.roiFossilBlink) / fossils.length,
 *       withRepeat(
 *         withTiming(1, { duration: Durations.fast }), -1, true,
 *       ),
 *     );
 *   });
 */
export const __PATTERNS_DOC__ = true;
