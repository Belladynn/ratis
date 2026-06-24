// ratis_client/app/completer.tsx
//
// « Compléter ce produit » screen — queue-based product-field
// completion flow. Linked from the dashboard `EnrichissementCard`
// CTA. Fetches a batch of up to 10 missing-field tasks ranked by
// cross-user popularity, iterates locally via a state machine :
//
//   loading → form → submitting → success → next OR done
//                                       ↓
//                                  exhausted (after last)
//
//   skip → form (next task, no backend call)
//
// Spec : docs/superpowers/specs/2026-05-14-completer-screen-design.md

import { useRouter } from 'expo-router';
import React, { useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { Colors } from '@/constants/theme';
import { useIncompleteProducts } from '@/hooks/use-incomplete-products';
import { useContributeField } from '@/hooks/use-contribute-field';
import { FormState } from '@/components/completer/form-state';
import { SuccessState } from '@/components/completer/success-state';
import {
  EmptyState,
  ExhaustedState,
  ErrorState,
} from '@/components/completer/empty-state';

export default function CompleterScreen() {
  const router = useRouter();
  const incomplete = useIncompleteProducts({ limit: 10 });
  const contribute = useContributeField();

  const tasks = incomplete.data?.items ?? [];
  const [index, setIndex] = useState(0);
  const [phase, setPhase] = useState<'form' | 'success'>('form');

  if (incomplete.isLoading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" color={Colors.terracotta} />
      </View>
    );
  }

  if (incomplete.error) {
    return (
      <ErrorState
        onRetry={() => incomplete.refetch()}
        onBack={() => router.back()}
      />
    );
  }

  if (tasks.length === 0) {
    return <EmptyState onBack={() => router.back()} />;
  }

  if (index >= tasks.length) {
    return <ExhaustedState onBack={() => router.back()} />;
  }

  const task = tasks[index];

  if (phase === 'success') {
    return (
      <SuccessState
        reward={task.cab_reward}
        onNext={() => {
          setPhase('form');
          setIndex((i) => i + 1);
        }}
        onDone={() => router.back()}
      />
    );
  }

  return (
    <FormState
      task={task}
      isSubmitting={contribute.isPending}
      onSubmit={async (value) => {
        await contribute.mutateAsync({
          ean: task.product_ean,
          field: task.missing_field as
            | 'name'
            | 'brands'
            | 'categories_tags'
            | 'labels_tags',
          value,
        });
        setPhase('success');
      }}
      onSkip={() => setIndex((i) => i + 1)}
    />
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, justifyContent: 'center', alignItems: 'center' },
});
