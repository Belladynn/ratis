/**
 * Produit hero card — V5 strict iso (`Produit.png`, hero band).
 *
 * Source JSX : `Ratis_handoff/lib/ratis-other-tabs.jsx` lignes 379-400.
 *
 * Anatomy :
 *
 *   ┌──────────────────────────────────────────┐
 *   │ ┌──────┐  NESPRESSO  (brand uppercase)   │
 *   │ │      │  Capsules Café Vivalto Lungo… │
 *   │ │ 80x80│  7640110350683  (EAN, mono)    │
 *   │ └──────┘                                 │
 *   └──────────────────────────────────────────┘
 *
 * Visual contract (immuable, JSX iso) :
 *   - Bg : gradient violet sombre `#2D2438 → #1F1A2E` (160deg)
 *   - Border : `1.5px rgba(168,85,247,0.3)` violet glow
 *   - Image tile 80×80, gold-cream fill `#fff → #f0e8d8`, gold border `#B47800`
 *   - Brand : violet `#A78BFA`, weight 800, size 9, letter-spacing 1.2
 *   - Name : white, weight 800, size 14
 *   - EAN : monospace, opacity 0.4, weight 700, size 9
 *
 * Token derogation : numeric values (radius/padding/fontSize) come straight
 * from the JSX — see `chunk-3-followups.md` § 10. Theme tokens (`Colors.violet`)
 * are used where they map naturally.
 */
import React from 'react';
import { View, Text, Image, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { Colors } from '@/constants/theme';

export interface ProduitHeroCardProps {
  brand?: string | null;
  name: string;
  ean: string;
  /** External photo URL (e.g. OFF). Falls back to emoji if absent. */
  photoUrl?: string | null;
  /** Emoji used when no photo (e.g. ☕, 🥖, 📦). */
  fallbackEmoji?: string;
  testID?: string;
}

export function ProduitHeroCard({
  brand,
  name,
  ean,
  photoUrl,
  fallbackEmoji = '📦',
  testID,
}: ProduitHeroCardProps) {
  return (
    <LinearGradient
      testID={testID}
      colors={['#2D2438', '#1F1A2E']}
      start={{ x: 0, y: 0 }}
      end={{ x: 0.85, y: 1 }}
      style={styles.card}
    >
      <View style={styles.imgTile} testID="hero-img">
        {photoUrl ? (
          <Image
            source={{ uri: photoUrl }}
            style={styles.imgPhoto}
            resizeMode="contain"
          />
        ) : (
          <Text style={styles.imgEmoji}>{fallbackEmoji}</Text>
        )}
      </View>
      <View style={styles.titleBlock}>
        {brand ? (
          <Text style={styles.brand} numberOfLines={1}>
            {brand.toUpperCase()}
          </Text>
        ) : null}
        <Text style={styles.name} numberOfLines={2}>
          {name}
        </Text>
        <Text style={styles.ean} numberOfLines={1}>
          {ean}
        </Text>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    padding: 16,
    borderRadius: 18,
    borderWidth: 1.5,
    borderColor: 'rgba(168,85,247,0.3)',
    shadowColor: 'rgba(60,30,100,0.55)',
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  imgTile: {
    width: 80,
    height: 80,
    borderRadius: 18,
    backgroundColor: '#fff',
    borderWidth: 2,
    borderColor: '#B47800',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    overflow: 'hidden',
  },
  imgPhoto: { width: '100%', height: '100%' },
  imgEmoji: { fontSize: 42 },
  titleBlock: { flex: 1, minWidth: 0 },
  brand: {
    fontSize: 9,
    fontWeight: '900',
    color: Colors.violet,
    letterSpacing: 1.2,
  },
  name: {
    fontSize: 14,
    fontWeight: '900',
    color: Colors.textPrimary,
    letterSpacing: -0.3,
    marginTop: 3,
    lineHeight: 17,
  },
  ean: {
    fontSize: 9,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.4)',
    marginTop: 4,
    fontFamily: 'monospace',
  },
});

export default ProduitHeroCard;
