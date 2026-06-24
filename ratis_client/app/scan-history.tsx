// ratis_client/app/scan-history.tsx
//
// Full-page unified scan history. Accessible from the scan tab via
// `router.push('/scan-history')`. Implements:
//
//   - Infinite scroll via `useScanHistory` (cursor pagination).
//   - Accordion per entry (receipt vs label_group) with lazy item fetch.
//   - Shared `BarcodeScannerModal` triggered from an item's coloured button —
//     links an EAN to the clicked scan_id via `POST /scan/barcode`.
//
// ARCH : `ratis_client/ARCH_scan_history.md` § V0 · Écran historique.

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { ScreenBackground } from '@/components/ui/screen-background-legacy';
import {
  useScanHistory,
  type ReceiptEntry,
  type ScanHistoryEntry,
} from '@/hooks/use-scan-history';
import { useReceiptItems, type ReceiptItem } from '@/hooks/use-receipt-items';
import { useScanBarcodeLink } from '@/hooks/use-scan-barcode-link';
import { useScanConfirmStore } from '@/hooks/use-scan-confirm-store';
import { ScanHistoryReceiptAccordion } from '@/components/scan/scan-history-receipt-accordion';
import { ScanHistoryLabelAccordion } from '@/components/scan/scan-history-label-accordion';
import { BarcodeScannerModal } from '@/components/scan/barcode-scanner-modal';
import {
  StoreConfirmationModal,
  type StoreConfirmationErrorCode,
} from '@/components/scan/store-confirmation-modal';

interface ActiveLink {
  receiptId: string;
  item: ReceiptItem;
}

export default function ScanHistoryScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const history = useScanHistory();

  // Which receipt + scan row is currently being linked (drives the modal).
  const [activeLink, setActiveLink] = useState<ActiveLink | null>(null);
  const barcodeLink = useScanBarcodeLink(activeLink?.receiptId ?? '');
  const [linkFeedback, setLinkFeedback] = useState<string | null>(null);

  // Which receipt is currently being confirmed for store identification.
  // Setting this state triggers a fetch of the receipt detail (for
  // `store_candidate_info`) and opens the StoreConfirmationModal.
  const [activeConfirmStore, setActiveConfirmStore] =
    useState<ReceiptEntry | null>(null);
  const confirmStoreReceiptId = activeConfirmStore?.receipt_id ?? null;
  const confirmStoreDetail = useReceiptItems(confirmStoreReceiptId, {
    enabled: !!confirmStoreReceiptId,
  });
  const confirmStoreMutation = useScanConfirmStore(confirmStoreReceiptId ?? '');
  const [confirmStoreError, setConfirmStoreError] =
    useState<StoreConfirmationErrorCode | null>(null);
  const [confirmStoreSuccess, setConfirmStoreSuccess] = useState<string | null>(null);

  // Flatten pages for the FlatList — `data` shape is { pages, pageParams }.
  const entries: ScanHistoryEntry[] =
    history.data?.pages.flatMap((p) => p.entries) ?? [];

  const handlePressBarcodeForItem = useCallback(
    (receiptId: string) => (item: ReceiptItem) => {
      setLinkFeedback(null);
      setActiveLink({ receiptId, item });
    },
    [],
  );

  const handleBarcodeScanned = useCallback(
    async (ean: string) => {
      if (!activeLink) return;
      setLinkFeedback(null);
      try {
        await barcodeLink.mutateAsync({ ean, scan_id: activeLink.item.scan_id });
        setLinkFeedback(t('scan.history.barcode_modal.success'));
        // Auto-close after a short visual ack — consumer sees the row recolour.
        setTimeout(() => {
          setActiveLink(null);
          setLinkFeedback(null);
        }, 1200);
      } catch (err) {
        const msg = err instanceof Error ? err.message : '';
        if (msg.includes('product_mismatch')) {
          setLinkFeedback(t('scan.history.barcode_modal.error_mismatch'));
        } else if (msg.includes('product_not_found')) {
          setLinkFeedback(t('scan.history.barcode_modal.error_not_found'));
          setTimeout(() => {
            setActiveLink(null);
            setLinkFeedback(null);
          }, 1500);
        } else if (msg.includes('scan_already_resolved')) {
          setLinkFeedback(t('scan.history.barcode_modal.error_already_resolved'));
          setTimeout(() => {
            setActiveLink(null);
            setLinkFeedback(null);
          }, 1200);
        } else {
          setLinkFeedback(t('scan.history.barcode_modal.error_generic'));
        }
      }
    },
    [activeLink, barcodeLink, t],
  );

  const handleCloseModal = useCallback(() => {
    setActiveLink(null);
    setLinkFeedback(null);
  }, []);

  const handlePressEditStore = useCallback((entry: ReceiptEntry) => {
    setConfirmStoreError(null);
    setConfirmStoreSuccess(null);
    setActiveConfirmStore(entry);
  }, []);

  const handleCloseConfirmStore = useCallback(() => {
    setActiveConfirmStore(null);
    setConfirmStoreError(null);
    setConfirmStoreSuccess(null);
  }, []);

  const handleConfirmStore = useCallback(async () => {
    if (!confirmStoreReceiptId) return;
    setConfirmStoreError(null);
    try {
      await confirmStoreMutation.mutateAsync();
      setConfirmStoreSuccess(t('scan.history.confirm_store.success_pending'));
      // Auto-close after a short visual ack — the badge swaps to "pending".
      setTimeout(() => {
        setActiveConfirmStore(null);
        setConfirmStoreSuccess(null);
      }, 1500);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('insufficient_ocr_data')) {
        setConfirmStoreError('insufficient_ocr_data');
      } else if (msg.includes('receipt_already_resolved')) {
        setConfirmStoreError('receipt_already_resolved');
      } else if (msg.includes('candidate_not_found')) {
        setConfirmStoreError('candidate_not_found');
      } else {
        setConfirmStoreError('generic');
      }
    }
  }, [confirmStoreMutation, confirmStoreReceiptId, t]);

  const handleRescan = useCallback(() => {
    setActiveConfirmStore(null);
    setConfirmStoreError(null);
    setConfirmStoreSuccess(null);
    router.push('/(tabs)/scan');
  }, [router]);

  // If we got a candidate_not_found-class detail (i.e. the receipt detail
  // returned without a `store_candidate_info` block while we expected one),
  // surface that to the user before they even click Confirm.
  useEffect(() => {
    if (!activeConfirmStore) return;
    if (confirmStoreDetail.isLoading) return;
    if (confirmStoreDetail.isError) {
      setConfirmStoreError('generic');
      return;
    }
    const detail = confirmStoreDetail.data;
    if (detail && !detail.store_candidate_info) {
      setConfirmStoreError('candidate_not_found');
    }
  }, [
    activeConfirmStore,
    confirmStoreDetail.data,
    confirmStoreDetail.isError,
    confirmStoreDetail.isLoading,
  ]);

  const renderEntry = useCallback(
    ({ item }: { item: ScanHistoryEntry }) => {
      if (item.type === 'receipt') {
        return (
          <ScanHistoryReceiptAccordion
            entry={item}
            onPressBarcodeForItem={handlePressBarcodeForItem(item.receipt_id)}
            onPressEditStore={handlePressEditStore}
          />
        );
      }
      return <ScanHistoryLabelAccordion entry={item} />;
    },
    [handlePressBarcodeForItem, handlePressEditStore],
  );

  const keyExtractor = useCallback((item: ScanHistoryEntry) => {
    return item.type === 'receipt' ? `r:${item.receipt_id}` : `l:${item.group_key}`;
  }, []);

  const handleEndReached = useCallback(() => {
    if (history.hasNextPage && !history.isFetchingNextPage) {
      history.fetchNextPage();
    }
  }, [history]);

  return (
    <View style={styles.container}>
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        <View style={styles.header}>
          <Pressable
            testID="scan-history-back"
            accessibilityRole="button"
            accessibilityLabel={t('scan.history.back')}
            onPress={() => router.back()}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title}>{t('scan.history.title')}</Text>
          <View style={styles.backBtn} />
        </View>

        {history.isLoading ? (
          <View style={styles.centerBody}>
            <ActivityIndicator color="#A78BFA" />
          </View>
        ) : history.isError ? (
          <View style={styles.centerBody}>
            <Text style={styles.errorTxt}>{t('scan.history.error_title')}</Text>
            <Pressable
              onPress={() => history.refetch()}
              style={styles.retryBtn}
              testID="scan-history-retry"
              accessibilityRole="button"
            >
              <Text style={styles.retryTxt}>{t('scan.history.error_retry')}</Text>
            </Pressable>
          </View>
        ) : entries.length === 0 ? (
          <View style={styles.centerBody} testID="scan-history-empty">
            <Text style={styles.emptyTitle}>{t('scan.history.empty_title')}</Text>
            <Text style={styles.emptyHint}>{t('scan.history.empty_hint')}</Text>
          </View>
        ) : (
          <FlatList
            testID="scan-history-list"
            data={entries}
            keyExtractor={keyExtractor}
            renderItem={renderEntry}
            contentContainerStyle={styles.list}
            onEndReachedThreshold={0.3}
            onEndReached={handleEndReached}
            onRefresh={() => history.refetch()}
            refreshing={history.isRefetching && !history.isFetchingNextPage}
            ListFooterComponent={
              history.isFetchingNextPage ? (
                <View style={styles.footer}>
                  <ActivityIndicator color="#A78BFA" />
                </View>
              ) : null
            }
          />
        )}
      </SafeAreaView>

      <BarcodeScannerModal
        visible={activeLink !== null}
        onClose={handleCloseModal}
        onBarcode={handleBarcodeScanned}
        title={t('scan.history.barcode_modal.title')}
        hint={t('scan.history.barcode_modal.hint')}
      >
        {linkFeedback && (
          <View style={styles.feedbackBand} testID="scan-history-barcode-feedback">
            <Text style={styles.feedbackTxt}>{linkFeedback}</Text>
          </View>
        )}
      </BarcodeScannerModal>

      <StoreConfirmationModal
        visible={activeConfirmStore !== null}
        candidateInfo={confirmStoreDetail.data?.store_candidate_info ?? null}
        onConfirm={handleConfirmStore}
        onClose={handleCloseConfirmStore}
        onRescan={handleRescan}
        isLoading={confirmStoreMutation.isPending}
        errorCode={confirmStoreError}
      />

      {confirmStoreSuccess && (
        <View
          style={styles.successToast}
          testID="scan-history-confirm-store-success"
          pointerEvents="none"
        >
          <Text style={styles.successTxt}>{confirmStoreSuccess}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0f12' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  backTxt: { color: '#fff', fontSize: 28, lineHeight: 28 },
  title: { fontSize: 17, fontWeight: '900', color: '#fff', letterSpacing: -0.3 },
  centerBody: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    gap: 8,
  },
  emptyTitle: { color: '#fff', fontSize: 16, fontWeight: '700', textAlign: 'center' },
  emptyHint: { color: 'rgba(255,255,255,0.6)', fontSize: 13, textAlign: 'center' },
  errorTxt: { color: '#F87171', fontSize: 14, fontWeight: '600', textAlign: 'center' },
  retryBtn: {
    marginTop: 14,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: 'rgba(139,92,246,0.2)',
    borderWidth: 1,
    borderColor: 'rgba(139,92,246,0.4)',
  },
  retryTxt: { color: '#A78BFA', fontWeight: '800' },
  list: { padding: 14, paddingBottom: 80 },
  footer: { padding: 16, alignItems: 'center' },
  feedbackBand: {
    padding: 14,
    borderRadius: 12,
    backgroundColor: 'rgba(0,0,0,0.75)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.15)',
    alignItems: 'center',
  },
  feedbackTxt: { color: '#fff', fontSize: 14, fontWeight: '600', textAlign: 'center' },
  successToast: {
    position: 'absolute',
    bottom: 32,
    left: 16,
    right: 16,
    padding: 14,
    borderRadius: 12,
    backgroundColor: 'rgba(34,197,94,0.9)',
    alignItems: 'center',
  },
  successTxt: { color: '#fff', fontSize: 13, fontWeight: '700', textAlign: 'center' },
});
