// ratis_client/components/scan/scan-history-receipt-accordion.tsx
//
// Accordion card rendering a single receipt entry from the unified scan
// history list. The body (item rows) is lazy-loaded on first expand via
// `useReceiptItems(enabled=expanded)` — see ARCH_scan_history.md Flow A.

import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'expo-router';
import { ScreenCard } from '@/components/ui/screen-card';
import { useReceiptItems, type ReceiptItem } from '@/hooks/use-receipt-items';
import type { ReceiptEntry } from '@/hooks/use-scan-history';
import { ScanHistoryItemRow } from '@/components/scan/scan-history-item-row';
import { formatScanDateTime } from '@/utils/date';
import { toDisplayCase } from '@/utils/text';

interface Props {
  entry: ReceiptEntry;
  /** Called when the user taps a coloured barcode button on one of the items —
   *  the parent screen opens the shared BarcodeScannerModal. */
  onPressBarcodeForItem: (item: ReceiptItem) => void;
  /** Called when the user taps the edit-store pen — the parent screen opens
   *  the StoreConfirmationModal. Optional so existing call-sites don't break. */
  onPressEditStore?: (entry: ReceiptEntry) => void;
}

function formatPrice(cents: number | null): string {
  if (cents == null) return '—';
  return `${(cents / 100).toFixed(2).replace('.', ',')}€`;
}

export function ScanHistoryReceiptAccordion({
  entry,
  onPressBarcodeForItem,
  onPressEditStore,
}: Props) {
  const { t } = useTranslation();
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const itemsQuery = useReceiptItems(entry.receipt_id, { enabled: expanded });

  const totalArticles = entry.matched_count + entry.unmatched_count + entry.pending_count;
  const todo = entry.unmatched_count + entry.pending_count;

  const storeLabel = entry.store_name
    ? toDisplayCase(entry.store_name)
    : t('scan.history.receipt.unknown_store');
  // Includes time of day so users can distinguish two scans on the same day.
  const dateLabel = formatScanDateTime(entry.scanned_at);
  // Pen turns red when the store needs user attention (pending detection,
  // unknown match, or absent name). Confirmed stores get a discreet grey pen.
  const storeNeedsAttention =
    entry.store_name == null ||
    entry.store_status === 'pending' ||
    entry.store_status === 'unknown';
  const penColor = storeNeedsAttention ? '#EF4444' : 'rgba(255,255,255,0.4)';
  const showPendingBadge = entry.store_status === 'pending';

  const handleEditStorePress = () => {
    onPressEditStore?.(entry);
  };

  return (
    <ScreenCard accent="none" noPadding>
      <View style={styles.header}>
        <Pressable
          onPress={() => setExpanded((v) => !v)}
          style={styles.headerTextWrap}
          testID={`receipt-accordion-header-${entry.receipt_id}`}
          accessibilityRole="button"
        >
          <Text style={styles.storeEmoji}>🏪</Text>
          <View style={styles.headerText}>
            <View style={styles.storeRow}>
              <Text style={styles.store} numberOfLines={1}>
                {storeLabel}
              </Text>
              {dateLabel != null && (
                <Text
                  style={styles.storeDate}
                  testID={`receipt-accordion-date-${entry.receipt_id}`}
                >
                  {' · '}
                  {dateLabel}
                </Text>
              )}
            </View>
            {showPendingBadge && (
              <Text
                style={styles.pendingBadge}
                testID={`receipt-accordion-pending-validation-${entry.receipt_id}`}
              >
                🟡 {t('scan.history.receipt.store_pending_validation')}
              </Text>
            )}
            <Text style={styles.summary}>
              {t('scan.history.receipt.articles_count', { count: totalArticles })}
              {' · '}
              {formatPrice(entry.total_amount_cents)}
            </Text>
            <Text style={styles.detail}>
              {t('scan.history.receipt.articles_summary_recognized', { count: entry.matched_count })}
              {todo > 0 && (
                <Text>
                  {' · '}
                  {t('scan.history.receipt.articles_summary_todo', { count: todo })}
                </Text>
              )}
            </Text>
          </View>
        </Pressable>
        <Pressable
          onPress={handleEditStorePress}
          style={styles.editStoreBtn}
          testID={`receipt-accordion-edit-store-${entry.receipt_id}`}
          accessibilityRole="button"
          accessibilityLabel={t('scan.history.confirm_store.edit_store_a11y')}
          hitSlop={6}
        >
          <Text style={[styles.editStoreGlyph, { color: penColor }]}>✎</Text>
        </Pressable>
        <Pressable
          onPress={() => router.push('/(tabs)/scan')}
          style={styles.rescanBtn}
          testID={`receipt-accordion-rescan-${entry.receipt_id}`}
          accessibilityRole="button"
        >
          <Text style={styles.rescanTxt}>{t('scan.history.receipt.rescan_button')}</Text>
        </Pressable>
        <Text style={styles.chev}>{expanded ? '▾' : '▸'}</Text>
      </View>
      {expanded && (
        <View testID={`receipt-accordion-body-${entry.receipt_id}`}>
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
              item={item}
              onPressBarcode={onPressBarcodeForItem}
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
  headerTextWrap: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minWidth: 0,
  },
  storeEmoji: { fontSize: 18 },
  headerText: { flex: 1, minWidth: 0, gap: 2 },
  storeRow: { flexDirection: 'row', alignItems: 'baseline', minWidth: 0 },
  store: { fontSize: 13, fontWeight: '700', color: '#fff', flexShrink: 1 },
  storeDate: { fontSize: 13, fontWeight: '500', color: 'rgba(255,255,255,0.55)' },
  pendingBadge: {
    fontSize: 11,
    color: '#FBBF24',
    fontWeight: '700',
  },
  summary: { fontSize: 12, color: 'rgba(255,255,255,0.75)', fontWeight: '600' },
  detail: { fontSize: 11, color: 'rgba(255,255,255,0.5)' },
  editStoreBtn: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  editStoreGlyph: { fontSize: 16, fontWeight: '600' },
  rescanBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: 'rgba(139,92,246,0.2)',
    borderWidth: 1,
    borderColor: 'rgba(139,92,246,0.4)',
  },
  rescanTxt: { fontSize: 11, fontWeight: '800', color: '#A78BFA' },
  chev: { fontSize: 12, color: 'rgba(255,255,255,0.35)', marginLeft: 4 },
  loadingRow: {
    padding: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  errorTxt: { color: '#F87171', fontSize: 12 },
});
