import React from 'react';
import { Pressable, View, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

interface Props {
  onPress: () => void;
  disabled?: boolean;
}

export function ScanCaptureButton({ onPress, disabled = false }: Props) {
  return (
    <Pressable
      testID="scan-capture-btn"
      onPress={onPress}
      disabled={disabled}
      pointerEvents={disabled ? 'none' : 'auto'}
      style={[styles.outer, disabled && styles.disabled]}
    >
      <View style={styles.ring}>
        <LinearGradient
          colors={['#FFE580', '#FFB800']}
          style={styles.inner}
        />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  outer: {
    width: 66, height: 66, borderRadius: 33,
    alignItems: 'center', justifyContent: 'center',
  },
  disabled: { opacity: 0.4 },
  ring: {
    width: 66, height: 66, borderRadius: 33,
    borderWidth: 3, borderColor: 'rgba(255,255,255,0.9)',
    padding: 4,
  },
  inner: {
    flex: 1, borderRadius: 30,
    shadowColor: '#FFB800',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.5, shadowRadius: 20,
    elevation: 8,
  },
});
