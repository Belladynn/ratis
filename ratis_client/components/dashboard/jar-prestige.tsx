// ratis_client/components/dashboard/jar-prestige.tsx
//
// "Tirelire" hero — port of `Ratis_handoff/lib/roi-variants.jsx`
// `RoiV5_Jar` + `JarShape` (lines 209-413), restricted to the V1 spec from
// `ARCH_frontend_strict_iso.md` § Décision hero (lines 132-156) :
//   - ONE jar shape (the glass bocal — NOT the 5 different shapes from the
//     JSX which are V2 hors-scope)
//   - 5 colors cycled by `prestigeLevel % 5` (terre cuite, bronze, cuivre,
//     argent, or — palette in `constants/theme.ts` `JarTiers`)
//   - fill animated via Reanimated (currentFill 0..100)
//   - sparkles staggered (5 of them, 5.2-7.0s loop)
//   - coin fall continuous (3 of them, 3.2-4.1s loop)
//   - halo pulse 2.4s when full (currentFill ≥ 100 OR isFullState explicit)
//   - ray spin 14s when full
//   - title "TIRELIRE" pink/coral
//   - big total "{savings}€"
//   - "{fill_pct}%" overlay (rounded)
//   - footer "Plus que {next_tier_remaining}€ → palier suivant"
//
// API contract — kept compatible with the V4 test expectations preserved at
// `git@01d62ff:ratis_client/__tests__/components/dashboard/jar-prestige.test.tsx`
// (see also `chunk-1-followups.md` for the rationale of restoring those
// tests as-is).
//
// Skia is used for the SVG-like jar shape (richer than Image); Reanimated
// drives the fill rectangle's height. The animation loops are isolated as
// dedicated wrappers so the test mock for Skia (`__mocks__/shopify-react-
// native-skia.tsx`) renders the tree as plain Views.

import React, { useEffect, useMemo } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import {
  Canvas,
  Path,
  Group,
  Skia,
  LinearGradient as SkiaLinearGradient,
  vec,
} from '@shopify/react-native-skia';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';

import { Colors, getJarTier, Typography } from '@/constants/theme';

export type JarPrestigeProps = {
  /** Fill percentage 0..100 (clamped). */
  currentFill: number;
  /** Prestige level (drives the tier color via `prestigeLevel % 5`). */
  prestigeLevel: number;
  /** Lifetime savings in cents — displayed as "{euros},{cents}€". */
  totalSaved: number;
  /**
   * Optional alternate display : when provided, replaces the percent overlay
   * with the fractional abonnement count "{n,d}". Mirrors the historical
   * V4 jar API (preserved for test compatibility).
   */
  totalAbonnements?: number;
  /**
   * Remaining cents to unlock the next tier. When > 0, footer renders
   * "Plus que {N}€ → palier suivant" (default template). When 0/undefined,
   * the footer is omitted.
   */
  nextTierRemainingCents?: number;
  /**
   * Optional i18n template for the footer — must contain `{{amount}}` which
   * gets replaced by the integer-rounded EUR amount. Default :
   * `Plus que {{amount}}€ → palier suivant`.
   */
  nextTierFooterTemplate?: string;
  onPress?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

// `{{amount}}` is rendered as "Xeuros€" (unit suffix included by `renderFooter`).
const DEFAULT_FOOTER = 'Plus que {{amount}} → palier suivant';

function clampFill(v: number): number {
  if (Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 100) return 100;
  return v;
}

function formatEUR(cents: number): string {
  return (cents / 100).toFixed(2).replace('.', ',') + '€';
}

function formatAbonnements(n: number): string {
  return n.toFixed(1).replace('.', ',');
}

function renderFooter(template: string, cents: number): string {
  const euros = Math.round(cents / 100);
  // V4 contract : `{{amount}}` is the EUR amount with the unit suffix already
  // baked in (the JSX displayed "X€" inline). Tests assert this auto-suffix.
  return template.replace('{{amount}}', `${euros}€`);
}

// --- Animation atoms -------------------------------------------------------

function Sparkle({
  position,
  size,
  duration,
  delay,
  color,
}: {
  position: { left?: number; right?: number; top?: number; bottom?: number };
  size: number;
  duration: number;
  delay: number;
  color: string;
}) {
  const opacity = useSharedValue(0.4);
  useEffect(() => {
    opacity.value = withDelay(
      delay,
      withRepeat(
        withSequence(
          withTiming(0.95, { duration: duration / 2, easing: Easing.inOut(Easing.ease) }),
          withTiming(0.25, { duration: duration / 2, easing: Easing.inOut(Easing.ease) }),
        ),
        -1,
        false,
      ),
    );
    return () => {
      cancelAnimation(opacity);
    };
  }, [opacity, duration, delay]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return (
    <Animated.Text
      style={[
        styles.sparkle,
        position,
        { fontSize: size, color, textShadowColor: color },
        animated,
      ]}
    >
      ✨
    </Animated.Text>
  );
}

function CoinFall({
  left,
  size,
  duration,
  delay,
}: {
  left: string;
  size: number;
  duration: number;
  delay: number;
}) {
  const y = useSharedValue(-20);
  const opacity = useSharedValue(0);
  useEffect(() => {
    y.value = withDelay(
      delay,
      withRepeat(
        withTiming(160, { duration, easing: Easing.in(Easing.quad) }),
        -1,
        false,
      ),
    );
    opacity.value = withDelay(
      delay,
      withRepeat(
        withSequence(
          withTiming(1, { duration: duration * 0.1 }),
          withTiming(1, { duration: duration * 0.7 }),
          withTiming(0, { duration: duration * 0.2 }),
        ),
        -1,
        false,
      ),
    );
    return () => {
      cancelAnimation(y);
      cancelAnimation(opacity);
    };
  }, [y, opacity, duration, delay]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ translateY: y.value }],
    opacity: opacity.value,
  }));
  return (
    <Animated.Text
      pointerEvents="none"
      style={[styles.coin, { left: left as never, fontSize: size }, animated]}
    >
      🪙
    </Animated.Text>
  );
}

function HaloPulse({ color }: { color: string }) {
  const scale = useSharedValue(0.9);
  useEffect(() => {
    scale.value = withRepeat(
      withSequence(
        withTiming(1.05, { duration: 1200, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.9, { duration: 1200, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
      false,
    );
    return () => {
      cancelAnimation(scale);
    };
  }, [scale]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }));
  return (
    <Animated.View
      pointerEvents="none"
      style={[styles.halo, { backgroundColor: color }, animated]}
    />
  );
}

function RaySpin() {
  const rotation = useSharedValue(0);
  useEffect(() => {
    rotation.value = withRepeat(
      withTiming(360, { duration: 14000, easing: Easing.linear }),
      -1,
      false,
    );
    return () => {
      cancelAnimation(rotation);
    };
  }, [rotation]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ rotate: `${rotation.value}deg` }],
  }));
  return <Animated.View pointerEvents="none" style={[styles.ray, animated]} />;
}

// --- Skia jar shape --------------------------------------------------------

const JAR_W = 100;
const JAR_H = 120;

function JarSkiaShape({
  fillPct,
  tierColors,
}: {
  fillPct: number;
  tierColors: { hi: string; mid: string; lo: string; sh: string };
}) {
  const jarPath = useMemo(() => {
    const p = Skia.Path.Make();
    // Mirrors the JSX path: M20 25 L20 90 Q20 100 30 100 L70 100 Q80 100 80 90 L80 25 Z
    p.moveTo(20, 25);
    p.lineTo(20, 90);
    p.quadTo(20, 100, 30, 100);
    p.lineTo(70, 100);
    p.quadTo(80, 100, 80, 90);
    p.lineTo(80, 25);
    p.close();
    return p;
  }, []);

  const fillHeight = (75 * fillPct) / 100; // 75 = 100 - 25 (jar interior height)
  const fillTop = 25 + (75 - fillHeight);

  const fillPath = useMemo(() => {
    const p = Skia.Path.Make();
    p.addRect(Skia.XYWHRect(20, fillTop, 60, fillHeight));
    return p;
    // re-create when fillTop changes — Skia path is immutable
  }, [fillTop, fillHeight]);

  const lidPath = useMemo(() => {
    const p = Skia.Path.Make();
    p.addRect(Skia.XYWHRect(22, 18, 56, 10));
    return p;
  }, []);

  return (
    <Canvas style={{ width: JAR_W + 10, height: JAR_H }}>
      <Group>
        {/* Glass jar outline */}
        <Path path={jarPath} color="rgba(255,255,255,0.05)" />
        <Path
          path={jarPath}
          style="stroke"
          strokeWidth={1.5}
          color="rgba(255,255,255,0.45)"
        />
        {/* Fill (gradient) — clipped within the jar bounds */}
        {fillPct > 0 ? (
          <Group clip={jarPath}>
            <Path path={fillPath}>
              <SkiaLinearGradient
                start={vec(50, fillTop)}
                end={vec(50, fillTop + fillHeight)}
                colors={[tierColors.hi, tierColors.mid, tierColors.lo]}
              />
            </Path>
          </Group>
        ) : null}
        {/* Lid (pink/coral — Colors.jarPink) */}
        <Path path={lidPath} color={Colors.jarPink} />
        <Path
          path={lidPath}
          style="stroke"
          strokeWidth={1}
          color="rgba(0,0,0,0.4)"
        />
      </Group>
    </Canvas>
  );
}

// --- Top-level component ---------------------------------------------------

export function JarPrestige({
  currentFill,
  prestigeLevel,
  totalSaved,
  totalAbonnements,
  nextTierRemainingCents,
  nextTierFooterTemplate = DEFAULT_FOOTER,
  onPress,
  testID = 'jar-prestige',
  style,
}: JarPrestigeProps) {
  const fill = clampFill(currentFill);
  const isFull = fill >= 100;
  const tier = getJarTier(prestigeLevel);

  const Body = onPress ? Pressable : View;

  // Sparkle palette : less saturated when not full, gold-ish when full.
  const sparkleColor = isFull ? '#FFE176' : 'rgba(255, 230, 200, 0.85)';

  const showAbonnementsOverlay = typeof totalAbonnements === 'number';

  return (
    <View testID={testID} style={[styles.root, style]}>
      {/* Base halo — always visible (subtle pink) */}
      <View style={styles.baseHalo} pointerEvents="none" />

      {/* Full-state effects */}
      {isFull ? (
        <>
          <HaloPulse color="rgba(255,184,0,0.32)" />
          <RaySpin />
        </>
      ) : null}

      {/* Sparkles (5 staggered) */}
      <Sparkle
        position={{ left: 14, top: 30 }}
        size={11}
        duration={5200}
        delay={0}
        color={sparkleColor}
      />
      <Sparkle
        position={{ right: 18, top: 56 }}
        size={9}
        duration={6400}
        delay={1500}
        color={sparkleColor}
      />
      <Sparkle
        position={{ left: 24, bottom: 70 }}
        size={10}
        duration={5800}
        delay={2800}
        color={sparkleColor}
      />
      <Sparkle
        position={{ right: 14, bottom: 92 }}
        size={12}
        duration={7000}
        delay={4100}
        color={sparkleColor}
      />
      <Sparkle
        position={{ left: '50%', top: 14 } as never}
        size={8}
        duration={5500}
        delay={3600}
        color={sparkleColor}
      />

      {/* Coin fall (3, continuous) */}
      <CoinFall left="38%" size={12} duration={3200} delay={0} />
      <CoinFall left="58%" size={11} duration={3800} delay={1400} />
      <CoinFall left="48%" size={10} duration={4100} delay={2600} />

      {/* Press surface */}
      <Body
        testID={onPress ? 'jar-press' : undefined}
        onPress={onPress}
        accessibilityRole={onPress ? 'button' : undefined}
        style={styles.content}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.label}>TIRELIRE</Text>
          <Text testID={testID ? `${testID}-eur` : 'jar-eur'} style={styles.amount}>
            {formatEUR(totalSaved)}
          </Text>
        </View>

        {/* Jar with overlay */}
        <View style={styles.jarFrame}>
          <JarSkiaShape fillPct={fill} tierColors={tier} />
          {showAbonnementsOverlay ? (
            <View
              testID={testID ? `${testID}-abonnements` : 'jar-abonnements'}
              style={styles.overlay}
              pointerEvents="none"
            >
              <Text style={styles.overlayText}>
                {formatAbonnements(totalAbonnements as number)}
              </Text>
            </View>
          ) : (
            <View
              testID={testID ? `${testID}-percent` : 'jar-percent'}
              style={styles.overlay}
              pointerEvents="none"
            >
              <Text style={styles.overlayText}>{Math.round(fill)}%</Text>
            </View>
          )}
        </View>

        {/* Footer */}
        {nextTierRemainingCents && nextTierRemainingCents > 0 ? (
          <Text
            testID={testID ? `${testID}-footer` : 'jar-footer'}
            style={styles.footer}
          >
            {renderFooter(nextTierFooterTemplate, nextTierRemainingCents)}
          </Text>
        ) : null}
      </Body>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'relative',
    overflow: 'hidden',
    flex: 1,
    minHeight: 220,
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 20,
    backgroundColor: '#3D1F2A',
    borderWidth: 1.5,
    borderColor: 'rgba(255,107,157,0.55)',
    // 3D shadow — single hard layer.
    shadowColor: 'rgba(80,20,40,0.85)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  baseHalo: {
    position: 'absolute',
    left: '50%',
    top: '52%',
    width: 170,
    height: 170,
    marginLeft: -85,
    marginTop: -85,
    borderRadius: 85,
    backgroundColor: 'rgba(255,107,157,0.30)',
    opacity: 0.7,
  },
  halo: {
    position: 'absolute',
    left: '50%',
    top: '52%',
    width: 240,
    height: 240,
    marginLeft: -120,
    marginTop: -120,
    borderRadius: 120,
    opacity: 0.6,
  },
  ray: {
    position: 'absolute',
    left: '50%',
    top: '52%',
    width: 220,
    height: 220,
    marginLeft: -110,
    marginTop: -110,
    borderRadius: 110,
    backgroundColor: 'rgba(255,184,0,0.05)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.18)',
    opacity: 0.7,
  },
  content: {
    flex: 1,
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'space-between',
    zIndex: 4,
  },
  header: {
    alignItems: 'center',
  },
  label: {
    ...Typography.label,
    fontSize: 8,
    letterSpacing: 0.8,
    color: 'rgba(255,107,157,0.85)',
  },
  amount: {
    fontFamily: 'Inter_900Black',
    fontSize: 26,
    color: Colors.textPrimary,
    letterSpacing: -0.8,
    marginTop: 2,
    textShadowColor: 'rgba(0,0,0,0.5)',
    textShadowOffset: { width: 0, height: 2 },
    textShadowRadius: 0,
  },
  jarFrame: {
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
    width: JAR_W + 10,
    height: JAR_H,
  },
  overlay: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: '55%',
    alignItems: 'center',
    justifyContent: 'center',
  },
  overlayText: {
    fontFamily: 'Inter_900Black',
    fontSize: 14,
    color: '#5C3D00',
    textShadowColor: 'rgba(255,255,255,0.45)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 0,
  },
  footer: {
    fontSize: 10,
    color: Colors.jarPink,
    fontWeight: '700',
    textAlign: 'center',
    lineHeight: 13,
  },
  sparkle: {
    position: 'absolute',
    textShadowOffset: { width: 0, height: 0 },
    textShadowRadius: 6,
    zIndex: 3,
  },
  coin: {
    position: 'absolute',
    top: -4,
    fontSize: 12,
    zIndex: 2,
  },
});

export default JarPrestige;
