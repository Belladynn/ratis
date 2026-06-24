// ratis_client/components/completer/form-state.tsx
//
// Composer : switches between FieldInputText and FieldInputTags
// based on task.missing_field. Renders the product name header
// + question label + the matching input component.

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Colors, Spacing, Typography } from '@/constants/theme';
import { FieldInputText } from '@/components/completer/field-input-text';
import { FieldInputTags } from '@/components/completer/field-input-tags';
import type { EnrichissementTask } from '@/types/gamification';

export interface FormStateProps {
  task: EnrichissementTask;
  isSubmitting?: boolean;
  onSubmit: (value: string | string[]) => void;
  onSkip: () => void;
}

export function FormState({ task, isSubmitting, onSubmit, onSkip }: FormStateProps) {
  const isTagsField =
    task.missing_field === 'categories_tags' ||
    task.missing_field === 'labels_tags';

  return (
    <View style={styles.root}>
      <Text style={styles.header} numberOfLines={2}>
        {task.product_name}
      </Text>
      {isTagsField ? (
        <FieldInputTags
          task={task}
          isSubmitting={isSubmitting}
          onSubmit={onSubmit as (tags: string[]) => void}
          onSkip={onSkip}
        />
      ) : (
        <FieldInputText
          task={task}
          isSubmitting={isSubmitting}
          onSubmit={onSubmit as (value: string) => void}
          onSkip={onSkip}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: Spacing.lg },
  header: {
    ...Typography.hero,
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
});
