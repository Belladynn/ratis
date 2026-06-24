// ratis_client/components/dashboard/next-achievement-card.tsx
//
// Compact "Prochain succès" card — port of
// `Ratis_handoff/lib/ratis-achievements-ui.jsx` `function NextAchievementCard`
// (lines 372-434).
//
// Rendered between the hero row and the Battlepass card on the dashboard. The
// achievements data layer is V2 hors-V1 in the ARCH (achievements modal lands
// in chunk 7), so V1 ships a UI-only component that the dashboard can show
// with a single placeholder achievement passed via prop. When the V2 backend
// surfaces real data, swap the prop source — no rewrite needed.
//
// API contract (kept close to the original test in git@01d62ff so we can
// revive those tests with minimal adapter work) :
//   - `achievement` : optional ; the card silently renders nothing if absent
//     (matches the JSX `if (!next) return null;`).
//   - `pickNextAchievement(list)` : exported helper preserving the JSX sort
//     rule (in_progress, sorted by progress / target descending). Useful for
//     consumers passing the full list rather than a pre-picked entry.

import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { Colors, Rarity, Typography } from '@/constants/theme';
import { ProgressBar } from '@/components/design-system';

export type AchievementRarity = 'common' | 'rare' | 'epic' | 'legendary';

export type NextAchievement = {
  id: string;
  label: string;
  rarity: AchievementRarity;
  progress: number;
  target: number;
  status: 'in_progress' | 'unlocked' | 'locked';
  icon?: string;
};

const RARITY_LABEL: Record<AchievementRarity, string> = {
  common: 'Commun',
  rare: 'Rare',
  epic: 'Épique',
  legendary: 'Légendaire',
};

const RARITY_PROGRESS_VARIANT: Record<
  AchievementRarity,
  'gold' | 'cyan' | 'jarPink' | 'terracotta'
> = {
  common: 'terracotta',
  rare: 'cyan',
  epic: 'jarPink',
  legendary: 'gold',
};

/**
 * Pick the in-progress achievement closest to completion. Returns `null`
 * when nothing matches. Mirrors JSX (`useMemo` sort by `progress / target`).
 */
export function pickNextAchievement(
  list: readonly NextAchievement[],
): NextAchievement | null {
  const candidates = list.filter((a) => a.status === 'in_progress');
  if (candidates.length === 0) return null;
  return candidates
    .slice()
    .sort((a, b) => b.progress / b.target - a.progress / a.target)[0];
}

export type NextAchievementCardProps = {
  achievement?: NextAchievement | null;
  onPress?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function NextAchievementCard({
  achievement,
  onPress,
  testID = 'next-achievement-card',
  style,
}: NextAchievementCardProps) {
  if (!achievement) return null;
  const pct = Math.min(1, achievement.progress / Math.max(1, achievement.target));
  const rarityColor = Rarity[achievement.rarity];
  const Body = onPress ? Pressable : View;

  return (
    <Body
      testID={testID}
      onPress={onPress}
      style={[styles.root, { borderColor: `${rarityColor}80` }, style]}
      accessibilityRole={onPress ? 'button' : undefined}
    >
      <LinearGradient
        colors={[`${rarityColor}33`, 'rgba(26,27,38,0.8)']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <View style={styles.row}>
        <View
          style={[
            styles.iconFrame,
            { backgroundColor: `${rarityColor}40` },
          ]}
        >
          <View style={styles.iconInner}>
            <Text style={styles.iconText}>{achievement.icon ?? '🏅'}</Text>
          </View>
        </View>
        <View style={styles.col}>
          <Text style={[styles.miniLabel, { color: rarityColor }]}>
            Prochain succès · {RARITY_LABEL[achievement.rarity]}
          </Text>
          <Text style={styles.title} numberOfLines={1}>
            {achievement.label}
          </Text>
          <ProgressBar
            testID={`${testID}-progress`}
            value={pct}
            variant={RARITY_PROGRESS_VARIANT[achievement.rarity]}
            height={5}
            shimmer={false}
          />
          <Text style={[styles.progressText, { color: rarityColor }]}>
            {Math.floor(achievement.progress)} / {achievement.target}
          </Text>
        </View>
      </View>
    </Body>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'relative',
    overflow: 'hidden',
    borderRadius: 16,
    borderWidth: 1.5,
    padding: 14,
    backgroundColor: 'rgba(26,27,38,0.6)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 14,
    elevation: 4,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  iconFrame: {
    width: 56,
    height: 56,
    borderRadius: 12,
    padding: 2,
  },
  iconInner: {
    flex: 1,
    borderRadius: 10,
    backgroundColor: '#1A1B26',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconText: {
    fontSize: 28,
    color: Colors.textPrimary,
  },
  col: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'column',
    gap: 4,
  },
  miniLabel: {
    ...Typography.label,
    fontSize: 9,
    letterSpacing: 0.6,
  },
  title: {
    fontFamily: 'Inter_900Black',
    fontSize: 14,
    color: Colors.textPrimary,
    letterSpacing: -0.2,
  },
  progressText: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 10,
  },
});

export default NextAchievementCard;
