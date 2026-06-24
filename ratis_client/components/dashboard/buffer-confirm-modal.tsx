// ratis_client/components/dashboard/buffer-confirm-modal.tsx
//
// Buffer + Burst (refonte 2026-05-09) — confirmation modal before applying
// a Buffer on a daily mission.
//
// The modal explains the impact (window +1 day, target × 2, +X CAB) and
// asks for explicit confirmation. Applying a Buffer is free in CAB and
// reversible only by waiting out the period — so we surface the trade
// clearly rather than silently mutate.
//
// Wiring : the parent (`MissionsBlock` / `MissionsModal`) opens this when
// the "Buffer" button is pressed. On `onConfirm` it calls
// `useBufferMission().mutateAsync(missionId)` then closes the modal.
//
// Design system : reuses `<Modal />` (bottom sheet) + `<Button />` for
// the cta pair. Strings are i18n'd under `gamification.buffer.*`.

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Colors, Typography } from '@/constants/theme';
import { Button, Modal } from '@/components/design-system';

export type BufferConfirmModalProps = {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  /** R additional CAB unlocked by this Buffer (= cab_reward / (n+1)). */
  cabBonus: number;
  /** Buffer count BEFORE this action (0..2). */
  currentBufferCount: number;
  /** Disabled while the mutation is in flight. */
  loading?: boolean;
  testID?: string;
};

export function BufferConfirmModal({
  open,
  onClose,
  onConfirm,
  cabBonus,
  currentBufferCount,
  loading = false,
  testID = 'buffer-confirm-modal',
}: BufferConfirmModalProps) {
  const { t } = useTranslation();
  const nextBufferCount = currentBufferCount + 1;

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={t('gamification.buffer.modal_eyebrow')}
      title={t('gamification.buffer.modal_title')}
      testID={testID}
      scrollable={false}
    >
      <View style={styles.body}>
        <Text testID={`${testID}-description`} style={styles.description}>
          {t('gamification.buffer.modal_description', {
            cab: cabBonus,
            level: nextBufferCount,
          })}
        </Text>
        <View style={styles.actions}>
          <Button
            testID={`${testID}-cancel`}
            label={t('gamification.buffer.modal_cancel')}
            variant="secondary"
            onPress={onClose}
            disabled={loading}
            style={styles.btn}
          />
          <Button
            testID={`${testID}-confirm`}
            label={t('gamification.buffer.modal_confirm')}
            variant="gold"
            onPress={onConfirm}
            disabled={loading}
            style={styles.btn}
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  body: {
    paddingTop: 4,
    paddingBottom: 8,
    gap: 16,
  },
  description: {
    ...Typography.body,
    color: Colors.textPrimary,
    fontSize: 14,
    lineHeight: 20,
  },
  actions: {
    flexDirection: 'row',
    gap: 10,
  },
  btn: {
    flex: 1,
  },
});

export default BufferConfirmModal;
