/**
 * Liste — RouteStopCard (V5 strict iso, Itinéraire tab).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste itineraire.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste.jsx` lines 328-394
 *                    (`RouteStopCard`).
 *
 * Layout
 * ------
 *   ┌── ① ─ store info ─────────── -2,40€ ┐
 *   │   ╎     0.4 km · 6 min · 3 articles │
 *   │   ╎     ● Lait demi-écrémé 1L       │
 *   │   ╎     ● Bananes (kg)              │
 *   │   ╎     ● Tomates cerises 250g      │
 *   └─────────────────────────────────────┘
 *
 *  - Left rail : a numbered circle (1-indexed `order`) with the store accent
 *    colour gradient + a dashed vertical line down to the next stop (skipped
 *    when `last`).
 *  - Body     : store name + "{distance} km · {time} min · {N} articles"
 *    eyebrow + a gold pill showing the savings.
 *  - Below the eyebrow, an optional flat list of item names with the accent
 *    bullet — useful for the per-stop breakdown without re-rendering the
 *    full ItemRow.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React from 'react';
import {
  Alert,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTranslation } from 'react-i18next';

import type { RouteStore } from '@/hooks/use-active-route';
import { getStoreAccent } from '@/utils/store-accent';

/**
 * Universal Google Maps directions URL — opens the native Maps app on
 * iOS (Apple Maps if user prefers, else Google Maps) and Android (Google
 * Maps), with a web-app fallback when no maps app is installed.
 *
 * Format : https://developers.google.com/maps/documentation/urls/get-started
 *
 * RGPD note : tapping the « Ouvrir dans Maps » button sends the
 * destination coordinates to the native Maps provider (Google/Apple),
 * which sits outside Ratis. The button surface MUST surface this via a
 * confirm Alert before invoking Linking.openURL.
 */
function buildMapsUrl(store: RouteStore): string {
  const destLabel = encodeURIComponent(store.store_name);
  return (
    'https://www.google.com/maps/dir/?api=1' +
    `&destination=${store.lat},${store.lng}` +
    `&destination_place_id=${destLabel}` +
    '&travelmode=driving'
  );
}

export type RouteStopCardProps = {
  store: RouteStore;
  /** Optional override for the numbered marker (defaults to `store.order`). */
  index?: number;
  /** When true, skips the trailing dashed line — for the last stop. */
  last?: boolean;
  /** Optional per-stop distance in km (V1 backend doesn't ship it yet). */
  distanceKm?: number | null;
  /** Optional per-stop duration in minutes. */
  durationMin?: number | null;
  /** Positive amount in major unit ; rendered in the savings pill. */
  savings?: number | null;
  testID?: string;
};

function fmtMoney(amount: number): string {
  return amount.toFixed(2).replace('.', ',') + '€';
}

export function RouteStopCard({
  store,
  index,
  last = false,
  distanceKm,
  durationMin,
  savings,
  testID,
}: RouteStopCardProps) {
  const { t } = useTranslation();
  const accent = getStoreAccent(store.store_name);
  const stopIndex = typeof index === 'number' ? index : store.order;
  const itemCount = store.items.length;

  // RGPD-aware deeplink to the native Maps app. We surface a confirm
  // Alert because tapping this button exits Ratis and sends the
  // destination coords to a 3rd-party Maps provider (Apple/Google).
  // Done as an Alert rather than a silent openURL so the user
  // explicitly opts-in each time (no « ne plus demander » in V1 — keep
  // the consent re-prompted ; AsyncStorage flag is a V2 polish).
  const openInMaps = React.useCallback(() => {
    Alert.alert(
      t('liste.itineraire.open_in_maps_title'),
      t('liste.itineraire.open_in_maps_warning', {
        store_name: store.store_name,
      }),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('liste.itineraire.open_in_maps_confirm'),
          onPress: () => {
            void Linking.openURL(buildMapsUrl(store)).catch(() => {
              // If openURL rejects (no Maps app + no browser), fail
              // silent — the dropdown stays visible so the user can
              // try again or cancel.
            });
          },
        },
      ],
      { cancelable: true },
    );
  }, [store, t]);

  const subtitleParts: string[] = [];
  if (typeof distanceKm === 'number') {
    subtitleParts.push(`${distanceKm.toFixed(1)} km`);
  }
  if (typeof durationMin === 'number') {
    subtitleParts.push(`${durationMin} min`);
  }
  subtitleParts.push(
    `${itemCount} article${itemCount > 1 ? 's' : ''}`,
  );

  return (
    <View style={styles.row} testID={testID ?? 'route-stop-card'}>
      {/* Left rail — numbered marker + dashed connector */}
      <View style={styles.rail}>
        <LinearGradient
          colors={[accent, `${accent}cc`]}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={styles.marker}
          testID="route-stop-card-marker"
        >
          <Text style={styles.markerTxt}>{stopIndex}</Text>
        </LinearGradient>
        {!last ? (
          <View
            style={styles.connector}
            testID="route-stop-card-connector"
          />
        ) : null}
      </View>

      {/* Body card */}
      <View
        style={[
          styles.card,
          { borderColor: `${accent}55` },
        ]}
      >
        <View style={styles.header}>
          <View style={styles.headerBody}>
            <Text style={styles.name} numberOfLines={1}>
              {store.store_name}
            </Text>
            <Text style={styles.subtitle} numberOfLines={1}>
              {subtitleParts.join(' · ')}
            </Text>
          </View>
          {typeof savings === 'number' && savings > 0 ? (
            <View style={styles.savings} testID="route-stop-card-savings">
              <Text style={styles.savingsTxt}>-{fmtMoney(savings)}</Text>
            </View>
          ) : null}
        </View>

        {itemCount > 0 ? (
          <View style={styles.itemsList}>
            {store.items.map((it) => (
              <View key={it.item_id} style={styles.itemRow}>
                <Text style={[styles.bullet, { color: accent }]}>●</Text>
                <Text style={styles.itemName} numberOfLines={1}>
                  {it.product_name}
                </Text>
              </View>
            ))}
          </View>
        ) : null}

        <Pressable
          testID="route-stop-card-open-in-maps"
          accessibilityRole="button"
          accessibilityLabel={t(
            'liste.itineraire.open_in_maps_a11y',
            { store_name: store.store_name },
          )}
          onPress={openInMaps}
          style={({ pressed }) => [
            styles.mapsCta,
            { borderColor: `${accent}66` },
            pressed && styles.mapsCtaPressed,
          ]}
        >
          <Text style={styles.mapsCtaTxt}>
            🧭  {t('liste.itineraire.open_in_maps_label')}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 12,
  },
  rail: {
    width: 32,
    alignItems: 'center',
  },
  marker: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: 'rgba(0,0,0,0.4)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  markerTxt: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '900',
    textShadowColor: 'rgba(0,0,0,0.4)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 1,
  },
  connector: {
    flex: 1,
    width: 2,
    marginTop: 4,
    backgroundColor: 'rgba(255,255,255,0.18)',
    // Dashed look approximated via opacity ; RN doesn't support
    // `border-style: dashed` on a 2px-wide bar reliably.
    opacity: 0.7,
  },
  card: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1.5,
    borderRadius: 14,
    padding: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  headerBody: {
    flex: 1,
    minWidth: 0,
  },
  name: {
    fontSize: 14,
    fontWeight: '900',
    color: '#fff',
    letterSpacing: -0.3,
  },
  subtitle: {
    fontSize: 10,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
  savings: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    backgroundColor: 'rgba(255,184,0,0.18)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.5)',
    borderRadius: 10,
  },
  savingsTxt: {
    fontSize: 11,
    fontWeight: '900',
    color: '#FFB800',
    letterSpacing: -0.1,
  },
  itemsList: {
    marginTop: 10,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.06)',
    gap: 4,
  },
  itemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  bullet: {
    fontSize: 10,
  },
  itemName: {
    flex: 1,
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.7)',
  },
  mapsCta: {
    marginTop: 10,
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderRadius: 10,
    borderWidth: 1,
    backgroundColor: 'rgba(255,255,255,0.04)',
    alignSelf: 'flex-start',
  },
  mapsCtaPressed: {
    backgroundColor: 'rgba(255,255,255,0.10)',
  },
  mapsCtaTxt: {
    fontSize: 12,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: -0.2,
  },
});

export default RouteStopCard;
