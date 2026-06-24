// ratis_client/components/ui/screen-background.tsx
import React from 'react';
import { View, Image, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

interface ScreenBackgroundProps {
  testID?: string;
}

export function ScreenBackground({ testID }: ScreenBackgroundProps = {}) {
  return (
    <View testID={testID} style={StyleSheet.absoluteFill} pointerEvents="none">
      {/* Layer 1 — image */}
      <Image
        testID="screen-bg-image"
        source={require('@/assets/images/bg-ratis.jpg')}
        style={styles.image}
        resizeMode="cover"
      />
      {/* Layer 2 — tint teal-bleu subtil */}
      <LinearGradient
        colors={[
          'rgba(12,22,30,0.35)',
          'rgba(18,40,52,0.25)',
          'rgba(22,55,70,0.15)',
        ]}
        locations={[0, 0.7, 1]}
        style={StyleSheet.absoluteFill}
      />
      {/* Layer 3 — FOG vertical */}
      <LinearGradient
        testID="screen-bg-fog"
        colors={[
          'rgba(8,14,20,0.82)',
          'rgba(10,18,26,0.84)',
          'rgba(12,22,32,0.5)',
          'rgba(15,28,40,0.2)',
          'transparent',
        ]}
        locations={[0, 0.55, 0.75, 0.9, 1]}
        style={StyleSheet.absoluteFill}
      />
      {/* Glows */}
      <View testID="screen-bg-glow-teal" style={styles.glowTeal} />
      <View testID="screen-bg-glow-amber" style={styles.glowAmber} />
    </View>
  );
}

const styles = StyleSheet.create({
  image: {
    position: 'absolute',
    bottom: 0,
    left: '-60%',       // (220% - 100%) / 2 = 60% overflow chaque cote pour centrer
    width: '220%',
    aspectRatio: 1.75,
    opacity: 0.92,
  },
  glowTeal: {
    position: 'absolute',
    top: -80, right: -80,
    width: 280, height: 280,
    borderRadius: 140,
    backgroundColor: 'rgba(77,212,179,0.18)',
    opacity: 0.5,
  },
  glowAmber: {
    position: 'absolute',
    bottom: -60, left: -60,
    width: 200, height: 200,
    borderRadius: 100,
    backgroundColor: 'rgba(255,184,0,0.10)',
    opacity: 0.5,
  },
});
