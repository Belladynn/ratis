// components/liste/route-map.tsx
//
// Native map for the Itinéraire tab — renders a MapLibre Native map with one
// numbered marker per stop + the OSRM-encoded polyline connecting them. Falls
// back to straight lines between markers when the backend hasn't shipped
// `route_polyline` yet (defensive — `RouteFull.route_polyline` is declared
// `string | null`).
//
// History (don't repeat) :
//   - PR #441 introduced MapLibre Native (RGPD independence, no Google SDK)
//   - PR #444 reverted to `react-native-maps` + Google provider (PO directive
//     2026-05-14, betting on Google Cloud billing being available)
//   - This PR : revert #444 back to MapLibre. Reason — the Google Cloud billing
//     account could not be activated, so Google Maps is off the table. We keep
//     MapLibre Native but serve tiles from MapTiler (free tier, EU-hosted, no
//     billing — just a client API key) instead of the public OSM endpoints
//     (OSMF tile policy forbids application usage). RGPD : disclosed in
//     PRIVACY.md § « Cartographie & Itinéraire ».
//
// Why MapTiler tiles instead of the public OSM servers :
//   - OSMF tile-usage policy explicitly forbids using a.b.c.tile.openstreetmap.org
//     in an application — those endpoints are for openstreetmap.org itself.
//   - MapTiler free tier = no billing account required, just a per-app API key,
//     EU-hosted (RGPD-friendly), vector tiles built from OSM data.
//
// Native dependency reminder (R34 + KP-92) : `@maplibre/maplibre-react-native`
// ships native modules → change is NOT OTAable. Requires a fresh `eas build`.
// `platforms: ["ios","android"]` in app.json keeps the web bundler from
// choking on the native-only module during `eas update`.

import {
  Camera,
  GeoJSONSource,
  Layer,
  Map,
  Marker,
} from '@maplibre/maplibre-react-native';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';

import { Colors } from '@/constants/theme';
import type { RouteStore } from '@/hooks/use-active-route';

export interface RouteMapProps {
  stores: RouteStore[];
  /** OSRM-encoded polyline (Google polyline algorithm, precision 5). When
   *  null/undefined the map falls back to straight lines between markers. */
  polylineEncoded?: string | null;
  testID?: string;
}

/** A `[lng, lat]` tuple — MapLibre RN's canonical coordinate representation. */
type LngLat = [number, number];

/**
 * Resolve the MapTiler API key, injected at JS runtime via the `EXPO_PUBLIC_*`
 * mechanism (provisioned in the EAS environment, never committed — R17).
 * Returns `''` when unset → the component renders a readable fallback rather
 * than a blank tile canvas. Read inside the component (not a module-level
 * const) so the value is re-evaluated per render and trivially testable.
 */
function maptilerKey(): string {
  return process.env.EXPO_PUBLIC_MAPTILER_KEY ?? '';
}

/** MapTiler vector style URL. The key is appended as a query param — the only
 *  place the key enters the render path. */
function maptilerStyleUrl(key: string): string {
  return `https://api.maptiler.com/maps/streets-v2/style.json?key=${key}`;
}

/**
 * Decode a Google polyline-algorithm string (precision 5) into `[lng, lat]`
 * pairs (MapLibre order — note this is the inverse of `@mapbox/polyline`,
 * which returns `[lat, lng]`).
 *
 * Inlined to avoid pulling a 3rd-party polyline package — the algorithm is
 * ~30 lines of pure JS and stable since Google published it in 2008.
 *
 * @see https://developers.google.com/maps/documentation/utilities/polylinealgorithm
 */
function decodePolyline(encoded: string): LngLat[] {
  const coordinates: LngLat[] = [];
  let index = 0;
  let lat = 0;
  let lng = 0;
  while (index < encoded.length) {
    let shift = 0;
    let result = 0;
    let byte: number;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    const dLat = result & 1 ? ~(result >> 1) : result >> 1;
    lat += dLat;

    shift = 0;
    result = 0;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    const dLng = result & 1 ? ~(result >> 1) : result >> 1;
    lng += dLng;

    coordinates.push([lng / 1e5, lat / 1e5]);
  }
  return coordinates;
}

/**
 * Compute the bounding box `[west, south, east, north]` that comfortably fits
 * every store. A 20 % padding factor on each axis (min 0.01°, ~1 km) prevents
 * markers from sitting flush against the edge when the bbox would otherwise be
 * degenerate (single store, or all stores in one neighbourhood).
 */
function computeBounds(stores: RouteStore[]): [number, number, number, number] {
  const lats = stores.map((s) => s.lat);
  const lngs = stores.map((s) => s.lng);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const padLat = Math.max(0.01, (maxLat - minLat) * 0.2);
  const padLng = Math.max(0.01, (maxLng - minLng) * 0.2);
  return [minLng - padLng, minLat - padLat, maxLng + padLng, maxLat + padLat];
}

function decodeOrFallback(
  encoded: string | null | undefined,
  stores: RouteStore[],
): LngLat[] {
  if (encoded) {
    return decodePolyline(encoded);
  }
  // Fallback : straight lines between markers in route order.
  return stores.map((s) => [s.lng, s.lat] as LngLat);
}

export function RouteMap({ stores, polylineEncoded, testID }: RouteMapProps) {
  const { t } = useTranslation();
  const key = maptilerKey();

  if (stores.length === 0) {
    return null;
  }

  // No MapTiler key → don't render a blank tile canvas; surface a readable
  // message instead. The stop list below the map still shows every stop.
  if (key === '') {
    return (
      <View
        style={[styles.mapWrap, styles.fallback]}
        testID={testID ?? 'liste-route-map'}
      >
        <Text style={styles.fallbackText}>
          {t('liste.itineraire.map_unavailable')}
        </Text>
      </View>
    );
  }

  const bounds = computeBounds(stores);
  const lineCoordinates = decodeOrFallback(polylineEncoded, stores);
  const drawLine = lineCoordinates.length > 1;

  return (
    <View style={styles.mapWrap} testID={testID ?? 'liste-route-map'}>
      <Map style={styles.map} mapStyle={maptilerStyleUrl(key)}>
        <Camera
          initialViewState={{
            bounds,
            padding: { top: 32, bottom: 32, left: 32, right: 32 },
          }}
        />
        {drawLine ? (
          <GeoJSONSource
            id="route-source"
            data={{
              type: 'Feature',
              properties: {},
              geometry: { type: 'LineString', coordinates: lineCoordinates },
            }}
          >
            <Layer
              id="route-line"
              type="line"
              paint={{
                'line-color': Colors.terracotta,
                'line-width': 3,
              }}
            />
          </GeoJSONSource>
        ) : null}
        {stores.map((s, i) => (
          <Marker key={s.store_id} id={s.store_id} lngLat={[s.lng, s.lat]}>
            <View style={styles.markerPill}>
              <Text style={styles.markerLabel}>{i + 1}</Text>
            </View>
          </Marker>
        ))}
      </Map>
    </View>
  );
}

const styles = StyleSheet.create({
  mapWrap: {
    height: 240,
    borderRadius: 14,
    overflow: 'hidden',
    marginBottom: 12,
  },
  map: { flex: 1 },
  fallback: {
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.surface,
    paddingHorizontal: 16,
  },
  fallbackText: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
  },
  markerPill: {
    minWidth: 24,
    height: 24,
    paddingHorizontal: 6,
    borderRadius: 12,
    backgroundColor: Colors.terracotta,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#FFFFFF',
  },
  markerLabel: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '700',
  },
});
