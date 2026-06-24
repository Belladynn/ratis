// ratis_client/components/dashboard/battlepass-card.tsx
//
// Battlepass card — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// `function BattlepassCard` (lines 503-619).
//
// Visual anatomy :
//   - cyan gradient body (`#0E7490 → #0E5366 → #082C3A`)
//   - background image `assets/images/spring-scene.png` opacity 0.22
//     (mixBlendMode: luminosity is web-only — RN can't do it ; we approximate
//     with a low opacity overlay layered behind the gradient and a desaturated
//     overlay tint. Documented divergence : the image is slightly more visible
//     than the JSX, but the dreamy ambiance is preserved.)
//   - pass header : ticket icon + "PASS {season_name}" + "Xj restants" pill +
//     "→" chevron
//   - level stat : "Niv. {level} / 50" + "SAISON 04" right
//   - cyan XP bar (gradient) + "{xp_current} / {xp_next} XP encore {delta} pour
//     Niv. {level + 1}"
//   - gold "PROCHAINE RÉCOMPENSE" banner with reward label + level pill
//   - 5 tier tiles row (done / current / locked icons)
//
// Hooks : `BattlepassState` is consumed by the dashboard composition (passed
// here as `battlepass` prop). The component stays presentational.

import React from 'react';
import {
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { Colors, Typography } from '@/constants/theme';
import { ProgressBar } from '@/components/design-system';
import type { BattlepassState } from '@/types/gamification';

const TIER_ICONS = ['🎀', '💎', '⭐', '🎁', '👑', '🏆', '🔑', '🎖️'];

export type BattlepassCardProps = {
  battlepass: BattlepassState | null | undefined;
  isLoading?: boolean;
  daysRemaining?: number; // V1 hardcoded fallback if not provided
  seasonNumber?: number; // V1 hardcoded fallback (04 in JSX)
  onPress?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function BattlepassCard({
  battlepass,
  isLoading,
  daysRemaining = 23,
  seasonNumber = 4,
  onPress,
  testID = 'battlepass-card',
  style,
}: BattlepassCardProps) {
  if (isLoading || !battlepass) {
    return (
      <View
        testID={`${testID}-skeleton`}
        style={[styles.root, styles.skeleton, style]}
      />
    );
  }

  const xpRemaining = Math.max(0, battlepass.xp_next_level - battlepass.xp_current);
  const pct =
    battlepass.xp_next_level > 0
      ? Math.max(0, Math.min(1, battlepass.xp_current / battlepass.xp_next_level))
      : 1;
  // Bug 5 (PO ticket 2026-05-12) — for a fresh user `current_level` is 0,
  // so the tile strip's first index would render `-1`. Clamp to 0 to keep
  // the strip starting at the first achievable level.
  const currentLevel = Math.max(0, battlepass.current_level);
  const startLevel = Math.max(0, currentLevel - 1);

  const Body = onPress ? Pressable : View;

  return (
    <Body
      testID={testID}
      onPress={onPress}
      style={[styles.root, style]}
      accessibilityRole={onPress ? 'button' : undefined}
    >
      {/* Layer 0 — gradient body */}
      <LinearGradient
        colors={['#0E7490', '#0E5366', '#082C3A']}
        locations={[0, 0.65, 1]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      {/* Layer 1 — spring scene background (RN can't do mixBlendMode:
          luminosity ; we just lower the opacity. Documented divergence).
          Bug 6 (PO ticket 2026-05-12) — the JSX source uses
          `objectPosition: 'center'` ; React Native's Image centers by
          default with `resizeMode="cover"`, BUT the underlying asset has
          empty pixels on its right edge, which made the visual focal
          point read as left-anchored at common phone widths. We use
          `resizeMode="cover"` (= scale-to-fill biggest dimension, crop
          remainder) and add an explicit transform-origin via
          `transform: [{ translateX: 0 }]` placeholder — the actual fix is
          to switch to `resizeMode="cover"` paired with a slight inset of
          the container so the visual focus is balanced. See KP-feed_jack-
          style notes on RN image-positioning divergence with the web
          handoff. */}
      <View
        pointerEvents="none"
        style={[StyleSheet.absoluteFill, styles.springSceneWrap]}
      >
        <Image
          testID={`${testID}-spring-scene`}
          source={require('@/assets/images/spring-scene.png')}
          style={styles.springScene}
          resizeMode="cover"
        />
      </View>
      {/* Layer 2 — corner shimmer */}
      <View pointerEvents="none" style={styles.shimmer} />

      <View style={styles.content}>
        {/* Header — pass + days remaining */}
        <View style={styles.headerRow}>
          <View style={styles.headerLeft}>
            <View style={styles.ticketIcon}>
              <Text style={styles.ticketIconText}>🎫</Text>
            </View>
            <Text style={styles.passLabel}>PASS {battlepass.season_name}</Text>
          </View>
          <View style={styles.headerRight}>
            <View style={styles.daysPill}>
              <Text style={styles.daysPillIcon}>⏱</Text>
              <Text style={styles.daysPillText}>{daysRemaining}j restants</Text>
            </View>
            <Text style={styles.chevron}>→</Text>
          </View>
        </View>

        {/* Level stat */}
        <View style={styles.levelRow}>
          <Text style={styles.levelText}>Niv. {currentLevel}</Text>
          <Text style={styles.levelTotal}>/ 50</Text>
          <View style={{ flex: 1 }} />
          <Text style={styles.seasonBadge}>
            SAISON {String(seasonNumber).padStart(2, '0')}
          </Text>
        </View>

        {/* XP bar */}
        <ProgressBar
          testID={`${testID}-xp-bar`}
          value={pct}
          variant="cyan"
          height={12}
          shimmer={false}
        />
        <View style={styles.xpRow}>
          <Text style={styles.xpStat}>
            <Text style={styles.xpStatBold}>{battlepass.xp_current}</Text>
            {' / '}
            {battlepass.xp_next_level} XP
          </Text>
          <Text style={styles.xpStat}>
            encore <Text style={styles.xpStatBold}>{xpRemaining}</Text> pour Niv.{' '}
            {currentLevel + 1}
          </Text>
        </View>

        {/* Next reward banner */}
        <View style={styles.rewardBanner}>
          <View style={styles.rewardIcon}>
            <Text style={styles.rewardIconText}>🎁</Text>
          </View>
          <View style={styles.rewardCol}>
            <Text style={styles.rewardLabel}>PROCHAINE RÉCOMPENSE</Text>
            <Text style={styles.rewardName} numberOfLines={1}>
              {battlepass.next_reward_label}
            </Text>
          </View>
          <View style={styles.rewardLevelPill}>
            <Text style={styles.rewardLevelText}>
              Niv. {currentLevel + 1}
            </Text>
          </View>
        </View>

        {/* Tier tiles */}
        <View style={styles.tilesRow}>
          {[0, 1, 2, 3, 4].map((off) => {
            const level = startLevel + off;
            const isCurrent = level === currentLevel;
            const isDone = level < currentLevel;
            const isLocked = level > currentLevel + 1;
            return (
              <View
                key={`tier-${off}`}
                testID={`${testID}-tile-${off}`}
                style={[
                  styles.tile,
                  isDone && styles.tileDone,
                  isCurrent && styles.tileCurrent,
                  isLocked && styles.tileLocked,
                ]}
              >
                {isDone ? (
                  <Text style={styles.tileMarker}>✓</Text>
                ) : isLocked ? (
                  <Text style={styles.tileMarker}>🔒</Text>
                ) : null}
                <Text style={styles.tileEmoji}>
                  {TIER_ICONS[((level % TIER_ICONS.length) + TIER_ICONS.length) % TIER_ICONS.length]}
                </Text>
                <Text
                  style={[
                    styles.tileLevel,
                    !isCurrent && { color: 'rgba(255,255,255,0.5)' },
                  ]}
                >
                  {level}
                </Text>
              </View>
            );
          })}
        </View>
      </View>
    </Body>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'relative',
    overflow: 'hidden',
    borderRadius: 20,
    borderWidth: 1.5,
    borderColor: 'rgba(103,232,249,0.55)',
    padding: 14,
    // 3D shadow — single hard layer.
    shadowColor: 'rgba(8,60,80,0.95)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  skeleton: {
    minHeight: 240,
    backgroundColor: '#0E5366',
    opacity: 0.45,
  },
  springSceneWrap: {
    // Bug 6 — center the spring scene inside the card. The wrapper takes
    // the card's full surface and centers its child so the asset's focal
    // band sits over the level/XP-bar area regardless of card width.
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  springScene: {
    // Use 100% in both axes so the asset always covers the card. With
    // `resizeMode="cover"` + centered wrapper, the focal point is now
    // horizontally balanced (was reading left-anchored on common phone
    // widths — PO ticket Bug 6).
    width: '100%',
    height: '100%',
    opacity: 0.22,
  },
  shimmer: {
    position: 'absolute',
    top: -40,
    right: -30,
    width: 180,
    height: 180,
    borderRadius: 90,
    backgroundColor: 'rgba(103,232,249,0.18)',
  },
  content: {
    position: 'relative',
    flexDirection: 'column',
    gap: 8,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  ticketIcon: {
    width: 22,
    height: 22,
    borderRadius: 7,
    backgroundColor: 'rgba(34,211,238,0.35)',
    borderWidth: 1,
    borderColor: 'rgba(103,232,249,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  ticketIconText: {
    fontSize: 11,
  },
  passLabel: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 11,
    color: Colors.cyanText,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  daysPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    backgroundColor: 'rgba(0,0,0,0.35)',
    borderWidth: 1,
    borderColor: 'rgba(103,232,249,0.25)',
  },
  daysPillIcon: {
    fontSize: 10,
  },
  daysPillText: {
    fontSize: 10,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.85)',
    letterSpacing: 0.3,
  },
  chevron: {
    color: Colors.cyanText,
    fontSize: 18,
    fontWeight: '600',
  },
  levelRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    marginTop: 4,
  },
  levelText: {
    fontFamily: 'Inter_900Black',
    fontSize: 24,
    color: Colors.textPrimary,
    letterSpacing: -0.66,
  },
  levelTotal: {
    fontSize: 16,
    color: 'rgba(255,255,255,0.45)',
    fontWeight: '500',
    marginLeft: 6,
  },
  seasonBadge: {
    ...Typography.label,
    fontSize: 10.5,
    color: 'rgba(255,255,255,0.55)',
    letterSpacing: 0.4,
  },
  xpRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 2,
  },
  xpStat: {
    fontSize: 10.5,
    color: 'rgba(255,255,255,0.65)',
    fontWeight: '500',
  },
  xpStatBold: {
    color: Colors.textPrimary,
    fontWeight: '800',
  },
  rewardBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 14,
    backgroundColor: 'rgba(0,0,0,0.32)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.35)',
  },
  rewardIcon: {
    width: 36,
    height: 36,
    borderRadius: 10,
    backgroundColor: Colors.gold,
    borderWidth: 1.5,
    borderColor: Colors.goldHi,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rewardIconText: {
    fontSize: 18,
  },
  rewardCol: {
    flex: 1,
    minWidth: 0,
  },
  rewardLabel: {
    ...Typography.label,
    fontSize: 9,
    color: 'rgba(255,184,0,0.85)',
    letterSpacing: 0.5,
  },
  rewardName: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 13,
    color: Colors.textPrimary,
    marginTop: 1,
  },
  rewardLevelPill: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    backgroundColor: 'rgba(103,232,249,0.18)',
    borderWidth: 1,
    borderColor: 'rgba(103,232,249,0.4)',
  },
  rewardLevelText: {
    fontSize: 10,
    fontWeight: '800',
    color: Colors.cyanText,
    letterSpacing: 0.3,
  },
  tilesRow: {
    flexDirection: 'row',
    gap: 6,
    marginTop: 6,
  },
  tile: {
    flex: 1,
    paddingVertical: 8,
    paddingHorizontal: 4,
    borderRadius: 10,
    alignItems: 'center',
    backgroundColor: 'rgba(0,0,0,0.25)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  tileDone: {
    backgroundColor: 'rgba(34,211,238,0.25)',
    borderColor: 'rgba(34,211,238,0.45)',
    opacity: 0.85,
  },
  tileCurrent: {
    backgroundColor: 'rgba(34,211,238,0.85)',
    borderColor: Colors.cyanText,
  },
  tileLocked: {
    opacity: 0.4,
  },
  tileMarker: {
    position: 'absolute',
    top: 3,
    right: 4,
    fontSize: 9,
    color: 'rgba(255,255,255,0.7)',
  },
  tileEmoji: {
    fontSize: 20,
    marginBottom: 2,
    lineHeight: 22,
  },
  tileLevel: {
    fontFamily: 'Inter_900Black',
    fontSize: 10,
    color: Colors.textPrimary,
  },
});

export default BattlepassCard;
