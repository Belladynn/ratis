/**
 * Design system SegmentedTabs — Duolingo / Clash Royale pivot (PR4.1).
 *
 * Pill-style segmented control with a sliding indicator (terracotta) that
 * smoothly translates between the active tab. Useful for filtering content
 * (e.g. daily / weekly missions, products / route in `liste`, …).
 *
 * Anatomy :
 *
 *   ┌──────────────────────────────────────┐
 *   │ ╔══════╗ ┌──────┐ ┌──────┐           │
 *   │ ║ Foo ░║ │ Bar  │ │ Baz  │           │   ← rounded outer pill
 *   │ ╚══════╝ └──────┘ └──────┘           │
 *   └──────────────────────────────────────┘
 *
 * Behaviour :
 *   - Single source of truth via `activeId` (controlled). `onChange(id)`
 *     fires on tap of an inactive tab.
 *   - Indicator width is computed from the measured tab width via onLayout —
 *     this lets us support tabs with different label lengths without
 *     pixel-perfect math by the consumer.
 *   - Reanimated worklet : `withTiming` 200ms ease-out for both X and width.
 *   - Haptic Light on tab change (opt-out via `hapticOnPress={false}`).
 *
 * Accessibility :
 *   - Each tab is a `Pressable` with `accessibilityRole="tab"` and
 *     `accessibilityState={{ selected: id === activeId }}`.
 *   - Container is `accessibilityRole="tablist"`.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { Colors, Radii, Spacing } from '@/constants/theme';
import { Durations } from '@/constants/animations';

export type SegmentedTab = {
  id: string;
  label: string;
};

export type SegmentedTabsProps = {
  tabs: SegmentedTab[];
  activeId: string;
  onChange: (id: string) => void;
  hapticOnPress?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function SegmentedTabs({
  tabs,
  activeId,
  onChange,
  hapticOnPress = true,
  testID,
  style,
}: SegmentedTabsProps) {
  // Tab measurements — one entry per tab, captured via onLayout.
  // [{ x, width }] — index aligns with `tabs`.
  const [layouts, setLayouts] = useState<({ x: number; width: number } | null)[]>(
    () => tabs.map(() => null),
  );

  // Reset layouts when the tabs prop reference changes (different list).
  useEffect(() => {
    setLayouts(tabs.map(() => null));
  }, [tabs]);

  // Reanimated values for the indicator.
  const indicatorX = useSharedValue(0);
  const indicatorW = useSharedValue(0);

  // Sync indicator with active tab whenever the layout or activeId changes.
  const activeIndex = tabs.findIndex((t) => t.id === activeId);
  useEffect(() => {
    if (activeIndex < 0) return;
    const layout = layouts[activeIndex];
    if (!layout) return;
    indicatorX.value = withTiming(layout.x, {
      duration: Durations.fast,
      easing: Easing.out(Easing.cubic),
    });
    indicatorW.value = withTiming(layout.width, {
      duration: Durations.fast,
      easing: Easing.out(Easing.cubic),
    });
  }, [activeIndex, layouts, indicatorX, indicatorW]);

  const indicatorStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: indicatorX.value }],
    width: indicatorW.value,
  }));

  const handleTabLayout = useCallback(
    (i: number, e: LayoutChangeEvent) => {
      const { x, width } = e.nativeEvent.layout;
      setLayouts((prev) => {
        const next = [...prev];
        next[i] = { x, width };
        return next;
      });
    },
    [],
  );

  const handleTabPress = useCallback(
    (id: string) => {
      if (id === activeId) return;
      if (hapticOnPress) {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
      }
      onChange(id);
    },
    [activeId, hapticOnPress, onChange],
  );

  return (
    <View
      testID={testID}
      accessibilityRole="tablist"
      style={[styles.container, style]}
    >
      {/* Sliding indicator (rendered behind the tabs). */}
      <Animated.View
        testID={testID ? `${testID}-indicator` : undefined}
        pointerEvents="none"
        style={[styles.indicator, indicatorStyle]}
      />
      {tabs.map((tab, i) => {
        const isActive = tab.id === activeId;
        return (
          <Pressable
            key={tab.id}
            testID={testID ? `${testID}-tab-${tab.id}` : undefined}
            accessibilityRole="tab"
            accessibilityState={{ selected: isActive }}
            onLayout={(e) => handleTabLayout(i, e)}
            onPress={() => handleTabPress(tab.id)}
            style={styles.tab}
          >
            <Text style={[styles.label, isActive && styles.labelActive]}>
              {tab.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: Radii.btn,
    padding: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    position: 'relative',
  },
  indicator: {
    position: 'absolute',
    top: 4,
    bottom: 4,
    backgroundColor: Colors.terracotta,
    borderRadius: Radii.btn - 2,
  },
  tab: {
    paddingHorizontal: Spacing.md,
    paddingVertical: 8,
    flexShrink: 0,
    zIndex: 1,
  },
  label: {
    fontSize: 13,
    fontWeight: '700',
    color: Colors.textSecondary,
    letterSpacing: -0.2,
  },
  labelActive: {
    color: Colors.textPrimary,
    fontWeight: '900',
  },
});

export default SegmentedTabs;
