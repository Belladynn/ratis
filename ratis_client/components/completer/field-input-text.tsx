// ratis_client/components/completer/field-input-text.tsx
//
// Free-text field input for the « Compléter ce produit » screen.
// Used when `task.missing_field` is `name` or `brands` (string
// scalar fields). For tag-array fields, see `field-input-tags.tsx`.
//
// V1 validation : min 2 chars trimmed. Backend Pydantic re-validates
// (more strictly — length cap, charset, etc.), so this is just a
// client-side gate to avoid obviously-bad submissions.

import React, { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '@/components/design-system';
import { Colors, Radii, Spacing, Typography } from '@/constants/theme';
import type { EnrichissementTask } from '@/types/gamification';

export interface FieldInputTextProps {
  task: EnrichissementTask;
  isSubmitting?: boolean;
  onSubmit: (value: string) => void;
  onSkip: () => void;
}

const MIN_LENGTH = 2;

function fieldLabel(field: string): string {
  switch (field) {
    case 'brands':
      return 'Quelle est la marque ?';
    case 'name':
      return 'Quel est le nom du produit ?';
    default:
      return 'Saisis la valeur';
  }
}

export function FieldInputText({
  task, isSubmitting, onSubmit, onSkip,
}: FieldInputTextProps) {
  const [value, setValue] = useState('');
  const trimmed = value.trim();
  const canSubmit = trimmed.length >= MIN_LENGTH && !isSubmitting;

  return (
    <View style={styles.root}>
      <Text style={styles.label}>{fieldLabel(task.missing_field)}</Text>
      <TextInput
        testID="field-input-text-input"
        value={value}
        onChangeText={setValue}
        placeholder="Tape ici…"
        placeholderTextColor={Colors.textMuted}
        style={styles.input}
        autoCapitalize="words"
        editable={!isSubmitting}
      />
      <Button
        testID="field-input-text-submit"
        variant="primary"
        size="md"
        label={isSubmitting ? 'Envoi…' : `Valider · +${task.cab_reward} ⚡`}
        fullWidth
        disabled={!canSubmit}
        onPress={() => canSubmit && onSubmit(trimmed)}
        style={styles.submit}
      />
      <Button
        testID="field-input-text-skip"
        variant="secondary"
        size="sm"
        label="Pas sûr, passer"
        onPress={onSkip}
        disabled={isSubmitting}
        style={styles.skip}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { padding: Spacing.lg, gap: Spacing.md },
  label: {
    ...Typography.body,
    color: Colors.textPrimary,
  },
  input: {
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.15)',
    borderRadius: Radii.badge,
    padding: Spacing.md,
    color: Colors.textPrimary,
    backgroundColor: Colors.surface,
    fontFamily: 'Inter_400Regular',
    fontSize: 14,
  },
  submit: { marginTop: Spacing.sm },
  skip: { alignSelf: 'center' },
});
