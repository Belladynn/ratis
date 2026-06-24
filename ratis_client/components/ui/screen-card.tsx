// ratis_client/components/ui/screen-card.tsx
import React from 'react';
import { View, StyleSheet, StyleProp, ViewStyle } from 'react-native';

export type CardAccent = 'teal' | 'coral' | 'gold' | 'violet' | 'orange' | 'warm' | 'none';

interface ScreenCardProps {
  accent?: CardAccent;
  noPadding?: boolean;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  testID?: string;
}

const ACCENT_COLORS: Record<CardAccent, string> = {
  none: 'rgba(255,255,255,0.08)',
  teal: 'rgba(77,212,179,0.22)',
  coral: 'rgba(251,113,133,0.26)',
  gold: 'rgba(255,184,0,0.2)',
  violet: 'rgba(139,92,246,0.22)',
  orange: 'rgba(251,146,60,0.22)',
  warm: 'rgba(184,138,108,0.2)',
};

export function ScreenCard({
  accent = 'none',
  noPadding = false,
  children,
  style,
  testID,
}: ScreenCardProps) {
  return (
    <View
      testID={testID}
      style={[
        styles.base,
        { borderColor: ACCENT_COLORS[accent] },
        noPadding && { padding: 0 },
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: 'rgba(255,255,255,0.035)',
    borderWidth: 1,
    borderRadius: 18,
    padding: 14,
    marginBottom: 10,
  },
});
