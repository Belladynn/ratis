import React from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';
import type { ScanMode } from './scan-mode-switch';

interface Props {
  mode: ScanMode;
  photoCount: number;
  maxPhotos: number;
  onSend: () => void;
}

export function ScanTopActions({ mode, photoCount, maxPhotos, onSend }: Props) {
  const { t } = useTranslation();
  const canSend = photoCount > 0;
  return (
    <View style={styles.actions}>
      {mode === 'label' && (
        <View testID="photo-counter" style={styles.counter}>
          <Text style={styles.counterIcon}>📸</Text>
          <Text style={styles.counterTxt}>{photoCount}/{maxPhotos}</Text>
        </View>
      )}
      <Pressable
        testID="btn-send"
        onPress={canSend ? onSend : undefined}
        accessibilityState={{ disabled: !canSend }}
        style={{ opacity: canSend ? 1 : 0.4 }}
      >
        <LinearGradient
          colors={['#7C3AED', '#5B21B6']}
          style={styles.sendBtn}
        >
          <Text style={styles.sendTxt}>{t('scan.send')}</Text>
        </LinearGradient>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: 'column', gap: 6, alignItems: 'flex-end' },
  counter: {
    flexDirection: 'row', gap: 4, alignItems: 'center',
    backgroundColor: 'rgba(11,11,16,0.7)',
    borderWidth: 1, borderColor: 'rgba(251,113,133,0.35)',
    borderRadius: 10, paddingVertical: 4, paddingHorizontal: 9,
  },
  counterIcon: { fontSize: 10 },
  counterTxt: { fontSize: 11, fontWeight: '800', color: '#FB7185' },
  sendBtn: {
    borderRadius: 10, paddingVertical: 7, paddingHorizontal: 12,
    shadowColor: '#7C3AED', shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.5, shadowRadius: 14, elevation: 6,
  },
  sendTxt: {
    color: '#fff', fontSize: 11, fontWeight: '900',
    letterSpacing: 0.5, textTransform: 'uppercase',
  },
});
