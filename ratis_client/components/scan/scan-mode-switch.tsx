import React from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

export type ScanMode = 'receipt' | 'label';

interface Props {
  mode: ScanMode;
  onChange: (mode: ScanMode) => void;
}

export function ScanModeSwitch({ mode, onChange }: Props) {
  const { t } = useTranslation();
  return (
    <View style={styles.switch}>
      <Pressable
        testID="scan-mode-receipt"
        style={[styles.item, mode === 'receipt' && styles.active]}
        onPress={() => onChange('receipt')}
      >
        <Text style={[styles.txt, mode === 'receipt' && styles.txtActive]}>
          {t('scan.mode.receipt')}
        </Text>
      </Pressable>
      <Pressable
        testID="scan-mode-label"
        style={[styles.item, mode === 'label' && styles.active]}
        onPress={() => onChange('label')}
      >
        <Text style={[styles.txt, mode === 'label' && styles.txtActive]}>
          {t('scan.mode.label')}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  switch: {
    flexDirection: 'row',
    backgroundColor: 'rgba(11,11,16,0.7)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)',
    borderRadius: 24, padding: 3,
  },
  item: {
    paddingVertical: 8, paddingHorizontal: 16,
    borderRadius: 22,
  },
  active: {
    backgroundColor: 'rgba(139,92,246,0.2)',
    borderWidth: 1, borderColor: 'rgba(139,92,246,0.4)',
  },
  txt: { fontSize: 11, fontWeight: '800', color: 'rgba(255,255,255,0.55)' },
  txtActive: { color: '#A78BFA' },
});
