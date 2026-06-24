/**
 * Liste — VoiceSheet (V5 strict iso, V1 stub).
 *
 * Reference JSX : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 401-457
 *                 (`VoiceSheet`).
 *
 * V1 ships a "coming soon" stub : a microphone visual + a translated label.
 * Real speech-to-text integration will land in V2 once the Expo Speech
 * provider is wired through `EXPO_PUBLIC_*` env (out of scope for the
 * strict-iso PR).
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/components/design-system';

export type VoiceSheetProps = {
  open: boolean;
  onClose: () => void;
  testID?: string;
};

export function VoiceSheet({ open, onClose, testID }: VoiceSheetProps) {
  const { t } = useTranslation();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('liste.sheets.voice_title')}
      eyebrow={t('liste.sheets.voice_eyebrow')}
      testID={testID ?? 'liste-voice-sheet'}
      scrollable={false}
    >
      <View style={styles.body}>
        <View style={styles.mic}>
          <Text style={styles.micIcon}>🎤</Text>
        </View>
        <Text style={styles.label}>
          {t('liste.sheets.voice_coming_soon')}
        </Text>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  body: {
    paddingVertical: 24,
    alignItems: 'center',
    gap: 16,
  },
  mic: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: 'rgba(167,139,250,0.18)',
    borderWidth: 1.5,
    borderColor: 'rgba(167,139,250,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  micIcon: {
    fontSize: 44,
  },
  label: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '800',
    letterSpacing: -0.2,
    textAlign: 'center',
  },
});

export default VoiceSheet;
