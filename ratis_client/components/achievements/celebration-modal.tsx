// ratis_client/components/achievements/celebration-modal.tsx
//
// Achievements V1 — full-screen celebration modal (PR 8/8).
//
// Triggered for `rarity ∈ {emerald, sapphire, ruby, crystal, diamond}` AFTER
// the toast has dismissed (orchestrated by
// `services/achievement-notification-handler.ts`). Diamants with a registered
// bespoke component bypass this modal entirely (cf
// `bespoke-animations/index.ts`) — this is the generic "PS-Trophy"-style
// fallback for the other rare tiers.
//
// Anatomy :
//   - Tappable backdrop (rgba(0,0,0,0.85) + radial-ish glow)
//   - Centered metallic frame card with the achievement icon zoom-in,
//     label, description, +CAB pill
//   - Close button (top-right ✕) + tap-anywhere backdrop dismiss
//
// Animation : icon scale-in (0 → 1.1 → 1.0 cubic-bezier overshoot), card
// translateY/opacity slide-in. Reanimated cleanup mandatory (cf
// achievement-card.tsx).

import React, { useEffect } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import {
  RARITIES,
  type RarityKey,
} from '@/components/profil/achievements-data';
import type {
  AchievementRarity,
  AchievementUnlockedPayload,
} from '@/types/achievements';

/**
 * Rarities that trigger the celebration modal (= the spec's "Émeraude+" tier
 * and above). Bronze / Silver / Gold get the toast only.
 */
const MODAL_RARITIES: ReadonlySet<AchievementRarity> = new Set([
  'emerald',
  'sapphire',
  'ruby',
  'crystal',
  'diamond',
]);

export type AchievementCelebrationModalProps = {
  payload: AchievementUnlockedPayload | null;
  onDismiss: () => void;
  testID?: string;
};

export function AchievementCelebrationModal({
  payload,
  onDismiss,
  testID,
}: AchievementCelebrationModalProps) {
  const insets = useSafeAreaInsets();
  const cardOpacity = useSharedValue(0);
  const cardTranslateY = useSharedValue(30);
  const iconScale = useSharedValue(0);

  useEffect(() => {
    if (!payload) return;
    cardOpacity.value = 0;
    cardTranslateY.value = 30;
    iconScale.value = 0;

    cardOpacity.value = withTiming(1, { duration: 250 });
    cardTranslateY.value = withTiming(0, {
      duration: 350,
      easing: Easing.out(Easing.cubic),
    });
    iconScale.value = withSequence(
      withTiming(1.15, {
        duration: 280,
        easing: Easing.out(Easing.back(1.5)),
      }),
      withTiming(1, { duration: 180, easing: Easing.out(Easing.cubic) }),
    );

    return () => {
      cancelAnimation(cardOpacity);
      cancelAnimation(cardTranslateY);
      cancelAnimation(iconScale);
    };
  }, [payload, cardOpacity, cardTranslateY, iconScale]);

  const cardAnimated = useAnimatedStyle(() => ({
    opacity: cardOpacity.value,
    transform: [{ translateY: cardTranslateY.value }],
  }));
  const iconAnimated = useAnimatedStyle(() => ({
    transform: [{ scale: iconScale.value }],
  }));

  if (!payload) return null;
  if (!MODAL_RARITIES.has(payload.rarity)) return null;

  const r = RARITIES[payload.rarity as RarityKey];
  const rootTestID = testID ?? 'achievement-celebration-modal';

  return (
    <View
      testID={rootTestID}
      style={[styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom }]}
      pointerEvents="auto"
    >
      <Pressable
        testID={`${rootTestID.replace('-modal', '')}-backdrop`}
        onPress={onDismiss}
        style={StyleSheet.absoluteFill}
        accessibilityRole="button"
        accessibilityLabel="Fermer"
      >
        <LinearGradient
          colors={[`${r.glow}`, '#000000ee', '#000000ee']}
          start={{ x: 0.5, y: 0 }}
          end={{ x: 0.5, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      </Pressable>

      <Animated.View style={[styles.cardWrap, cardAnimated]} pointerEvents="box-none">
        <LinearGradient
          colors={r.metal}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={[
            styles.frame,
            {
              shadowColor: r.color,
              shadowOpacity: 0.85,
              shadowRadius: 32,
              shadowOffset: { width: 0, height: 0 },
            },
          ]}
        >
          <View style={styles.body}>
            <LinearGradient
              colors={[r.glow, 'transparent']}
              start={{ x: 0.5, y: 0 }}
              end={{ x: 0.5, y: 1 }}
              style={StyleSheet.absoluteFill}
              pointerEvents="none"
            />

            <Pressable
              testID={`${rootTestID.replace('-modal', '')}-close`}
              accessibilityRole="button"
              accessibilityLabel="Fermer"
              onPress={onDismiss}
              hitSlop={12}
              style={styles.closeBtn}
            >
              <Text style={styles.closeBtnChar}>✕</Text>
            </Pressable>

            <Text style={[styles.eyebrow, { color: r.color }]}>
              ★ Succès débloqué · {r.label}
            </Text>

            <Animated.View
              style={[
                styles.iconWrap,
                iconAnimated,
                {
                  shadowColor: r.color,
                  shadowOpacity: 1,
                  shadowRadius: 24,
                  shadowOffset: { width: 0, height: 0 },
                },
              ]}
            >
              <Text style={styles.icon}>{payload.icon}</Text>
            </Animated.View>

            <Text style={styles.label}>{payload.label}</Text>
            <Text style={styles.description}>{payload.description}</Text>

            <View style={[styles.cabPill, { borderColor: r.color }]}>
              <Text style={[styles.cabPillText, { color: r.color }]}>
                +{payload.cab_granted} CAB
              </Text>
            </View>
          </View>
        </LinearGradient>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 60,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  cardWrap: {
    width: '100%',
    maxWidth: 420,
  },
  frame: {
    padding: 2,
    borderRadius: 20,
  },
  body: {
    padding: 28,
    borderRadius: 18,
    backgroundColor: '#1A1B26',
    alignItems: 'center',
    overflow: 'hidden',
  },
  closeBtn: {
    position: 'absolute',
    top: 12,
    right: 12,
    width: 30,
    height: 30,
    borderRadius: 15,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.18)',
    backgroundColor: 'rgba(255,255,255,0.06)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeBtnChar: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '900',
  },
  eyebrow: {
    fontSize: 10,
    fontWeight: '900',
    letterSpacing: 1.4,
    textTransform: 'uppercase',
    marginBottom: 18,
    marginTop: 4,
  },
  iconWrap: {
    width: 96,
    height: 96,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
  },
  icon: {
    fontSize: 72,
  },
  label: {
    fontSize: 24,
    fontWeight: '900',
    color: '#fff',
    textAlign: 'center',
    letterSpacing: -0.4,
    marginBottom: 6,
  },
  description: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.75)',
    textAlign: 'center',
    marginBottom: 20,
    paddingHorizontal: 8,
  },
  cabPill: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1.5,
    backgroundColor: 'rgba(0,0,0,0.25)',
  },
  cabPillText: {
    fontSize: 14,
    fontWeight: '900',
    letterSpacing: 0.5,
  },
});

export default AchievementCelebrationModal;
