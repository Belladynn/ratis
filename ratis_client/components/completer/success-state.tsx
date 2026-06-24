// ratis_client/components/completer/success-state.tsx
//
// Inline success state shown after a successful POST /contribute.
// Renders the reward animation (+5 ⚡) and offers two CTAs :
// « Suivant » (advance to next task in the batch) and « Retour »
// (back to dashboard). V1 keeps the reward animation simple — a
// small Animated.View scale-bounce on mount, no full-screen
// confetti, no sound.

import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/design-system';
import { Colors, Spacing, Typography } from '@/constants/theme';

export interface SuccessStateProps {
  reward: number;
  onNext: () => void;
  onDone: () => void;
}

export function SuccessState({ reward, onNext, onDone }: SuccessStateProps) {
  const { t } = useTranslation();
  const scale = useRef(new Animated.Value(0.6)).current;

  useEffect(() => {
    Animated.spring(scale, {
      toValue: 1,
      tension: 80,
      friction: 6,
      useNativeDriver: true,
    }).start();
  }, [scale]);

  return (
    <View style={styles.root}>
      <Text style={styles.heading}>{t('completer.success.heading')}</Text>
      <Animated.View style={[styles.rewardBurst, { transform: [{ scale }] }]}>
        <Text style={styles.rewardText}>{`+${reward} ⚡`}</Text>
      </Animated.View>
      <Button
        testID="success-state-next"
        variant="primary"
        size="md"
        label={t('completer.success.next')}
        fullWidth
        onPress={onNext}
        style={styles.cta}
      />
      <Button
        testID="success-state-done"
        variant="secondary"
        size="sm"
        label={t('completer.success.done')}
        onPress={onDone}
        style={styles.cta}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    padding: Spacing.xl,
    alignItems: 'center',
    gap: Spacing.lg,
    flex: 1,
    justifyContent: 'center',
  },
  heading: {
    ...Typography.hero,
    color: Colors.textPrimary,
    textAlign: 'center',
  },
  rewardBurst: {
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.md,
    borderRadius: 20,
    backgroundColor: Colors.terracotta,
    marginVertical: Spacing.lg,
  },
  rewardText: {
    color: '#FFFFFF',
    fontSize: 32,
    fontFamily: 'Inter_900Black',
  },
  cta: { marginTop: Spacing.sm },
});
