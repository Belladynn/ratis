// ratis_client/components/ui/page-title-band.tsx
//
// Generic title band shared across the (tabs) surfaces — Liste, Produit and
// Profil all consume it. Per `Ratis_handoff/lib/ratis-screens.jsx`
// (`function PageTitle`) and the V5 screenshots :
//
//   ┌────────────────────────────────────────────────┐
//   │ [←]  Title text                       [🗺][⋯] │
//   └────────────────────────────────────────────────┘
//
// Anatomy
// -------
//  - Optional `leftIcon` slot (typically a back arrow on detail screens) —
//    the consumer wires the press themselves so this primitive stays purely
//    structural.
//  - Title centered between the side slots, single-line ellipsised.
//  - `rightIcons` is a list of ReactNodes (Pressables, Text, Badges …) — the
//    primitive lays them out in a row with consistent gap and does not own
//    their styling.
//  - `titleSize` — `default` (Liste / Profil headline) or `small` (Produit
//    detail "← Fiche produit").
//
// Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`
//                    + `Produit.png` + `Profil.png`
// Reference JSX    : `Ratis_handoff/lib/ratis-screens.jsx` (`function PageTitle`)

import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

export type PageTitleBandProps = {
  title: string;
  leftIcon?: React.ReactNode;
  rightIcons?: React.ReactNode[];
  titleSize?: 'default' | 'small';
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function PageTitleBand({
  title,
  leftIcon,
  rightIcons,
  titleSize = 'default',
  testID,
  style,
}: PageTitleBandProps) {
  return (
    <View style={[styles.band, style]} testID={testID}>
      {leftIcon ? <View style={styles.leftIcon}>{leftIcon}</View> : null}
      <Text
        style={[
          styles.title,
          { fontSize: titleSize === 'small' ? 14 : 20 },
        ]}
        numberOfLines={1}
      >
        {title}
      </Text>
      {rightIcons && rightIcons.length > 0 ? (
        <View style={styles.rightIcons}>
          {rightIcons.map((icon, i) => (
            <View key={i} style={styles.iconSlot}>
              {icon}
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  band: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    backgroundColor: 'rgba(20, 26, 32, 0.94)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  title: {
    flex: 1,
    fontWeight: '900',
    letterSpacing: -0.3,
    color: '#fff',
  },
  leftIcon: { marginRight: 4 },
  rightIcons: { flexDirection: 'row', gap: 8 },
  iconSlot: {},
});

export default PageTitleBand;
