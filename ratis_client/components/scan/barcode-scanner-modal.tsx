// ratis_client/components/scan/barcode-scanner-modal.tsx
//
// Generic camera-backed barcode scanner modal. The caller owns the handling
// of the scanned EAN via `onBarcode` and the feedback rendering — this lets
// the same primitive serve both the list scan-check flow and the scan-history
// barcode-link flow (see ARCH_scan_history.md § Composants).
//
// Key behaviours :
//   - 1.5s cooldown de-dup per EAN so a camera that keeps a code in frame
//     doesn't spam the backend.
//   - When the modal closes, in-flight reads are ignored (guard ref).
//   - Permission-denied state renders a translated hint instead of the camera.

import React, { useCallback, useEffect, useRef } from 'react';
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { useTranslation } from 'react-i18next';

const SCAN_COOLDOWN_MS = 1500;

interface Props {
  visible: boolean;
  onClose: () => void;
  /** Called once per unique EAN (within the cooldown window). May be async —
   *  the caller is responsible for showing a toast / closing on success. */
  onBarcode: (ean: string) => Promise<void> | void;
  title: string;
  /** Optional hint text rendered inside the viewfinder. */
  hint?: string;
  /** Optional children rendered above the camera — for caller-owned feedback. */
  children?: React.ReactNode;
}

export function BarcodeScannerModal({ visible, onClose, onBarcode, title, hint, children }: Props) {
  const { t } = useTranslation();
  const [permission] = useCameraPermissions();
  const lastScannedRef = useRef<{ ean: string; at: number } | null>(null);
  const visibleRef = useRef(visible);

  // Keep a stable ref so the CameraView callback always sees the latest visibility.
  useEffect(() => {
    visibleRef.current = visible;
    if (visible) {
      // Reset cooldown when the modal reopens — the user is intentionally starting a new scan.
      lastScannedRef.current = null;
    }
  }, [visible]);

  const handleBarcode = useCallback(
    (payload: { data: string }) => {
      if (!visibleRef.current) return;
      const ean = payload?.data;
      if (!ean) return;
      const now = Date.now();
      const last = lastScannedRef.current;
      if (last && last.ean === ean && now - last.at < SCAN_COOLDOWN_MS) return;
      lastScannedRef.current = { ean, at: now };
      // Fire-and-forget — the caller handles async completion + error surfacing.
      Promise.resolve(onBarcode(ean)).catch(() => {});
    },
    [onBarcode],
  );

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onClose}
      testID="barcode-scanner-modal"
    >
      <View style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.title}>{title}</Text>
          <Pressable
            style={styles.closeBtn}
            onPress={onClose}
            testID="barcode-scanner-modal-close"
            accessibilityLabel={t('scan.history.barcode_modal.close_label')}
          >
            <Text style={styles.closeTxt}>✕</Text>
          </Pressable>
        </View>

        {!permission?.granted ? (
          <View style={styles.centerBody} testID="barcode-scanner-permission-denied">
            <Text style={styles.emptyHint}>
              {t('scan.history.barcode_modal.permission_text')}
            </Text>
          </View>
        ) : (
          <View style={styles.cameraWrap}>
            <CameraView
              style={StyleSheet.absoluteFill}
              testID="barcode-scanner-camera"
              onBarcodeScanned={handleBarcode}
              barcodeScannerSettings={{ barcodeTypes: ['ean13', 'ean8', 'upc_a'] }}
            />
            <View style={styles.viewfinder} pointerEvents="none" testID="barcode-scanner-viewfinder" />
            {children ? (
              <View style={styles.feedbackLayer} pointerEvents="box-none">
                {children}
              </View>
            ) : hint ? (
              <View style={styles.hintBand} pointerEvents="none">
                <Text style={styles.hintTxt}>{hint}</Text>
              </View>
            ) : null}
          </View>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 52,
    paddingHorizontal: 16,
    paddingBottom: 12,
    backgroundColor: '#000',
  },
  title: { color: '#fff', fontSize: 16, fontWeight: '700' },
  closeBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(255,255,255,0.1)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeTxt: { color: '#fff', fontSize: 14, fontWeight: '700' },
  centerBody: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    gap: 8,
  },
  emptyHint: { color: 'rgba(255,255,255,0.65)', fontSize: 13, textAlign: 'center' },
  cameraWrap: { flex: 1, position: 'relative' },
  viewfinder: {
    position: 'absolute',
    top: '40%',
    left: '20%',
    right: '20%',
    height: 120,
    borderWidth: 2,
    borderColor: '#A78BFA',
    borderRadius: 12,
    shadowColor: '#7C3AED',
    shadowOpacity: 0.6,
    shadowRadius: 12,
  },
  hintBand: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 48,
    alignItems: 'center',
  },
  hintTxt: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 12,
    backgroundColor: 'rgba(0,0,0,0.55)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 14,
  },
  feedbackLayer: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 48,
  },
});
