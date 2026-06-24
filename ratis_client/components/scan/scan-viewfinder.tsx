import React from 'react';
import { View, StyleSheet } from 'react-native';

export function ScanViewfinder() {
  return (
    <View style={styles.frame} pointerEvents="none">
      <View testID="vf-corner" style={[styles.corner, styles.c1]} />
      <View testID="vf-corner" style={[styles.corner, styles.c2]} />
      <View testID="vf-corner" style={[styles.corner, styles.c3]} />
      <View testID="vf-corner" style={[styles.corner, styles.c4]} />
    </View>
  );
}

const CORAL = '#FB7185';
const BORDER = 2.5;

const styles = StyleSheet.create({
  frame: {
    position: 'absolute',
    top: '38%', left: '14%', right: '14%', height: '28%',
  },
  corner: {
    position: 'absolute',
    width: 26, height: 26,
    borderColor: CORAL,
    shadowColor: CORAL,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.65, shadowRadius: 6,
  },
  c1: { top: 0, left: 0, borderTopWidth: BORDER, borderLeftWidth: BORDER, borderTopLeftRadius: 8 },
  c2: { top: 0, right: 0, borderTopWidth: BORDER, borderRightWidth: BORDER, borderTopRightRadius: 8 },
  c3: { bottom: 0, left: 0, borderBottomWidth: BORDER, borderLeftWidth: BORDER, borderBottomLeftRadius: 8 },
  c4: { bottom: 0, right: 0, borderBottomWidth: BORDER, borderRightWidth: BORDER, borderBottomRightRadius: 8 },
});
