// ratis_client/components/profil/profil-menu-row.tsx
//
// V5 Profil menu row — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 507-543 (`function MenuRow`).
//
// Anatomy (left → right) :
//   1. 36×36 rounded square containing the row's emoji icon, tinted by
//      `iconColor` (alpha-stamped background + alpha-stamped border).
//   2. Title (white, bold) + optional subtitle (grey, 10px).
//   3. Right-edge chevron `›` (omitted when `danger`).
//
// Behaviour :
//   - Tap → invokes `onPress` (no-op when `disabled`).
//   - `disabled` greys the row to ~45% opacity and disables the press.
//   - `danger` recolours the title to coral and hides the chevron.
//   - `last` removes the bottom separator (the parent `ProfilMenuGroup`
//     passes `last` on the last child).
//
// `iconColor` is a tagged enum (`'gold' | 'violet' | 'red' | 'mint'`) rather
// than a raw hex so call sites stay readable. The hex map is local to keep
// this primitive self-contained.

import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

import { Colors } from '@/constants/theme';

export type ProfilMenuRowIconColor = 'gold' | 'violet' | 'red' | 'mint';

const ICON_HEX: Record<ProfilMenuRowIconColor, string> = {
  gold: Colors.gold,
  violet: Colors.violet,
  red: '#FB7185',
  mint: '#4DD4B3',
};

const DANGER_HEX = '#FB7185';

export type ProfilMenuRowProps = {
  icon: string;
  iconColor: ProfilMenuRowIconColor;
  title: string;
  subtitle?: string;
  onPress?: () => void;
  /** Removes the bottom separator (set by the parent on the last child). */
  last?: boolean;
  /** Recolours the title to coral and hides the chevron. */
  danger?: boolean;
  /** Greys out + disables press (V1 stub rows). */
  disabled?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function ProfilMenuRow({
  icon,
  iconColor,
  title,
  subtitle,
  onPress,
  last,
  danger,
  disabled,
  testID = 'profil-menu-row',
  style,
}: ProfilMenuRowProps) {
  const iconHex = ICON_HEX[iconColor];
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.row,
        last ? null : styles.rowDivider,
        pressed && !disabled ? styles.rowPressed : null,
        disabled ? styles.rowDisabled : null,
        style,
      ]}
    >
      <View
        style={[
          styles.iconBox,
          {
            backgroundColor: iconHex + '22',
            borderColor: iconHex + '50',
          },
        ]}
      >
        <Text style={styles.iconChar}>{icon}</Text>
      </View>
      <View style={styles.titleColumn}>
        <Text
          style={[styles.title, danger ? styles.titleDanger : null]}
          numberOfLines={1}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text style={styles.subtitle} numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {!danger ? <Text style={styles.chevron}>›</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 14,
  },
  rowDivider: {
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.05)',
  },
  rowPressed: {
    backgroundColor: 'rgba(255,255,255,0.04)',
  },
  rowDisabled: {
    opacity: 0.45,
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 11,
    borderWidth: 1.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconChar: {
    fontSize: 16,
    lineHeight: 20,
  },
  titleColumn: {
    flex: 1,
    minWidth: 0,
  },
  title: {
    fontSize: 13,
    fontWeight: '800',
    color: Colors.textPrimary,
    letterSpacing: -0.2,
  },
  titleDanger: {
    color: DANGER_HEX,
  },
  subtitle: {
    fontSize: 10,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
  chevron: {
    color: 'rgba(255,255,255,0.35)',
    fontSize: 16,
    fontWeight: '700',
  },
});

export default ProfilMenuRow;
