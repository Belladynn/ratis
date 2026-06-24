/**
 * Liste — SuggestionsSheet (V5 strict iso).
 *
 * Reference JSX : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 351-399
 *                 (`SuggestionsSheet`).
 *
 * Renders a list of "you often re-buy…" suggestions in a bottom sheet. The
 * actual suggestion source is provided by the parent — V1 ships with an
 * empty list (no backend endpoint yet) and the sheet surfaces a placeholder.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/components/design-system';
import { Colors } from '@/constants/theme';

export type Suggestion = {
  id: string;
  name: string;
  brand?: string | null;
  est?: number | null;
};

export type SuggestionsSheetProps = {
  open: boolean;
  onClose: () => void;
  suggestions?: Suggestion[];
  onAdd?: (s: Suggestion) => void;
  testID?: string;
};

function fmt(amount: number): string {
  return amount.toFixed(2).replace('.', ',') + '€';
}

export function SuggestionsSheet({
  open,
  onClose,
  suggestions = [],
  onAdd,
  testID,
}: SuggestionsSheetProps) {
  const { t } = useTranslation();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('liste.sheets.suggestions_title')}
      eyebrow={t('liste.sheets.suggestions_eyebrow')}
      testID={testID ?? 'liste-suggestions-sheet'}
    >
      {suggestions.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTxt}>
            {t('liste.sheets.voice_coming_soon')}
          </Text>
        </View>
      ) : (
        <View style={styles.list}>
          {suggestions.map((s) => (
            <Pressable
              key={s.id}
              testID={`liste-suggestion-${s.id}`}
              onPress={() => onAdd?.(s)}
              style={styles.row}
              accessibilityRole="button"
              accessibilityLabel={s.name}
            >
              <View style={styles.rowBody}>
                {s.brand ? <Text style={styles.brand}>{s.brand}</Text> : null}
                <Text style={styles.name} numberOfLines={1}>
                  {s.name}
                </Text>
              </View>
              {typeof s.est === 'number' ? (
                <Text style={styles.price}>{fmt(s.est)}</Text>
              ) : null}
              <View style={styles.addBadge}>
                <Text style={styles.addBadgeTxt}>＋</Text>
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
    gap: 8,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: 'rgba(255,255,255,0.03)',
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 12,
  },
  rowBody: {
    flex: 1,
    minWidth: 0,
  },
  brand: {
    fontSize: 9,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.5)',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
  },
  name: {
    fontSize: 13,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: -0.2,
  },
  price: {
    fontSize: 12,
    fontWeight: '900',
    color: Colors.gold,
  },
  addBadge: {
    width: 26,
    height: 26,
    borderRadius: 7,
    backgroundColor: 'rgba(167,139,250,0.18)',
    borderWidth: 1,
    borderColor: 'rgba(167,139,250,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  addBadgeTxt: {
    color: Colors.violetText,
    fontSize: 14,
    fontWeight: '900',
  },
});

export default SuggestionsSheet;
