// ratis_client/components/completer/field-input-tags.tsx
//
// Multi-select chip input for the « Compléter ce produit » screen.
// Used when `task.missing_field` is `categories_tags` or
// `labels_tags` (string-array fields). Selection sources from the
// curated list in `constants/contribute-tags-fr.ts`.
//
// V1 : no autocomplete from OFF taxonomy — the curated FR list
// covers ~80% of grocery use cases. Backend accepts any string for
// these fields (no taxonomy validation), so the FE drives the
// vocabulary entirely.

import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/design-system';
import { Colors, Spacing, Typography } from '@/constants/theme';
import {
  CATEGORY_TAGS_FR,
  LABEL_TAGS_FR,
  type CuratedTag,
} from '@/constants/contribute-tags-fr';
import type { EnrichissementTask } from '@/types/gamification';

export interface FieldInputTagsProps {
  task: EnrichissementTask;
  isSubmitting?: boolean;
  onSubmit: (tags: string[]) => void;
  onSkip: () => void;
}

function listFor(field: string): readonly CuratedTag[] {
  return field === 'labels_tags' ? LABEL_TAGS_FR : CATEGORY_TAGS_FR;
}

function fieldLabel(field: string): string {
  return field === 'labels_tags'
    ? 'Quels labels s’appliquent à ce produit ?'
    : 'Dans quelles catégories ?';
}

export function FieldInputTags({
  task, isSubmitting, onSubmit, onSkip,
}: FieldInputTagsProps) {
  const tags = useMemo(() => listFor(task.missing_field), [task.missing_field]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const canSubmit = selected.size > 0 && !isSubmitting;

  const toggle = (tag: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  return (
    <View style={styles.root}>
      <Text style={styles.label}>{fieldLabel(task.missing_field)}</Text>
      <ScrollView contentContainerStyle={styles.chips}>
        {tags.map((entry) => {
          const isOn = selected.has(entry.tag);
          return (
            <Pressable
              key={entry.tag}
              onPress={() => toggle(entry.tag)}
              style={[styles.chip, isOn ? styles.chipOn : styles.chipOff]}
              disabled={isSubmitting}
            >
              <Text style={[styles.chipText, isOn && styles.chipTextOn]}>
                {entry.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <Button
        testID="field-input-tags-submit"
        variant="primary"
        size="md"
        label={isSubmitting ? 'Envoi…' : `Valider · +${task.cab_reward} ⚡`}
        fullWidth
        disabled={!canSubmit}
        onPress={() => canSubmit && onSubmit(Array.from(selected))}
        style={styles.submit}
      />
      <Button
        testID="field-input-tags-skip"
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
  root: { padding: Spacing.lg, gap: Spacing.md, flex: 1 },
  label: {
    ...Typography.body,
    color: Colors.textPrimary,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.sm,
    paddingBottom: Spacing.sm,
  },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 16,
  },
  chipOff: {
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.15)',
  },
  chipOn: {
    backgroundColor: Colors.terracotta,
  },
  chipText: {
    color: Colors.textPrimary,
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
  },
  chipTextOn: {
    color: '#FFFFFF',
    fontFamily: 'Inter_600SemiBold',
  },
  submit: { marginTop: Spacing.sm },
  skip: { alignSelf: 'center' },
});
