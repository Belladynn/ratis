/**
 * Liste — TemplatesSheet (V5 strict iso).
 *
 * Reference JSX : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 304-348
 *                 (`TemplatesSheet`).
 *
 * Renders predefined shopping list templates in a bottom sheet. Source of the
 * templates is provided by the parent. V1 ships with an empty list (no
 * backend endpoint) and surfaces a placeholder.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/components/design-system';

export type ListTemplate = {
  id: string;
  label: string;
  icon: string;
  color: string;
  itemCount: number;
  estimatedTotal?: number | null;
};

export type TemplatesSheetProps = {
  open: boolean;
  onClose: () => void;
  templates?: ListTemplate[];
  onApply?: (t: ListTemplate) => void;
  testID?: string;
};

function fmt(amount: number): string {
  return '~' + amount.toFixed(2).replace('.', ',') + '€';
}

export function TemplatesSheet({
  open,
  onClose,
  templates = [],
  onApply,
  testID,
}: TemplatesSheetProps) {
  const { t } = useTranslation();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('liste.sheets.templates_title')}
      eyebrow={t('liste.sheets.templates_eyebrow')}
      testID={testID ?? 'liste-templates-sheet'}
    >
      {templates.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTxt}>
            {t('liste.sheets.voice_coming_soon')}
          </Text>
        </View>
      ) : (
        <View style={styles.list}>
          {templates.map((tmpl) => (
            <Pressable
              key={tmpl.id}
              testID={`liste-template-${tmpl.id}`}
              onPress={() => onApply?.(tmpl)}
              style={styles.row}
              accessibilityRole="button"
              accessibilityLabel={tmpl.label}
            >
              <View style={[styles.tile, { backgroundColor: tmpl.color }]}>
                <Text style={styles.tileIcon}>{tmpl.icon}</Text>
              </View>
              <View style={styles.body}>
                <Text style={styles.label}>{tmpl.label}</Text>
                <Text style={styles.sub}>
                  {tmpl.itemCount} articles
                  {typeof tmpl.estimatedTotal === 'number'
                    ? ` · ${fmt(tmpl.estimatedTotal)}`
                    : ''}
                </Text>
              </View>
            </Pressable>
          ))}
        </View>
      )}
    </Modal>
  );
}

const styles = StyleSheet.create({
  empty: {
    paddingVertical: 24,
    alignItems: 'center',
  },
  emptyTxt: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 13,
    fontWeight: '700',
  },
  list: {
    gap: 10,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 12,
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 14,
  },
  tile: {
    width: 44,
    height: 44,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tileIcon: {
    fontSize: 22,
  },
  body: {
    flex: 1,
    minWidth: 0,
  },
  label: {
    fontSize: 14,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.3,
  },
  sub: {
    fontSize: 10,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
});

export default TemplatesSheet;
