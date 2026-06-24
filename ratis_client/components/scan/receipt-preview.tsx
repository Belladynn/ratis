// ratis_client/components/scan/receipt-preview.tsx
//
// Full-screen modal shown after the user taps the shutter in receipt mode.
// Lets them visually verify the photo before any network call. Without this
// the receipt was uploaded immediately on capture, leaving the user no way
// to retake a missed shot — first-bad-photo = polluted DB + R2 storage.
// Cf AF-09 in ALPHA_FEEDBACK.

import React from 'react';
import {
  Modal,
  View,
  Image,
  Text,
  Pressable,
  StyleSheet,
  StatusBar,
} from 'react-native';
import { useTranslation } from 'react-i18next';

interface Props {
  /** URI of the captured photo. When non-null the modal is shown. */
  uri: string | null;
  /** User accepted the photo — parent should enqueue + send. */
  onConfirm: () => void;
  /** User rejected the photo — parent should discard and return to camera. */
  onRetake: () => void;
}

export function ReceiptPreview({ uri, onConfirm, onRetake }: Props) {
  const { t } = useTranslation();
  const visible = uri !== null;

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onRetake}
      statusBarTranslucent
      testID="receipt-preview"
    >
      <StatusBar barStyle="light-content" backgroundColor="#000" />
      <View style={styles.root}>
        <View style={styles.imageWrap}>
          {uri ? (
            <Image
              source={{ uri }}
              style={styles.image}
              resizeMode="contain"
              testID="receipt-preview-image"
            />
          ) : null}
        </View>

        <View style={styles.captionWrap}>
          <Text style={styles.title}>{t('scan.preview.title')}</Text>
          <Text style={styles.subtitle}>{t('scan.preview.subtitle')}</Text>
        </View>

        <View style={styles.actions}>
          <Pressable
            onPress={onRetake}
            style={[styles.btn, styles.btnSecondary]}
            testID="receipt-preview-retake"
          >
            <Text style={styles.btnSecondaryTxt}>{t('scan.preview.retake')}</Text>
          </Pressable>
          <Pressable
            onPress={onConfirm}
            style={[styles.btn, styles.btnPrimary]}
            testID="receipt-preview-send"
          >
            <Text style={styles.btnPrimaryTxt}>{t('scan.preview.send')}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#000' },
  imageWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
    paddingTop: 24,
  },
  image: { width: '100%', height: '100%' },
  captionWrap: {
    paddingHorizontal: 24,
    paddingTop: 12,
    paddingBottom: 8,
    alignItems: 'center',
  },
  title: { color: '#fff', fontSize: 18, fontWeight: '800', marginBottom: 4 },
  subtitle: {
    color: 'rgba(255,255,255,0.7)',
    fontSize: 13,
    textAlign: 'center',
  },
  actions: {
    flexDirection: 'row',
    gap: 12,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 32,
  },
  btn: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  btnSecondary: {
    backgroundColor: 'rgba(255,255,255,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.20)',
  },
  btnSecondaryTxt: { color: '#fff', fontSize: 15, fontWeight: '700' },
  btnPrimary: { backgroundColor: '#7C3AED' },
  btnPrimaryTxt: { color: '#fff', fontSize: 15, fontWeight: '800' },
});
