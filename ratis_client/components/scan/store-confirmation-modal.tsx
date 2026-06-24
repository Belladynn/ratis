// ratis_client/components/scan/store-confirmation-modal.tsx
//
// Read-only modal asking the user to confirm a store header that the OCR
// pipeline parsed but that doesn't match any known `stores` row. The user
// either confirms (creates a `user_suggested` store, validation pending) or
// taps "Re-scanner" to retry the receipt.
//
// Edition is intentionally NOT exposed — the user only confirms what the OCR
// read (anti-abuse layer 1 in ARCH_store_validation.md).

import React, { useEffect } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { toDisplayCase } from '@/utils/text';

const AUTO_CLOSE_DELAY_MS = 1500;

export type StoreConfirmationErrorCode =
  | 'insufficient_ocr_data'
  | 'receipt_already_resolved'
  | 'candidate_not_found'
  | 'generic';

export interface StoreCandidateInfo {
  brand_guess: string;
  address?: string | null;
  postal_code?: string | null;
  city?: string | null;
  phone?: string | null;
}

interface Props {
  visible: boolean;
  candidateInfo: StoreCandidateInfo | null;
  onConfirm: () => void;
  onClose: () => void;
  /** Caller routes the user back to the scan tab. */
  onRescan: () => void;
  isLoading?: boolean;
  errorCode?: StoreConfirmationErrorCode | null;
}

function joinAddress(info: StoreCandidateInfo): string | null {
  const parts = [info.address, info.postal_code, info.city]
    .filter((p): p is string => typeof p === 'string' && p.trim().length > 0);
  if (parts.length === 0) return null;
  return parts.join(', ');
}

export function StoreConfirmationModal({
  visible,
  candidateInfo,
  onConfirm,
  onClose,
  onRescan,
  isLoading = false,
  errorCode = null,
}: Props) {
  const { t } = useTranslation();

  // Auto-close on `receipt_already_resolved` — the user can't act on it,
  // they just need to know and move on.
  useEffect(() => {
    if (errorCode === 'receipt_already_resolved') {
      const timer = setTimeout(onClose, AUTO_CLOSE_DELAY_MS);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [errorCode, onClose]);

  const errorMessage = (() => {
    switch (errorCode) {
      case 'insufficient_ocr_data':
        return t('scan.history.confirm_store.error_insufficient_data');
      case 'receipt_already_resolved':
        return t('scan.history.confirm_store.error_already_resolved');
      case 'candidate_not_found':
        return t('scan.history.confirm_store.error_candidate_not_found');
      case 'generic':
        return t('scan.history.confirm_store.error_generic');
      default:
        return null;
    }
  })();

  const addressLine = candidateInfo ? joinAddress(candidateInfo) : null;
  const brandDisplay = candidateInfo ? toDisplayCase(candidateInfo.brand_guess) : '';
  const addressDisplay = addressLine ? toDisplayCase(addressLine) : null;

  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={onClose}
      testID="store-confirmation-modal"
    >
      <View style={styles.backdrop}>
        <SafeAreaView style={styles.safeArea}>
          <View style={styles.card}>
            <View style={styles.header}>
              <Text style={styles.title}>
                {t('scan.history.confirm_store.modal_title')}
              </Text>
              <Pressable
                style={styles.closeBtn}
                onPress={onClose}
                testID="store-confirmation-close"
                accessibilityLabel={t('scan.history.barcode_modal.close_label')}
                hitSlop={8}
              >
                <Text style={styles.closeTxt}>✕</Text>
              </Pressable>
            </View>

            <Text style={styles.intro}>
              {t('scan.history.confirm_store.modal_body_intro')}
            </Text>

            {candidateInfo && (
              <View style={styles.infoBlock}>
                <Text style={styles.infoLine}>
                  <Text style={styles.glyph}>🏪 </Text>
                  {brandDisplay}
                </Text>
                {addressDisplay != null && (
                  <Text style={styles.infoLine}>
                    <Text style={styles.glyph}>📍 </Text>
                    {addressDisplay}
                  </Text>
                )}
                {candidateInfo.phone != null && candidateInfo.phone.trim().length > 0 && (
                  <Text style={styles.infoLine}>
                    <Text style={styles.glyph}>☎️ </Text>
                    {candidateInfo.phone}
                  </Text>
                )}
              </View>
            )}

            <Text style={styles.question}>
              {t('scan.history.confirm_store.modal_body_question')}
            </Text>

            {errorMessage != null && (
              <View style={styles.errorBand} testID="store-confirmation-error">
                <Text style={styles.errorTxt}>{errorMessage}</Text>
              </View>
            )}

            <View style={styles.actions}>
              <Pressable
                style={[styles.btn, styles.btnSecondary]}
                onPress={onRescan}
                testID="store-confirmation-rescan-btn"
                accessibilityRole="button"
              >
                <Text style={styles.btnSecondaryTxt}>
                  {t('scan.history.confirm_store.btn_rescan')}
                </Text>
              </Pressable>
              <Pressable
                style={[
                  styles.btn,
                  styles.btnPrimary,
                  isLoading && styles.btnPrimaryDisabled,
                ]}
                onPress={() => {
                  if (isLoading) return;
                  onConfirm();
                }}
                disabled={isLoading}
                testID="store-confirmation-confirm-btn"
                accessibilityRole="button"
              >
                {isLoading ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.btnPrimaryTxt}>
                    {t('scan.history.confirm_store.btn_confirm')}
                  </Text>
                )}
              </Pressable>
            </View>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 16,
  },
  safeArea: {
    width: '100%',
    maxWidth: 480,
  },
  card: {
    backgroundColor: '#1a1f25',
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: 'rgba(167,139,250,0.25)',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 12,
    gap: 12,
  },
  title: {
    flex: 1,
    color: '#fff',
    fontSize: 16,
    fontWeight: '800',
  },
  closeBtn: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.08)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeTxt: { color: '#fff', fontSize: 13, fontWeight: '700' },
  intro: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 13,
    marginBottom: 12,
  },
  infoBlock: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: 12,
    padding: 12,
    gap: 6,
    marginBottom: 12,
  },
  infoLine: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '600',
  },
  glyph: {
    fontSize: 14,
  },
  question: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 13,
    marginBottom: 16,
  },
  errorBand: {
    backgroundColor: 'rgba(248,113,113,0.12)',
    borderColor: 'rgba(248,113,113,0.4)',
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    marginBottom: 12,
  },
  errorTxt: {
    color: '#F87171',
    fontSize: 12,
    fontWeight: '600',
    textAlign: 'center',
  },
  actions: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
  },
  btn: {
    flex: 1,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  btnSecondary: {
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.15)',
  },
  btnSecondaryTxt: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: 14,
    fontWeight: '700',
  },
  btnPrimary: {
    backgroundColor: '#A78BFA',
  },
  btnPrimaryDisabled: {
    opacity: 0.55,
  },
  btnPrimaryTxt: {
    color: '#0a0f12',
    fontSize: 14,
    fontWeight: '800',
  },
});
