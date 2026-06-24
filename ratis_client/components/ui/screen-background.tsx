// ratis_client/components/ui/screen-background.tsx
//
// V5 industrial dark teal background for the (tabs) surfaces — port of
// `Ratis_handoff/lib/ratis-real-v4.jsx` lines 25-80 (`ScreenBackground`).
//
// The JSX reference is intentionally minimal (a single flat `#1c2730` div)
// because the V5 design lets each card carry its own gradient/texture. The
// only V5 ambient touches are :
//   - a faint vertical fog gradient (top → bottom, low opacity)
//   - two off-screen color glows (teal + amber) seeping through the cards
//
// We keep the testID surface stable across V4→V5 (image / fog / glows) so
// downstream tests can keep asserting the same anatomy without coupling to
// V4's dashboard background image. The image testID is still emitted but
// renders as a no-op `View` — V5 doesn't ship a base photographic plate.
//
// Hors-V5 surfaces (scan-history, my-info, referral) use the dedicated
// `screen-background-legacy.tsx` companion which preserves the V4 photo +
// tint layering. See `chunk-1-followups.md` § 1.

import React from 'react';
import { View, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

interface ScreenBackgroundProps {
  testID?: string;
}

export function ScreenBackground({ testID }: ScreenBackgroundProps = {}) {
  return (
    <View testID={testID} style={StyleSheet.absoluteFill} pointerEvents="none">
      {/* Layer 1 — base flat teal (matches `#1c2730` from JSX 25-80) */}
      <View testID="screen-bg-image" style={styles.base} />
      {/* Layer 2 — vertical fog (top darker, fades into the base) */}
      <LinearGradient
        testID="screen-bg-fog"
        colors={[
          'rgba(8,14,20,0.55)',
          'rgba(12,22,32,0.30)',
          'rgba(15,28,40,0.10)',
          'transparent',
        ]}
        locations={[0, 0.45, 0.75, 1]}
        style={StyleSheet.absoluteFill}
      />
      {/* Glows */}
      <View testID="screen-bg-glow-teal" style={styles.glowTeal} />
      <View testID="screen-bg-glow-amber" style={styles.glowAmber} />
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    ...StyleSheet.absoluteFillObject,
    // Slightly darker than `Colors.bg` so the cards (which use `Colors.surface`)
    // pop. Matches the JSX root `background: '#1c2730'`.
    backgroundColor: '#1c2730',
  },
  glowTeal: {
    position: 'absolute',
    top: -80,
    right: -80,
    width: 280,
    height: 280,
    borderRadius: 140,
    backgroundColor: 'rgba(77,212,179,0.12)',
    opacity: 0.5,
  },
  glowAmber: {
    position: 'absolute',
    bottom: -60,
    left: -60,
    width: 200,
    height: 200,
    borderRadius: 100,
    backgroundColor: 'rgba(255,184,0,0.08)',
    opacity: 0.5,
  },
});
