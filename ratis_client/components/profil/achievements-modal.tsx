// ratis_client/components/profil/achievements-modal.tsx
//
// Full-screen "Succès · Collection" modal — port of
// `Ratis_handoff/lib/ratis-achievements-ui.jsx` `function AchievementsModal`
// (lines 226-336).
//
// This is NOT a bottom sheet (the design-system `<Modal />` primitive only
// covers the bottom-sheet variant). The Achievements modal is a full-screen
// surface with its own gradient background — it slides in from the right
// (Reanimated `entering={SlideInRight}`) and slides out the same way on
// dismiss. `zIndex: 20` matches the JSX iso.
//
// Anatomy :
//   - Gradient bg `#0a0d14 → #1a242c` (linear 180deg)
//   - Header band : eyebrow "COLLECTION" + title "Succès" + × close button
//   - 3 stat pills (Débloqués X/Y · En cours · Score %)
//   - Status filter tabs (Tous / Débloqués / En cours / À faire) — tab
//     border bottom with terracotta indicator on the active one.
//   - Category chip filter row (horizontal scroll) — Toutes ✨ + 7
//     categories.
//   - Grid 3 columns of `<AchievementCard>` (or empty-state copy).
//
// V1 data : reads from `ACHIEVEMENTS` const in `achievements-data.ts` (no
// hook ; the achievements backend lands V2). Exported as `defaultAchievements`
// fallback so the consumer can override for tests / future hook wiring.

import React, { useEffect, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors, Typography } from '@/constants/theme';
import { Durations } from '@/constants/animations';
import {
  ACHIEVEMENTS as DEFAULT_ACHIEVEMENTS,
  CATEGORIES,
  type Achievement,
  type AchievementStatus,
  type CategoryKey,
} from '@/components/profil/achievements-data';
import { AchievementCard } from '@/components/profil/achievement-card';

type StatusFilter = 'all' | AchievementStatus;
type CategoryFilter = 'all' | CategoryKey;

const STATUS_TABS: readonly (readonly [StatusFilter, string])[] = [
  ['all', 'Tous'],
  ['unlocked', 'Débloqués'],
  ['in_progress', 'En cours'],
  ['locked', 'À faire'],
];

export type AchievementsModalProps = {
  open: boolean;
  onClose: () => void;
  /** Override the default V1 placeholder collection (testing / V2 hook wiring). */
  achievements?: readonly Achievement[];
  testID?: string;
};

function StatPill({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <View style={styles.pill}>
      <Text style={styles.pillLabel}>{label}</Text>
      <Text style={[styles.pillValue, { color }]}>{value}</Text>
    </View>
  );
}

function CategoryChip({
  active,
  icon,
  label,
  color,
  onPress,
  testID,
}: {
  active: boolean;
  icon: string;
  label: string;
  color: string;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={[
        styles.chip,
        {
          borderColor: active ? color : 'rgba(255,255,255,0.08)',
          backgroundColor: active ? `${color}25` : 'rgba(255,255,255,0.04)',
        },
      ]}
    >
      <Text style={styles.chipIcon}>{icon}</Text>
      <Text
        style={[
          styles.chipLabel,
          { color: active ? color : 'rgba(255,255,255,0.6)' },
        ]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function AchievementsModal({
  open,
  onClose,
  achievements = DEFAULT_ACHIEVEMENTS,
  testID = 'achievements-modal',
}: AchievementsModalProps) {
  const insets = useSafeAreaInsets();
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [category, setCategory] = useState<CategoryFilter>('all');

  // Slide-in animation. Translates from full width on the right to 0 on
  // open ; reverses on close. We do NOT use `withRepeat` here so no
  // `cancelAnimation` cleanup is needed.
  const translateX = useSharedValue(1);
  useEffect(() => {
    translateX.value = withTiming(open ? 0 : 1, {
      duration: Durations.normal,
      easing: Easing.out(Easing.cubic),
    });
  }, [open, translateX]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ translateX: `${translateX.value * 100}%` }],
  }));

  const filtered = useMemo(() => {
    return achievements.filter((a) => {
      if (filter !== 'all' && a.status !== filter) return false;
      if (category !== 'all' && a.category !== category) return false;
      return true;
    });
  }, [achievements, filter, category]);

  const stats = useMemo(() => {
    const total = achievements.length;
    const unlocked = achievements.filter((a) => a.status === 'unlocked').length;
    const inProgress = achievements.filter(
      (a) => a.status === 'in_progress',
    ).length;
    return { total, unlocked, inProgress };
  }, [achievements]);

  const scorePct =
    stats.total > 0 ? Math.round((stats.unlocked / stats.total) * 100) : 0;

  // Mount-only when open OR animating. We keep mounted while animating
  // out by tracking translateX directly (animatedStyle) — when it reaches
  // 1 again with `open=false`, the View remains but is fully off-screen.
  if (!open && translateX.value === 1) {
    return null;
  }

  return (
    <Animated.View
      testID={testID}
      pointerEvents={open ? 'auto' : 'none'}
      style={[styles.root, animated, { paddingTop: insets.top }]}
    >
      <LinearGradient
        colors={['#0a0d14', '#1a242c']}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 1 }}
        style={StyleSheet.absoluteFill}
      />

      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerTop}>
          <View style={styles.headerTitleCol}>
            <Text style={styles.eyebrow}>Collection</Text>
            <Text style={styles.title}>Succès</Text>
          </View>
          <Pressable
            testID={`${testID}-close`}
            accessibilityRole="button"
            accessibilityLabel="Fermer"
            onPress={onClose}
            hitSlop={8}
            style={styles.closeBtn}
          >
            <Text style={styles.closeIcon}>✕</Text>
          </Pressable>
        </View>

        {/* Stat pills */}
        <View style={styles.pillsRow}>
          <StatPill
            label="Débloqués"
            value={`${stats.unlocked}/${stats.total}`}
            color="#34D399"
          />
          <StatPill label="En cours" value={`${stats.inProgress}`} color="#60A5FA" />
          <StatPill label="Score" value={`${scorePct}%`} color="#FBBF24" />
        </View>

        {/* Status tabs */}
        <View style={styles.statusTabs}>
          {STATUS_TABS.map(([key, lbl]) => {
            const active = filter === key;
            return (
              <Pressable
                key={key}
                testID={`${testID}-status-${key}`}
                accessibilityRole="button"
                accessibilityLabel={lbl}
                accessibilityState={{ selected: active }}
                onPress={() => setFilter(key)}
                style={[
                  styles.statusTab,
                  active && styles.statusTabActive,
                ]}
              >
                <Text
                  style={[
                    styles.statusTabText,
                    {
                      color: active ? '#fff' : 'rgba(255,255,255,0.45)',
                    },
                  ]}
                >
                  {lbl}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Category chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipsRow}
        >
          <CategoryChip
            active={category === 'all'}
            icon="✨"
            label="Toutes"
            color="#fff"
            onPress={() => setCategory('all')}
            testID={`${testID}-cat-all`}
          />
          {(Object.entries(CATEGORIES) as [CategoryKey, typeof CATEGORIES[CategoryKey]][]).map(
            ([key, c]) => (
              <CategoryChip
                key={key}
                active={category === key}
                icon={c.icon}
                label={c.label}
                color={c.color}
                onPress={() => setCategory(key)}
                testID={`${testID}-cat-${key}`}
              />
            ),
          )}
        </ScrollView>
      </View>

      {/* Grid */}
      <ScrollView
        style={styles.gridScroll}
        contentContainerStyle={[
          styles.gridContent,
          { paddingBottom: insets.bottom + 24 },
        ]}
      >
        {filtered.length === 0 ? (
          <Text style={styles.empty}>
            Aucun succès dans cette section pour l&apos;instant.
          </Text>
        ) : (
          <View style={styles.grid}>
            {filtered.map((a) => (
              <View key={a.id} style={styles.gridCell}>
                <AchievementCard achievement={a} />
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 20,
    backgroundColor: Colors.bg,
  },
  header: {
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(192,132,252,0.05)',
  },
  headerTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  headerTitleCol: {
    flexShrink: 1,
  },
  eyebrow: {
    ...Typography.label,
    fontSize: 9,
    color: 'rgba(192,132,252,0.85)',
    letterSpacing: 0.8,
  },
  title: {
    ...Typography.hero,
    color: Colors.textPrimary,
    marginTop: 2,
  },
  closeBtn: {
    width: 30,
    height: 30,
    borderRadius: 15,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.15)',
    backgroundColor: 'rgba(255,255,255,0.06)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeIcon: {
    color: Colors.textPrimary,
    fontSize: 14,
    fontWeight: '700',
  },
  pillsRow: {
    flexDirection: 'row',
    gap: 6,
    marginBottom: 12,
  },
  pill: {
    flex: 1,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
  },
  pillLabel: {
    fontSize: 8,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.45)',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  pillValue: {
    fontSize: 14,
    fontWeight: '900',
    marginTop: 2,
  },
  statusTabs: {
    flexDirection: 'row',
    marginBottom: 8,
    borderBottomWidth: 2,
    borderBottomColor: 'rgba(255,255,255,0.08)',
  },
  statusTab: {
    flex: 1,
    paddingHorizontal: 4,
    paddingVertical: 6,
    paddingBottom: 8,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
    marginBottom: -2,
    alignItems: 'center',
  },
  statusTabActive: {
    borderBottomColor: Colors.terracotta,
  },
  statusTabText: {
    fontSize: 10,
    fontWeight: '900',
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
  chipsRow: {
    flexDirection: 'row',
    gap: 5,
    paddingBottom: 2,
    paddingRight: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    borderWidth: 1,
  },
  chipIcon: {
    fontSize: 11,
  },
  chipLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  gridScroll: {
    flex: 1,
  },
  gridContent: {
    padding: 12,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  gridCell: {
    // 3 columns minus gap. ScrollView contentContainer width = screen - 24.
    // We use `flexBasis` percentages so the grid stays responsive.
    width: '32%',
  },
  empty: {
    textAlign: 'center',
    paddingVertical: 40,
    color: 'rgba(255,255,255,0.4)',
    fontSize: 13,
  },
});

export default AchievementsModal;
