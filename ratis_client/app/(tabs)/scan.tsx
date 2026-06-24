import React, { useState, useCallback, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Pressable, Modal, ToastAndroid, Platform, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { LocationPermissionBanner } from '@/components/ui/location-permission-banner';
import { ScanViewfinder } from '@/components/scan/scan-viewfinder';
import { ScanModeSwitch, ScanMode } from '@/components/scan/scan-mode-switch';
import { ScanCaptureButton } from '@/components/scan/scan-capture-button';
import { ScanTopActions } from '@/components/scan/scan-top-actions';
import { ReceiptPreview } from '@/components/scan/receipt-preview';
import {
  ScanHistoryOverlay,
  ScanHistoryItem as OverlayItem,
} from '@/components/scan/scan-history-overlay';
import {
  useScanHistory,
  SCAN_HISTORY_QUERY_KEY,
  type ScanHistoryEntry,
} from '@/hooks/use-scan-history';
import { usePendingScans } from '@/hooks/use-pending-scans';
import {
  enqueueReceipt,
  enqueueLabel,
  processQueue,
} from '@/services/scan-queue';
import { scanEvents } from '@/services/scan-events';
import { flattenAndResize } from '@/services/image-pipeline';

import type { ScanItem } from '@/types/scan';

/**
 * Project a unified history entry onto the compact overlay shape used by
 * `ScanHistoryOverlay`. Receipts show total amount, label groups are a proxy
 * row (no per-product price in the unified endpoint).
 *
 * Pending detection (fix 2026-05-01) — a receipt is "still processing" when
 * EITHER:
 *   - the OCR has not yet emitted any row (no total + 0 items), OR
 *   - at least one of the persisted scans is still in `status='pending'`
 *     (pipeline_v3 creates pending scan rows BEFORE the cascade resolves
 *     them — without this, a receipt mid-pipeline showed up as a 0€
 *     done-row instead of "Traitement en cours").
 */
function toOverlayItem(entry: ScanHistoryEntry, fallbackName: string): OverlayItem {
  if (entry.type === 'receipt') {
    const itemsTotal =
      entry.matched_count + entry.unmatched_count + entry.pending_count;
    const ocrEmpty = entry.total_amount_cents === null && itemsTotal === 0;
    const stillProcessing = ocrEmpty || entry.pending_count > 0;
    return {
      id: `r:${entry.receipt_id}`,
      name: entry.store_name ?? fallbackName,
      price: (entry.total_amount_cents ?? 0) / 100,
      status: stillProcessing
        ? 'processing'
        : entry.store_status === 'unknown'
          ? 'unknown_store'
          : 'normal',
    };
  }
  return {
    id: `l:${entry.group_key}`,
    name: entry.store_name ?? fallbackName,
    price: 0,
    status: 'normal',
  };
}

/** Poll interval (ms) for the scan-history query while a pending receipt is
 *  in flight. Short enough to feel live, long enough to keep the network
 *  cheap on cellular. */
const PENDING_POLL_INTERVAL_MS = 5_000;

/** Max age of a local orphan ScanItem (uploading/error) we surface in the
 *  abridged scan-tab preview. Beyond this the user has likely moved on and
 *  the full /scan-history page is the right place for recovery. Without this
 *  bound, never-purged terminal `error` items in AsyncStorage dominated the
 *  3-slot preview and hid recent backend receipts. */
const LOCAL_ORPHAN_MAX_AGE_MS = 10 * 60 * 1_000; // 10 min

/** True when at least one local in-flight scan or one backend pending receipt
 *  exists — drives the auto-refetch on the scan-history query so the badge
 *  flips from "Traitement en cours" to its final state without manual refresh. */
function hasInFlightWork(
  entries: ScanHistoryEntry[],
  localItems: ScanItem[],
): boolean {
  if (localItems.some((it) => it.status === 'uploading' || it.status === 'processing')) {
    return true;
  }
  return entries.some(
    (e) =>
      e.type === 'receipt' &&
      (e.pending_count > 0 ||
        (e.total_amount_cents === null &&
          e.matched_count + e.unmatched_count + e.pending_count === 0)),
  );
}

/**
 * Project a local-only ScanItem (from AsyncStorage) onto the overlay shape.
 * Used for entries the backend hasn't seen yet (`uploading`, no backendScanId)
 * or for failures we need to surface even after the upload-side stage.
 *
 * Id uses backendScanId when known, else the local UUID — so the dedup with
 * backend entries (which key on receipt_id / group_key) actually matches.
 */
function localToOverlayItem(item: ScanItem, fallbackName: string): OverlayItem {
  let status: OverlayItem['status'];
  if (item.status === 'uploading') status = 'uploading';
  else if (item.status === 'processing') status = 'processing';
  else if (item.status === 'error') status = 'error';
  else status = 'normal';

  const stableId = item.backendScanId ?? item.id;
  return {
    id: item.type === 'receipt' ? `r:${stableId}` : `l:${stableId}`,
    name: item.storeName ?? item.productName ?? fallbackName,
    price: (item.totalCents ?? item.priceCents ?? 0) / 100,
    status,
  };
}

export default function ScanScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const [mode, setMode] = useState<ScanMode>('receipt');
  const [photos, setPhotos] = useState<string[]>([]);
  const [location, setLocation] = useState<{ lat: number; lng: number } | null>(null);
  const [locationDenied, setLocationDenied] = useState(false);
  const [locationBannerDismissed, setLocationBannerDismissed] = useState(false);
  const [permission, requestPermission] = useCameraPermissions();
  const [unknownStoreModal, setUnknownStoreModal] = useState(false);
  // Receipt-mode preview gate (AF-09). When non-null the user has just taken a
  // photo in receipt mode and is reviewing it before any network call. Sending
  // happens on confirm, NOT on capture — fixes the "first bad photo = polluted
  // DB" UX.
  const [pendingReceiptUri, setPendingReceiptUri] = useState<string | null>(null);
  const cameraRef = useRef<CameraView>(null);
  const queryClient = useQueryClient();
  const pending = usePendingScans();
  // Drive auto-refetch from the previously-rendered first page so a freshly
  // uploaded ticket flips from "Traitement en cours" to its final state on
  // its own — no pull-to-refresh required. We read the query cache directly
  // (rather than a second `useScanHistory` call) so we have ONE source of
  // truth for the interval. On the first render the cache is empty and we
  // poll-conservatively only if local pending entries exist; once data
  // arrives, the next render evaluates `pending_count` and keeps polling
  // until everything resolves.
  const cachedFirstPageEntries: ScanHistoryEntry[] =
    queryClient.getQueryData<{
      pages: { entries: ScanHistoryEntry[] }[];
    }>([...SCAN_HISTORY_QUERY_KEY, 20])?.pages?.[0]?.entries ?? [];
  const shouldPoll = hasInFlightWork(cachedFirstPageEntries, pending.data ?? []);
  const history = useScanHistory(20, {
    refetchInterval: shouldPoll ? PENDING_POLL_INTERVAL_MS : false,
  });
  const fallbackName = t('scan.history.fallback_name');

  // Invalidate the scan-history query as soon as a batch is uploaded so the
  // backend version of the row appears in the list (it will most likely show
  // up in `status='pending'` first, then flip to its final state via the
  // refetch interval driven by `shouldPoll`). Without this the freshly
  // uploaded ticket only appeared after pull-to-refresh — see fix 2026-05-01.
  useEffect(() => {
    const unsubscribe = scanEvents.subscribe((event) => {
      if (event.type === 'batch_uploaded') {
        queryClient.invalidateQueries({ queryKey: SCAN_HISTORY_QUERY_KEY });
      }
    });
    return unsubscribe;
  }, [queryClient]);
  // AF-02 — merge local in-flight scans with the backend history.
  //
  // Backend is canonical : as soon as `/scan/history` knows about a receipt,
  // its row is the truth (store, total, items, OCR pending state). Local
  // entries are only useful BEFORE the backend has registered them — i.e.
  // during the upload window (`uploading`) or when the upload itself failed
  // (`error`).
  //
  // Match key : backendScanId (= receipt_id) when set, otherwise the local
  // UUID. Without this, local id `r:<localUUID>` and backend id
  // `r:<receipt_id>` never matched and both rows showed up forever.
  //
  // Recency window (fix 2026-05-01) — the abridged overlay only shows the 3
  // most recent rows. Local AsyncStorage history is never purged, so old
  // terminal `error` items (from past failed uploads) used to accumulate and
  // be unconditionally prepended via [...localOrphans, ...backendItems],
  // pushing recent backend receipts off the visible slice. The user reported
  // "the preview shows only old failures while the full history page shows
  // my recent scans". We now surface local rows only inside a short recency
  // window (`LOCAL_ORPHAN_MAX_AGE_MS`) — beyond it, the user has moved on
  // and the full /scan-history page handles recovery (retry / dismiss).
  const firstPageEntries = history.data?.pages[0]?.entries ?? [];
  const backendIds = new Set(
    firstPageEntries.map(e =>
      e.type === 'receipt' ? `r:${e.receipt_id}` : `l:${e.group_key}`,
    ),
  );
  const nowMs = Date.now();
  const localOrphans = (pending.data ?? [])
    .filter(item => {
      // Only keep local rows that the backend hasn't yet absorbed AND that
      // are still informative (uploading / error) AND recent enough that the
      // user is likely still expecting feedback on them in the abridged
      // preview. Once backend has it, the backend version wins — even if
      // it's still OCR-processing, the toOverlayItem mapper will surface
      // that state correctly.
      const overlayId = localToOverlayItem(item, fallbackName).id;
      if (backendIds.has(overlayId)) return false;
      if (item.status !== 'uploading' && item.status !== 'error') return false;
      return nowMs - item.createdAt <= LOCAL_ORPHAN_MAX_AGE_MS;
    })
    .map(item => localToOverlayItem(item, fallbackName));
  const backendItems = firstPageEntries.map(entry =>
    toOverlayItem(entry, fallbackName),
  );
  const historyItems: OverlayItem[] = [...localOrphans, ...backendItems];

  // Subscribe to scan-queue events — show the "unknown store" modal when a
  // label batch was persisted without a matching store. The user is invited
  // to scan a receipt to validate the store (Part B reconciliation).
  useEffect(() => {
    const unsubscribe = scanEvents.subscribe(event => {
      if (event.type === 'batch_uploaded' && event.store_status === 'unknown') {
        setUnknownStoreModal(true);
      }
    });
    return unsubscribe;
  }, []);

  // Request foreground geolocation once — needed for label mode (backend
  // resolves the store from user_lat/user_lng). RGPD: lat/lng never logged.
  const requestLocation = useCallback(async () => {
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      setLocationDenied(true);
      return;
    }
    setLocationDenied(false);
    setLocationBannerDismissed(false);
    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    });
    setLocation({ lat: loc.coords.latitude, lng: loc.coords.longitude });
  }, []);

  useEffect(() => {
    requestLocation();
  }, [requestLocation]);

  // Helper: cross-platform feedback toast.
  const showToast = useCallback((msg: string) => {
    if (Platform.OS === 'android') {
      ToastAndroid.show(msg, ToastAndroid.SHORT);
    } else {
      Alert.alert(msg);
    }
  }, []);

  const handleCapture = useCallback(async () => {
    if (!permission?.granted) return;
    if (mode === 'label' && !location) {
      // Silently bail — the banner + disabled capture button communicate the
      // required action. No intrusive Alert.
      return;
    }
    // Capture at high quality, skip the camera's own processing — we'll
    // re-encode through expo-image-manipulator below (AF-12 fix : honor
    // EXIF orientation + resize down).
    const pic = await cameraRef.current?.takePictureAsync({
      quality: 1,
      skipProcessing: true,
    });
    if (!pic?.uri) return;
    // Bake EXIF rotation into the pixels and resize. Without this, Android
    // portrait photos arrive at PaddleOCR upside down (the JPEG carries an
    // EXIF rotation tag but raw-sensor pixels — server-side libs don't
    // honor the tag). Cf alpha 2026-04-26.
    const processedUri = await flattenAndResize(pic.uri).catch(() => pic.uri);

    if (mode === 'label' && location) {
      // Label mode keeps the batch flow : each shot increments the counter,
      // user taps "Envoyer →" once they've shot every label of the session.
      setPhotos(prev => [...prev, processedUri]);
      enqueueLabel(processedUri, location.lat, location.lng).catch(() => {});
    } else if (mode === 'receipt') {
      // Receipt mode pauses on a preview screen (AF-09). NOTHING goes out
      // to the network here — neither queue write nor history entry. The
      // user reviews the photo and taps "Envoyer" to commit, OR "Reprendre"
      // to discard and shoot again. Avoids polluting DB+R2 with bad shots.
      setPendingReceiptUri(processedUri);
    }
  }, [mode, permission?.granted, location]);

  // AF-09 — receipt confirmed in preview : enqueue, kick the queue, clear
  // the preview. This is the moment when the receipt actually leaves the
  // device.
  const handleConfirmReceipt = useCallback(() => {
    if (!pendingReceiptUri) return;
    enqueueReceipt(pendingReceiptUri).catch(() => {});
    processQueue().catch(() => {});
    setPendingReceiptUri(null);
    showToast(t('scan.queued_toast'));
  }, [pendingReceiptUri, showToast, t]);

  // AF-09 — receipt rejected in preview : just discard. No DB / R2 / queue
  // touched, the user is back at the camera as if nothing happened.
  const handleRetakeReceipt = useCallback(() => {
    setPendingReceiptUri(null);
  }, []);

  // Send button at the top of the screen — only meaningful in label batch
  // mode. In receipt mode the preview's Send button is the commit action.
  const handleSend = useCallback(() => {
    const hadPhotos = photos.length > 0;
    processQueue().catch(() => {});
    setPhotos([]);
    showToast(hadPhotos ? t('scan.queued_toast') : t('scan.queued_empty_toast'));
  }, [photos.length, showToast, t]);

  if (!permission) return <View style={styles.container} />;

  const labelGeoMissing = mode === 'label' && !location;
  const showLocationBanner =
    locationDenied && !locationBannerDismissed;

  return (
    <View style={styles.container}>
      {permission.granted ? (
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing="back"
        />
      ) : (
        <View style={[StyleSheet.absoluteFill, styles.noperm]}>
          <Text style={styles.npTxt}>{t('scan.permission_required')}</Text>
          <Pressable onPress={requestPermission} style={styles.npBtn}>
            <Text style={styles.npBtnTxt}>{t('scan.permission_allow')}</Text>
          </Pressable>
        </View>
      )}

      <ScanViewfinder />

      <SafeAreaView edges={['top']} style={styles.topArea}>
        <LinearGradient
          colors={['rgba(11,11,16,0.85)', 'rgba(11,11,16,0)']}
          style={StyleSheet.absoluteFill}
        />
        <View style={styles.overlayTop}>
          <ScanHistoryOverlay
            items={historyItems}
            onPressMore={() => router.push('/scan-history')}
            onRequestReceiptMode={() => setMode('receipt')}
          />
          {/* Top "Envoyer →" only in label mode — in receipt mode the
              ReceiptPreview's Send button is the commit. (AF-09) */}
          {mode === 'label' ? (
            <ScanTopActions
              mode={mode}
              photoCount={photos.length}
              maxPhotos={50}
              onSend={handleSend}
            />
          ) : null}
        </View>
      </SafeAreaView>

      <View style={styles.bottomArea}>
        <LinearGradient
          colors={['rgba(11,11,16,0)', 'rgba(11,11,16,0.75)']}
          style={StyleSheet.absoluteFill}
        />
        <View style={styles.overlayBottom}>
          {showLocationBanner ? (
            <View style={styles.bannerWrap}>
              <LocationPermissionBanner
                context="scan"
                onRequestPermission={requestLocation}
                onDismiss={() => setLocationBannerDismissed(true)}
              />
            </View>
          ) : null}
          {labelGeoMissing && !showLocationBanner ? (
            <View style={styles.geoBanner} testID="geo-banner">
              <Text style={styles.geoBannerText}>
                {t('scan.geo_banner')}
              </Text>
            </View>
          ) : null}
          <ScanModeSwitch mode={mode} onChange={setMode} />
          <ScanCaptureButton
            onPress={handleCapture}
            disabled={!permission?.granted || labelGeoMissing}
          />
        </View>
      </View>

      <ReceiptPreview
        uri={pendingReceiptUri}
        onConfirm={handleConfirmReceipt}
        onRetake={handleRetakeReceipt}
      />

      <Modal
        visible={unknownStoreModal}
        transparent
        animationType="fade"
        onRequestClose={() => setUnknownStoreModal(false)}
        testID="unknown-store-modal"
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>
              {t('scan.unknown_store_modal.title')}
            </Text>
            <Text style={styles.modalBody}>
              {t('scan.unknown_store_modal.body')}
            </Text>
            <View style={styles.modalActions}>
              <Pressable
                onPress={() => setUnknownStoreModal(false)}
                style={[styles.modalBtn, styles.modalBtnSecondary]}
                testID="unknown-store-modal-later"
              >
                <Text style={styles.modalBtnSecondaryTxt}>
                  {t('scan.unknown_store_modal.later')}
                </Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  setMode('receipt');
                  setUnknownStoreModal(false);
                }}
                style={[styles.modalBtn, styles.modalBtnPrimary]}
                testID="unknown-store-modal-scan-receipt"
              >
                <Text style={styles.modalBtnPrimaryTxt}>
                  {t('scan.unknown_store_modal.scan_receipt')}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  topArea: { position: 'absolute', top: 0, left: 0, right: 0 },
  overlayTop: {
    flexDirection: 'row', gap: 10, alignItems: 'flex-start',
    padding: 14,
  },
  bottomArea: {
    position: 'absolute', bottom: 72, left: 0, right: 0,
    paddingTop: 30, paddingBottom: 16,
  },
  overlayBottom: {
    alignItems: 'center', gap: 12,
  },
  noperm: { alignItems: 'center', justifyContent: 'center', padding: 20 },
  npTxt: { color: '#fff', fontSize: 16, marginBottom: 10 },
  npBtn: { paddingHorizontal: 20, paddingVertical: 10, backgroundColor: '#7C3AED', borderRadius: 10 },
  npBtnTxt: { color: '#fff', fontWeight: '800' },
  bannerWrap: {
    alignSelf: 'stretch',
    paddingHorizontal: 14,
  },
  geoBanner: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: 'rgba(252, 165, 165, 0.15)',
    borderWidth: 1,
    borderColor: 'rgba(252, 165, 165, 0.4)',
  },
  geoBannerText: { color: '#FCA5A5', fontSize: 13, fontWeight: '600' },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: {
    width: '100%',
    maxWidth: 380,
    backgroundColor: '#141420',
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.25)',
  },
  modalTitle: {
    color: '#FFB800',
    fontSize: 16,
    fontWeight: '800',
    marginBottom: 10,
  },
  modalBody: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 18,
  },
  modalActions: { flexDirection: 'row', gap: 10, justifyContent: 'flex-end' },
  modalBtn: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 10,
  },
  modalBtnPrimary: { backgroundColor: '#7C3AED' },
  modalBtnPrimaryTxt: { color: '#fff', fontWeight: '800', fontSize: 13 },
  modalBtnSecondary: {
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.25)',
  },
  modalBtnSecondaryTxt: { color: 'rgba(255,255,255,0.85)', fontWeight: '700', fontSize: 13 },
});
