// ratis_client/components/scan/scan-history-label-accordion.tsx
//
// Accordion card rendering a (store_id, date) label group from the unified
// scan history. Body lazy-loads accepted items via `useLabelGroupItems` —
// unmatched / rejected labels never appear here (ARCH § Flow D).

import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import { ScreenCard } from '@/components/ui/screen-card';
import { useLabelGroupItems } from '@/hooks/use-label-group-items';
import type { LabelGroupEntry } from '@/hooks/use-scan-history';
import { ScanHistoryItemRow } from '@/components/scan/scan-history-item-row';
import { formatScanDateTime } from '@/utils/date';
import { toDisplayCase } from '@/utils/text';

interface Props {
  entry: LabelGroupEntry;
}

export function ScanHistoryLabelAccordion({ entry }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const itemsQuery = useLabelGroupItems(entry.store_id, entry.date, { enabled: expanded });
  // Includes time of day so users can distinguish two label scans on the same day.
  const dateLabel = formatScanDateTime(entry.latest_scanned_at);

  return (
    <ScreenCard accent="none" noPadding>
      <Pressable
        onPress={() => setExpanded((v) => !v)}
        style={styles.header}
        testID={`label-accordion-header-${entry.group_key}`}
        accessibilityRole="button"
      >
        <Text style={styles.emoji}>🏪</Text>
        <View style={styles.headerText}>
          <View style={styles.storeRow}>
            <Text style={styles.store} numberOfLines={1}>
              {entry.store_name ? toDisplayCase(entry.store_name) : t('scan.history.receipt.unknown_store')}
            </Text>
            {dateLabel != null && (
              <Text
                style={styles.storeDate}
                testID={`label-accordion-date-${entry.group_key}`}
              >
                {' · '}
                {dateLabel}
              </Text>
            )}
          </View>
          <Text style={styles.summary}>
            {t('scan.history.label_group.products_count', { count: entry.accepted_count })}
          </Text>
        </View>
        <Text style={styles.chev}>{expanded ? '▾' : '▸'}</Text>
      </Pressable>
      {expanded && (
        <View testID={`label-accordion-body-${entry.group_key}`}>
          {itemsQuery.isLoading && (
            <View style={styles.loadingRow}>
              <ActivityIndicator color="#A78BFA" />
            </View>
          )}
          {itemsQuery.isError && (
            <View style={styles.loadingRow}>
              <Text style={styles.errorTxt}>{t('scan.history.error_title')}</Text>
            </View>
          )}
          {itemsQuery.data?.items.map((item) => (
            <ScanHistoryItemRow
              key={item.scan_id}
              item={{ kind: 'label_group', item }}
              // Labels don't support barcode re-linking (fire-and-forget).
              // The row still renders a green button but taps are a no-op.
              onPressBarcode={() => {}}
            />
          ))}
        </View>
      )}
    </ScreenCard>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  emoji: { fontSize: 18 },
  headerText: { flex: 1, minWidth: 0, gap: 2 },
  storeRow: { flexDirection: 'row', alignItems: 'baseline', minWidth: 0 },
  store: { fontSize: 13, fontWeight: '700', color: '#fff', flexShrink: 1 },
  storeDate: { fontSize: 13, fontWeight: '500', color: 'rgba(255,255,255,0.55)' },
  summary: { fontSize: 12, color: 'rgba(255,255,255,0.75)', fontWeight: '600' },
  chev: { fontSize: 12, color: 'rgba(255,255,255,0.35)' },
  loadingRow: {
    padding: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  errorTxt: { color: '#F87171', fontSize: 12 },
});
