// ratis_client/components/profil/achievement-card.tsx
//
// Single trading-card-style achievement tile — port of
// `Ratis_handoff/lib/ratis-achievements-ui.jsx` `function AchievementCard`
// (lines 7-138).
//
// Visual contract :
//   - aspect ratio 3/4
//   - rarity-based metallic frame (LinearGradient ; outer 2px frame +
//     inner radial-ish surface). For unlocked tiers we use the rarity's
//     `metal` gradient ; for locked tiers a neutral dark gradient.
//   - Holographic shine sweep (`achHoloShine`, 4.5s linear infinite) ONLY
//     on unlocked rare+ tiers (`r.holo === true`). Implemented via a
//     LinearGradient overlay translated horizontally (`-100% → +100%`) on
//     a `withRepeat` shared value. **Cleanup mandatory** : `cancelAnimation`
//     in the `useEffect` cleanup callback (chunk 3 discipline).
//   - Locked tiles : grayscale + reduced opacity on the icon only
//     (`<Image>`-style `tintColor` is not portable on emoji ; we do
//     `opacity: 0.4` which preserves the shape recognition).
//   - In-progress tiles : show a slim progress bar at the bottom for the
//     low tiers (terracotta, bronze, copper) so users see the next
//     milestone without opening the detail modal.
//
// Skia is NOT needed here — the metallic frame is expressible as a
// LinearGradient and the shine is a simple translated gradient. Keeping
// this RN-pure makes the grid cheap to render at 3 columns × N rows.
//
// Token derogation : rarity hex literals come from the JSX iso. Values
// like `aspect 3/4`, `radius 12`, `padding 2` are kept verbatim.

import React, { useEffect } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';

import {
  RARITIES,
  CATEGORIES,
  type Achievement,
  type RarityKey,
} from '@/components/profil/achievements-data';

const LOW_TIERS: readonly RarityKey[] = ['terracotta', 'bronze', 'copper'];

export type AchievementCardProps = {
  achievement: Achievement;
  onPress?: (a: Achievement) => void;
  testID?: string;
};

/**
 * Holo shine sweep — translucent gradient that travels across the card
 * surface every 4.5s. Mounted only on unlocked rare+ tiles.
 *
 * Reanimated discipline : the shared value drives a `withRepeat(-1)`
 * loop that we MUST cancel on unmount, otherwise the worklet keeps ticking
 * after the parent (modal) closes.
 */
function HoloShine({ color }: { color: string }) {
  const progress = useSharedValue(0);
  useEffect(() => {
    progress.value = withRepeat(
      withTiming(1, { duration: 4500, easing: Easing.inOut(Easing.ease) }),
      -1,
      false,
    );
    return () => {
      cancelAnimation(progress);
    };
  }, [progress]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ translateX: `${-100 + progress.value * 200}%` }],
  }));
  return (
    <Animated.View pointerEvents="none" style={[styles.holoWrap, animated]}>
      <LinearGradient
        colors={[
          'rgba(255,255,255,0)',
          `${color}33`,
          'rgba(255,255,255,0.30)',
          `${color}33`,
          'rgba(255,255,255,0)',
        ]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 0 }}
        style={StyleSheet.absoluteFill}
      />
    </Animated.View>
  );
}

/**
 * Burst-rays rotation — subtle ring rotating behind the icon for diamond
 * tiles. Same cancelAnimation discipline as `HoloShine`.
 *
 * The JSX uses a conic-gradient ; RN doesn't support conic. We render a
 * border ring with a soft translucent rotation overlay — same "sense of
 * motion" without the conic-specific look.
 */
function BurstRays({ color }: { color: string }) {
  const rotation = useSharedValue(0);
  useEffect(() => {
    rotation.value = withRepeat(
      withTiming(360, { duration: 8000, easing: Easing.linear }),
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
  return (
    <Animated.View
      pointerEvents="none"
      style={[styles.burstRays, { borderColor: `${color}40` }, animated]}
    />
  );
}

export function AchievementCard({
  achievement,
  onPress,
  testID,
}: AchievementCardProps) {
  const r = RARITIES[achievement.rarity];
  const cat = CATEGORIES[achievement.category];
  const isLocked = achievement.status === 'locked';
  const isInProgress = achievement.status === 'in_progress';
  const isUnlocked = achievement.status === 'unlocked';
  const isSecret = achievement.category === 'secret' && isLocked;
  const showProgress = isInProgress && LOW_TIERS.includes(achievement.rarity);
  const pct =
    achievement.target > 0
      ? Math.min(100, (achievement.progress / achievement.target) * 100)
      : 0;
  const isLegendary =
    achievement.rarity === 'diamond' || achievement.rarity === 'crystal';

  // Bug 4 (PO ticket 2026-05-12) — tier colours (terre cuite / bronze /
  // cuivre / argent / or / ...) must be visible on EVERY card, not just on
  // unlocked ones. Previously locked tiles defaulted to a neutral grey
  // gradient (`#1F2937` → `#111827`), which made the catalogue look
  // monotone before any unlock. Locked tiles now use the rarity's metal
  // gradient too, with reduced opacity on the frame to signal « not yet ».
  const frameColors: readonly [string, string, ...string[]] = r.metal;

  return (
    <Pressable
      testID={testID ?? `achievement-card-${achievement.id}`}
      accessibilityRole="button"
      accessibilityLabel={isSecret ? '???' : achievement.label}
      onPress={() => onPress?.(achievement)}
      style={({ pressed }) => [
        styles.root,
        isUnlocked
          ? { shadowColor: r.color, shadowOpacity: 0.35 }
          : null,
        pressed && styles.pressed,
      ]}
    >
      <LinearGradient
        colors={frameColors}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.frame, isLocked && styles.frameLocked]}
      >
        <View
          style={[
            styles.body,
            { backgroundColor: '#1A1B26' },
          ]}
        >
          {/* Soft rarity glow (radial-ish via vertical gradient).
              Bug 4 — locked tiles also surface their tier glow, dimmer so
              they read as « not yet » without erasing the colour cue. */}
          <LinearGradient
            colors={[isUnlocked ? r.glow : r.glow.replace(/[\d.]+\)$/, '0.10)'), '#1A1B26']}
            start={{ x: 0.5, y: 0 }}
            end={{ x: 0.5, y: 0.6 }}
            style={StyleSheet.absoluteFill}
          />

          {/* Burst rays — legendary tiles only */}
          {isUnlocked && isLegendary ? <BurstRays color={r.color} /> : null}

          {/* Holographic shine — unlocked rare+ tiles only */}
          {isUnlocked && r.holo ? <HoloShine color={r.color} /> : null}

          {/* Rarity ribbon — Bug 4 : the tier label ("TERRE CUITE", "BRONZE"
              etc.) now reads visibly even when locked, using the tier's
              accent colour at half opacity. */}
          <View
            style={[
              styles.ribbon,
              {
                borderBottomColor: isUnlocked
                  ? r.color
                  : `${r.color}40`,
                backgroundColor: isUnlocked
                  ? `${r.color}22`
                  : `${r.color}10`,
              },
            ]}
          >
            <Text
              style={[
                styles.ribbonText,
                { color: isUnlocked ? '#fff' : 'rgba(255,255,255,0.65)' },
              ]}
              numberOfLines={1}
            >
              {r.label}
            </Text>
            <Text style={styles.ribbonIcon}>{cat.icon}</Text>
          </View>

          {/* Icon */}
          <View style={styles.iconWrap}>
            <Text
              style={[
                styles.icon,
                isLocked && styles.iconLocked,
                isUnlocked
                  ? { textShadowColor: r.glow, textShadowRadius: 14 }
                  : null,
              ]}
            >
              {isSecret ? '🔒' : achievement.icon}
            </Text>
          </View>

          {/* Title */}
          <View
            style={[
              styles.titleBar,
              {
                backgroundColor: isUnlocked
                  ? 'rgba(0,0,0,0.45)'
                  : 'rgba(0,0,0,0.30)',
                borderTopColor: isUnlocked
                  ? `${r.color}80`
                  : 'rgba(255,255,255,0.06)',
              },
            ]}
          >
            <Text
              style={[
                styles.title,
                {
                  color: isUnlocked
                    ? '#fff'
                    : isLocked
                      ? 'rgba(255,255,255,0.45)'
                      : 'rgba(255,255,255,0.85)',
                },
              ]}
              numberOfLines={2}
            >
              {isSecret ? '???' : achievement.label}
            </Text>
            {showProgress ? (
              <View style={styles.progressWrap}>
                <View style={styles.progressTrack}>
                  <View
                    style={[
                      styles.progressFill,
                      { width: `${pct}%`, backgroundColor: r.color },
                    ]}
                  />
                </View>
                <Text
                  style={[styles.progressText, { color: r.color }]}
                >
                  {Math.floor(achievement.progress)}/{achievement.target}
                </Text>
              </View>
            ) : null}
          </View>
        </View>
      </LinearGradient>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: {
    aspectRatio: 3 / 4,
    borderRadius: 12,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.5,
    shadowRadius: 10,
    elevation: 4,
  },
  pressed: {
    opacity: 0.85,
  },
  frame: {
    flex: 1,
    padding: 2,
    borderRadius: 12,
  },
  frameLocked: {
    // Bug 4 — locked tiles show their tier colour but at a reduced
    // intensity so the unlock state is still legible at a glance.
    opacity: 0.72,
  },
  body: {
    flex: 1,
    borderRadius: 10,
    overflow: 'hidden',
    position: 'relative',
  },
  ribbon: {
    paddingHorizontal: 6,
    paddingVertical: 4,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
  },
  ribbonText: {
    fontSize: 7,
    fontWeight: '900',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    flex: 1,
  },
  ribbonIcon: {
    fontSize: 8,
    opacity: 0.7,
    marginLeft: 4,
  },
  iconWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 6,
  },
  icon: {
    fontSize: 36,
  },
  iconLocked: {
    opacity: 0.4,
  },
  titleBar: {
    paddingHorizontal: 6,
    paddingTop: 4,
    paddingBottom: 6,
    borderTopWidth: 1,
  },
  title: {
    fontSize: 9,
    fontWeight: '900',
    letterSpacing: 0.2,
    lineHeight: 11,
    textAlign: 'center',
    minHeight: 22,
  },
  progressWrap: {
    marginTop: 4,
  },
  progressTrack: {
    height: 3,
    borderRadius: 2,
    backgroundColor: 'rgba(255,255,255,0.1)',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
  },
  progressText: {
    fontSize: 7,
    fontWeight: '800',
    letterSpacing: 0.3,
    marginTop: 2,
    textAlign: 'center',
  },
  holoWrap: {
    ...StyleSheet.absoluteFillObject,
  },
  burstRays: {
    position: 'absolute',
    top: '30%',
    left: '20%',
    width: '60%',
    height: '50%',
    borderRadius: 999,
    borderWidth: 8,
  },
});

export default AchievementCard;
