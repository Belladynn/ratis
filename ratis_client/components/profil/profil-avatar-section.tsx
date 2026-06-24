// ratis_client/components/profil/profil-avatar-section.tsx
//
// V5 Profil avatar block — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 570-593 (the `<>… {/* Avatar block */} …</>` cluster of the
// `ProfilScreen`).
//
// Anatomy (top → bottom) :
//   1. Gradient circle 84×84 (terracotta-ish brown) with a 4px gold halo and
//      a thick 3D stacked shadow. Renders the rat emoji 🐀 by default but
//      accepts an optional `avatarEmoji` override (e.g. premium skins).
//   2. Display name (e.g. "Marie L.") — white 18px bold.
//   3. Row : "@handle" muted + gold "★ Niv. {level}" pill badge.
//
// JSX iso : the JSX uses `<button>` for the avatar so the user can tap it ;
// we mirror that with a `Pressable` and forward presses through `onPressAvatar`.
//
// Tokens : the gradient `[#C4895C, #8B5A2B]` and the gold halo `rgba(255,184,0,0.4)`
// are JSX iso values — no exact match in `Colors.*` for the brown stack
// (closest is `Colors.terracotta` `#DA7756` but the JSX is intentionally a
// warmer/duller "rat fur" hue). Token derogation per `chunk-3-followups.md`
// § 10.

import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

export type ProfilAvatarSectionProps = {
  name: string;
  handle: string;
  level: number;
  /** Override the default 🐀 emoji (V2 — premium skins). */
  avatarEmoji?: string;
  onPressAvatar?: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function ProfilAvatarSection({
  name,
  handle,
  level,
  avatarEmoji = '🐀',
  onPressAvatar,
  testID = 'profil-avatar-section',
  style,
}: ProfilAvatarSectionProps) {
  return (
    <View testID={testID} style={[styles.container, style]}>
      <Pressable
        testID="profil-avatar-press"
        accessibilityRole="button"
        accessibilityLabel={name}
        onPress={onPressAvatar}
        hitSlop={6}
      >
        <View style={styles.haloRing}>
          <LinearGradient
            colors={['#C4895C', '#8B5A2B']}
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            style={styles.gradientCircle}
          >
            <Text style={styles.emoji}>{avatarEmoji}</Text>
          </LinearGradient>
        </View>
      </Pressable>

      <Text style={styles.name} numberOfLines={1}>
        {name}
      </Text>

      <View style={styles.identityRow}>
        <Text style={styles.handle} numberOfLines={1}>
          {handle}
        </Text>
        <View testID="profil-level-badge" style={styles.levelBadgeOuter}>
          <LinearGradient
            colors={['#FFE066', '#FFB800']}
            start={{ x: 0, y: 0 }}
            end={{ x: 0, y: 1 }}
            style={styles.levelBadgeInner}
          >
            <Text style={styles.levelBadgeText}>{`★ Niv. ${level}`}</Text>
          </LinearGradient>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    gap: 8,
    marginTop: 4,
  },
  haloRing: {
    // 4px gold halo around the gradient circle (JSX `border: '4px solid
    // rgba(255,184,0,0.4)'` rendered via padding so the inner gradient stays
    // a perfect circle without RN's inner-vs-outer border quirks).
    width: 84,
    height: 84,
    borderRadius: 42,
    padding: 4,
    backgroundColor: 'rgba(255,184,0,0.4)',
    // 3D stacked shadow — dark drop shadow + soft falloff. RN does not
    // composite multiple `box-shadow`s natively, so we keep the heaviest
    // (the dark drop) which carries the V5 weight feel.
    shadowColor: 'rgba(60,30,10,0.7)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 6,
  },
  gradientCircle: {
    flex: 1,
    borderRadius: 38,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emoji: {
    fontSize: 44,
    // Cancel default vertical letter-frame so the rat reads visually centred.
    lineHeight: 50,
  },
  name: {
    fontSize: 18,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.4,
  },
  identityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  handle: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.55)',
  },
  levelBadgeOuter: {
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: '#B47800',
    overflow: 'hidden',
    shadowColor: '#8F5E00',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 2,
  },
  levelBadgeInner: {
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  levelBadgeText: {
    fontSize: 10,
    fontWeight: '900',
    color: '#3A2200',
    letterSpacing: -0.1,
  },
});

export default ProfilAvatarSection;
