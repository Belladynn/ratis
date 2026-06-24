import React from 'react';
import { View, Text, Pressable, StyleSheet, ActivityIndicator } from 'react-native';
import { useTranslation } from 'react-i18next';

// Possible statuses surfaced in the compact overlay :
// - 'normal'        → done, show price/store
// - 'uploading'     → local-only, queue is sending image to backend (AF-02)
// - 'processing'    → uploaded, backend OCR running (AF-02)
// - 'error'         → all upload retries exhausted, user can retry from history (AF-02)
// - 'unknown_store' → label saved but no store matched; user prompted to scan receipt
export type ScanHistoryItemStatus =
  | 'normal'
  | 'uploading'
  | 'processing'
  | 'error'
  | 'unknown_store';

export interface ScanHistoryItem {
  id: string;
  name: string;
  price: number;
  status?: ScanHistoryItemStatus;
}

interface Props {
  items: ScanHistoryItem[];
  onPressMore: () => void;
  // Fired when the user taps the amber chip on an 'unknown_store' row —
  // the parent screen is expected to switch the camera to receipt mode
  // so the user can reconcile their pending scans (Part B).
  onRequestReceiptMode?: () => void;
}

export function ScanHistoryOverlay({ items, onPressMore, onRequestReceiptMode }: Props) {
  const { t } = useTranslation();
  const displayed = items.slice(0, 3);
  return (
    <View style={styles.list}>
      <View style={styles.header}>
        <Text style={styles.label}>{t('scan.history.label')}</Text>
        <Pressable onPress={onPressMore} style={styles.more}>
          <Text style={styles.moreTxt}>{t('scan.history.see_all')}</Text>
        </Pressable>
      </View>
      {displayed.map(it => (
        <View key={it.id} style={styles.row} testID={`scan-history-row-${it.id}`}>
          <View style={styles.dot} />
          <Text style={styles.name} numberOfLines={1}>{it.name}</Text>
          {it.status === 'unknown_store' ? (
            <Pressable
              onPress={onRequestReceiptMode}
              disabled={!onRequestReceiptMode}
              style={styles.pendingBadge}
              testID={`scan-history-pending-badge-${it.id}`}
              accessibilityRole="button"
              accessibilityLabel={t('scan.history.pending_a11y')}
            >
              <Text style={styles.pendingBadgeTxt}>{t('scan.history.pending_chip')}</Text>
            </Pressable>
          ) : it.status === 'uploading' ? (
            <View style={styles.statusInline} testID={`scan-history-status-uploading-${it.id}`}>
              <ActivityIndicator size="small" color="#A78BFA" />
              <Text style={styles.statusInlineTxt}>{t('scan.history_status.uploading')}</Text>
            </View>
          ) : it.status === 'processing' ? (
            <View style={styles.statusInline} testID={`scan-history-status-processing-${it.id}`}>
              <ActivityIndicator size="small" color="#FFB800" />
              <Text style={styles.statusInlineTxt}>{t('scan.history_status.processing')}</Text>
            </View>
          ) : it.status === 'error' ? (
            <View style={styles.statusError} testID={`scan-history-status-error-${it.id}`}>
              <Text style={styles.statusErrorTxt}>{t('scan.history_status.error')}</Text>
            </View>
          ) : (
            <Text style={styles.price}>{it.price.toFixed(2)}€</Text>
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  list: {
    flex: 1,
    backgroundColor: 'rgba(11,11,16,0.6)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 14, padding: 10,
    maxHeight: 115,
    overflow: 'hidden',
  },
  header: {
    flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', gap: 6, marginBottom: 6,
  },
  label: {
    fontSize: 9, fontWeight: '800', letterSpacing: 1,
    color: 'rgba(251,113,133,0.95)', textTransform: 'uppercase',
  },
  more: {
    backgroundColor: 'rgba(139,92,246,0.14)',
    paddingVertical: 2, paddingHorizontal: 6,
    borderRadius: 6,
    borderWidth: 1, borderColor: 'rgba(139,92,246,0.3)',
  },
  moreTxt: {
    fontSize: 9, fontWeight: '800', color: '#A78BFA',
    textTransform: 'uppercase', letterSpacing: 0.6,
  },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingVertical: 3,
  },
  dot: {
    width: 5, height: 5, borderRadius: 2.5,
    backgroundColor: '#FB7185',
    shadowColor: '#FB7185',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1, shadowRadius: 4,
  },
  name: { flex: 1, fontSize: 10, color: 'rgba(255,255,255,0.85)' },
  price: { fontSize: 10, fontWeight: '700', color: 'rgba(255,184,0,0.9)' },
  pendingBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    backgroundColor: 'rgba(255,184,0,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.4)',
  },
  pendingBadgeTxt: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FFB800',
    letterSpacing: 0.4,
  },
  statusInline: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  statusInlineTxt: {
    fontSize: 9,
    color: 'rgba(255,255,255,0.7)',
    fontWeight: '600',
  },
  statusError: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    backgroundColor: 'rgba(252,165,165,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(252,165,165,0.4)',
  },
  statusErrorTxt: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FCA5A5',
    letterSpacing: 0.4,
  },
});
