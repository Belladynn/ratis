// components/navigation/ratis-tab-bar.tsx
//
// Custom bottom tab bar — strict iso V5.
// Source JSX : Ratis_handoff/lib/ratis-real-v4.jsx lines 731-779 (`RatisTabBar`).
// ARCH       : ratis_client/ARCH_frontend_strict_iso.md § Tab bar bottom (272-279).
//
// 5 tabs ordered : index | liste | scan (FAB centered -20px top) | produit | profil.
// Active tab     : indicator dot 4×4 terracotta + icon/label colored terracotta.
// Inactive       : rgba(255,255,255,0.45).
// Background     : rgba(22,32,40,0.95) + BlurView intensity 12 (backdrop-filter blur 12px).
// Border-top     : 1px rgba(255,255,255,0.06), zIndex 20.
// Scan FAB       : 60×60, border 2.5px terracotta, bg rgba(22,32,40,0.98), 3-layer shadow.

import { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { BlurView } from 'expo-blur';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { IconSymbol, type IconSymbolName } from '@/components/ui/icon-symbol';
import { Colors } from '@/constants/theme';

const TAB_ORDER = ['index', 'liste', 'scan', 'produit', 'profil'] as const;
type TabName = (typeof TAB_ORDER)[number];

const TAB_META: Record<TabName, { icon: IconSymbolName; label: string }> = {
  index: { icon: 'house.fill', label: 'Accueil' },
  liste: { icon: 'list.bullet', label: 'Liste' },
  scan: { icon: 'camera.fill', label: 'Scan' },
  produit: { icon: 'cube.fill', label: 'Produit' },
  profil: { icon: 'person.fill', label: 'Profil' },
};

const INACTIVE_COLOR = 'rgba(255,255,255,0.45)';

export function RatisTabBar({ state, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const focusedRouteName = state.routes[state.index]?.name;

  return (
    <BlurView
      intensity={12}
      tint="dark"
      style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 8) }]}
    >
      <View style={styles.row}>
        {TAB_ORDER.map((name) => {
          const route = state.routes.find((r) => r.name === name);
          if (!route) return null;
          const isFocused = focusedRouteName === name;
          const isFab = name === 'scan';

          const onPress = () => {
            const event = navigation.emit({
              type: 'tabPress',
              target: route.key,
              canPreventDefault: true,
            });
            if (!isFocused && !event.defaultPrevented) {
              navigation.navigate(route.name as never);
            }
          };

          if (isFab) {
            return (
              <Pressable
                key={name}
                testID={`tab-${name}`}
                accessibilityRole="button"
                accessibilityState={{ selected: isFocused }}
                accessibilityLabel={TAB_META[name].label}
                onPress={onPress}
                style={styles.fabSlot}
              >
                <View style={styles.fab}>
                  <IconSymbol
                    name={TAB_META[name].icon}
                    size={26}
                    color={Colors.terracotta}
                  />
                </View>
                <Text style={styles.fabLabel}>{TAB_META[name].label}</Text>
              </Pressable>
            );
          }

          const tint = isFocused ? Colors.terracotta : INACTIVE_COLOR;
          return (
            <Pressable
              key={name}
              testID={`tab-${name}`}
              accessibilityRole="button"
              accessibilityState={{ selected: isFocused }}
              accessibilityLabel={TAB_META[name].label}
              onPress={onPress}
              style={styles.tab}
            >
              {isFocused && <View style={styles.dot} />}
              <IconSymbol name={TAB_META[name].icon} size={22} color={tint} />
              <Text style={[styles.label, { color: tint }]}>
                {TAB_META[name].label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </BlurView>
  );
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: 'rgba(22,32,40,0.95)',
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(255,255,255,0.06)',
    paddingTop: 10,
    paddingHorizontal: 4,
    zIndex: 20,
    overflow: 'visible',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-around',
    overflow: 'visible',
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'flex-start',
    gap: 4,
    paddingTop: 6,
    position: 'relative',
  },
  dot: {
    position: 'absolute',
    top: 0,
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.terracotta,
  },
  label: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: -0.1,
  },
  fabSlot: {
    width: 78,
    alignItems: 'center',
    justifyContent: 'flex-start',
  },
  fab: {
    width: 60,
    height: 60,
    borderRadius: 30,
    marginTop: -20,
    borderWidth: 2.5,
    borderColor: Colors.terracotta,
    backgroundColor: 'rgba(22,32,40,0.98)',
    alignItems: 'center',
    justifyContent: 'center',
    // RN cannot stack 3 distinct shadow layers like CSS box-shadow does.
    // Approximation : keep the warm terracotta drop-glow ; the dark relief
    // pseudo-3D layer + inset highlight are not representable cross-platform.
    shadowColor: Colors.terracotta,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    elevation: 8,
    zIndex: 10,
  },
  fabLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: Colors.terracotta,
    marginTop: 4,
    letterSpacing: -0.1,
  },
});
