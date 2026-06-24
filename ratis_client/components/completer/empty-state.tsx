// ratis_client/components/completer/empty-state.tsx
//
// Three variants of the "no task to render" screen :
//   - EmptyState     : no incomplete products at all
//   - ExhaustedState : batch ran through, user must come back later
//   - ErrorState     : fetch failed, retry available
// Kept in one file because they share the same minimal shape.

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/design-system';
import { Colors, Spacing, Typography } from '@/constants/theme';

interface BaseProps {
  onBack: () => void;
}

export function EmptyState({ onBack }: BaseProps) {
  const { t } = useTranslation();
  return (
    <View style={styles.root}>
      <Text style={styles.icon}>🎉</Text>
      <Text style={styles.title}>{t('completer.empty.title')}</Text>
      <Text style={styles.body}>{t('completer.empty.body')}</Text>
      <Button
        label={t('completer.empty.back')}
        variant="primary"
        onPress={onBack}
        testID="empty-state-back"
      />
    </View>
  );
}

export function ExhaustedState({ onBack }: BaseProps) {
  const { t } = useTranslation();
  return (
    <View style={styles.root}>
      <Text style={styles.icon}>✨</Text>
      <Text style={styles.title}>{t('completer.exhausted.title')}</Text>
      <Text style={styles.body}>{t('completer.exhausted.body')}</Text>
      <Button
        label={t('completer.exhausted.back')}
        variant="primary"
        onPress={onBack}
        testID="exhausted-state-back"
      />
    </View>
  );
}

export interface ErrorStateProps extends BaseProps {
  onRetry: () => void;
}

export function ErrorState({ onBack, onRetry }: ErrorStateProps) {
  const { t } = useTranslation();
  return (
    <View style={styles.root}>
      <Text style={styles.icon}>📡</Text>
      <Text style={styles.title}>{t('completer.error.title')}</Text>
      <Text style={styles.body}>{t('completer.error.body')}</Text>
      <Button
        label={t('completer.error.retry')}
        variant="primary"
        onPress={onRetry}
        testID="error-state-retry"
      />
      <Button
        label={t('completer.error.back')}
        variant="secondary"
        size="sm"
        onPress={onBack}
        testID="error-state-back"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    padding: Spacing.xl,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.md,
  },
  icon: { fontSize: 56 },
  title: {
    ...Typography.hero,
    color: Colors.textPrimary,
    textAlign: 'center',
  },
  body: {
    ...Typography.body,
    color: Colors.textSecondary,
    textAlign: 'center',
    marginBottom: Spacing.md,
  },
});
