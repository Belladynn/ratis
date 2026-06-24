// ratis_client/components/dashboard/enrichissement-card.tsx
//
// "Compléter une fiche produit" gold card — port of
// `Ratis_handoff/lib/ratis-real-v4.jsx` `function EnrichissementCard`
// (lines 673-710).
//
// Visual anatomy :
//   - dark gold body `#3D2E0F` + soft gold border
//   - 42×42 gradient gold tile with 💡 emoji
//   - "Compléter" headline + sub "{product_name} — {missing_field}" (clamped
//     to 2 lines)
//   - full-width gold gradient CTA "+{reward} ⚡"
//
// Hook : `useEnrichissement()` is consumed by the dashboard composition. The
// JSX `if (!task) return null` behavior is preserved here — silently render
// nothing when the hook returns no task (e.g. product_analyser downtime).
//
// Reward display : `cab_reward` is integer CAB units (the canonical unit for
// the gamification economy — Cabécoins). Previously this component divided
// by 100 and formatted as euros, which contradicted the backend semantics
// and the rest of the app's convention (`⚡` icon for CAB everywhere). Fix
// shipped 2026-05-14 along with the « Compléter ce produit » screen wiring.
//
// Navigation : tap CTA → `router.push('/completer')`. The optional
// `onPress(ean)` callback is invoked first so existing callers passing a
// custom handler keep working — V1 dashboard doesn't pass one.

import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { useRouter } from 'expo-router';

import { Colors, Typography } from '@/constants/theme';
import { Button } from '@/components/design-system';
import type { EnrichissementTask } from '@/types/gamification';

export type EnrichissementCardProps = {
  task: EnrichissementTask | null | undefined;
  isLoading?: boolean;
  onPress?: (productEan: string) => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

function formatReward(cab: number): string {
  return `${cab}`;
}

export function EnrichissementCard({
  task,
  isLoading,
  onPress,
  testID = 'enrichissement-card',
  style,
}: EnrichissementCardProps) {
  const router = useRouter();

  if (isLoading) {
    return (
      <View
        testID={`${testID}-skeleton`}
        style={[styles.root, styles.skeleton, style]}
      />
    );
  }
  if (!task) return null;

  const rewardLabel = `+${formatReward(task.cab_reward)} ⚡`;

  return (
    <View testID={testID} style={[styles.root, style]}>
      <View style={styles.iconTile}>
        <Text style={styles.iconText}>💡</Text>
      </View>
      <Text style={styles.title}>Compléter</Text>
      <Text style={styles.subtitle} numberOfLines={2}>
        {task.product_name} — {task.missing_field}
      </Text>
      <Button
        testID={`${testID}-cta`}
        variant="gold"
        size="md"
        label={rewardLabel}
        fullWidth
        onPress={() => {
          onPress?.(task.product_ean);
          router.push('/completer');
        }}
        style={styles.cta}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flexDirection: 'column',
    gap: 6,
    padding: 14,
    borderRadius: 20,
    backgroundColor: '#3D2E0F',
    borderWidth: 1.5,
    borderColor: 'rgba(255,184,0,0.45)',
    // 3D shadow — single hard layer.
    shadowColor: 'rgba(120,80,0,0.5)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  skeleton: {
    minHeight: 130,
    opacity: 0.45,
  },
  iconTile: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: Colors.gold,
    borderWidth: 1.5,
    borderColor: Colors.goldHi,
    alignItems: 'center',
    justifyContent: 'center',
    // Top inset highlight is approximated via a subtle gold border.
    shadowColor: Colors.goldSh,
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 3,
  },
  iconText: {
    fontSize: 22,
    color: '#3A2200',
    lineHeight: 24,
  },
  title: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 13,
    color: Colors.textPrimary,
    marginTop: 4,
    letterSpacing: -0.26,
  },
  subtitle: {
    ...Typography.bodySm,
    color: 'rgba(255,255,255,0.55)',
    fontSize: 11,
    fontWeight: '500',
    lineHeight: 14,
  },
  cta: {
    marginTop: 4,
  },
});

export default EnrichissementCard;
