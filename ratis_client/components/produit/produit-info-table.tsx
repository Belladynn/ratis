/**
 * Produit info table — V5 strict iso (`Produit.png`, "Infos" tab).
 *
 * Source JSX : `Ratis_handoff/lib/ratis-other-tabs.jsx` lignes 457-478.
 *
 * Anatomy :
 *
 *   ┌──────────────────────────────────────────┐
 *   │ CARACTÉRISTIQUES                         │
 *   │ ───────────────────────────────────────  │
 *   │ Quantité           10 capsules           │
 *   │ Marque             Nespresso             │
 *   │ Origine            Suisse                │
 *   │ Poids net          57 g                  │
 *   │ Conservation       Sec, ambiant          │
 *   └──────────────────────────────────────────┘
 *
 * Visual contract (immuable, JSX iso) :
 *   - Surface card `Colors.surface` + 1.5px subtle border, radius 18
 *   - Header label uppercase weight 800 size 11 letter-spacing 0.8
 *   - Each row : flex-row space-between, padding-vertical 8, bottom border
 *     `rgba(255,255,255,0.04)`. Key opacity 0.55 weight 700 size 11. Value
 *     white weight 800 size 12.
 *
 * Data shape : caller passes a `rows` array of `{ key, value }` tuples.
 * Untranslated — the caller controls which fields show up. V1 backend
 * `ProductInfo` lacks origin/conservation/weight, so the screen falls back to
 * a "—" sentinel when those fields are missing.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Colors } from '@/constants/theme';

export type InfoRow = { key: string; value: string };

export interface ProduitInfoTableProps {
  rows: InfoRow[];
  testID?: string;
}

export function ProduitInfoTable({ rows, testID }: ProduitInfoTableProps) {
  const { t } = useTranslation();
  return (
    <View testID={testID} style={styles.card}>
      <Text style={styles.header}>
        {t('produit.info_table.section_label').toUpperCase()}
      </Text>
      <View>
        {rows.map((row, i) => (
          <View
            key={row.key}
            style={[styles.row, i === rows.length - 1 ? styles.rowLast : null]}
          >
            <Text style={styles.k}>{row.key}</Text>
            <Text style={styles.v} numberOfLines={1}>
              {row.value}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.surface,
    borderRadius: 18,
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.08)',
    padding: 16,
    shadowColor: 'rgba(0,0,0,0.35)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  header: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  rowLast: { borderBottomWidth: 0 },
  k: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.55)',
  },
  v: {
    fontSize: 12,
    fontWeight: '800',
    color: Colors.textPrimary,
    flexShrink: 1,
    marginLeft: 12,
    textAlign: 'right',
  },
});

export default ProduitInfoTable;
