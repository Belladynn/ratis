// ratis_client/components/profil/profil-menu-group.tsx
//
// V5 Profil menu group — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 545-559 (`function MenuGroup`).
//
// Anatomy :
//   - Uppercase title above the card, tinted by accent (gold / violet / coral).
//   - Card surface `#27293A` with a 1.5px accent border (alpha-stamped),
//     radius 18, dark drop shadow.
//   - Children rows (`ProfilMenuRow`) stacked, each row owns its own
//     bottom-border separator. Last child should pass `last`.
//
// Accent palette
// --------------
//  - `rewards` → `Colors.gold` (matches the JSX `color="#FFB800"`)
//  - `account` → `Colors.violet`
//  - `danger`  → coral `#FB7185` (this is NOT `Colors.coral` `#EF4444` — the
//    JSX uses a softer rose ; keeping iso per `chunk-3-followups.md` § 10)
//
// The accent is exposed as a discriminated string union rather than a raw
// hex — keeps the call sites readable and lets us add/rename palettes
// without churning every screen.

import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

import { Colors } from '@/constants/theme';

export type ProfilMenuGroupAccent = 'rewards' | 'account' | 'danger';

const ACCENT_HEX: Record<ProfilMenuGroupAccent, string> = {
  rewards: Colors.gold,
  account: Colors.violet,
  danger: '#FB7185',
};

export type ProfilMenuGroupProps = {
  /** Group label (e.g. "Récompenses"). Rendered upper-cased. */
  label: string;
  accent: ProfilMenuGroupAccent;
  children: React.ReactNode;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function ProfilMenuGroup({
  label,
  accent,
  children,
  testID,
  style,
}: ProfilMenuGroupProps) {
  const accentHex = ACCENT_HEX[accent];
  return (
    <View testID={testID} style={style}>
      <Text style={[styles.label, { color: accentHex }]}>
        {label.toUpperCase()}
      </Text>
      <View
        style={[
          styles.card,
          { borderColor: accentHex + '30' },
        ]}
      >
        {children}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  label: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    paddingLeft: 14,
    marginBottom: 8,
  },
  card: {
    backgroundColor: Colors.surface,
    borderWidth: 1.5,
    borderRadius: 18,
    overflow: 'hidden',
    // Heavy dark drop — JSX layers a softer 22px shadow on top, but RN can't
    // composite ; keep the strongest layer.
    shadowColor: 'rgba(0,0,0,0.4)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
});

export default ProfilMenuGroup;
