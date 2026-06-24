// __tests__/components/liste/route-map.test.tsx
//
// Native-map coverage for the Itinéraire tab — MapLibre Native + MapTiler tiles
// implementation (reverts PR #444's react-native-maps + Google provider, since
// the Google Cloud billing account couldn't be activated).
//
// `@maplibre/maplibre-react-native` ships native modules (TurboModules) that
// jest can't load, so it's mocked repo-wide via `moduleNameMapper` →
// `__mocks__/maplibre-react-native.tsx`. That passthrough mock surfaces the
// rendered prop values (mapStyle, lngLat, bounds, GeoJSON line) as `data-*`
// testID props so we can assert what would be drawn without the native bridge.
//
// The component reads `EXPO_PUBLIC_MAPTILER_KEY` inside its render body (not at
// module load), so we drive the key per test via `process.env` in beforeEach /
// the fallback test. A non-empty placeholder is enough; the mock never performs
// a real tile fetch (R17 — no real key committed).

import React from 'react';
import { render } from '@testing-library/react-native';

import type { RouteStore } from '@/hooks/use-active-route';
import { RouteMap } from '@/components/liste/route-map';

const makeStores = (n: number): RouteStore[] =>
  Array.from({ length: n }, (_, i) => ({
    store_id: `store-${i}`,
    store_name: `Magasin ${i}`,
    retailer: 'Lidl',
    address: `${i} rue de test`,
    lat: 48.85 + i * 0.01,
    lng: 2.35 + i * 0.01,
    order: i + 1,
    subtotal: 0,
    items: [],
  }));

// Tiny helper to encode a polyline in test fixtures (Google polyline algorithm,
// same one OSRM emits with `geometries=polyline`).
function encodePolyline(pairs: [number, number][]): string {
  const encode = (value: number) => {
    value = value < 0 ? ~(value << 1) : value << 1;
    let result = '';
    while (value >= 0x20) {
      result += String.fromCharCode((0x20 | (value & 0x1f)) + 63);
      value >>= 5;
    }
    result += String.fromCharCode(value + 63);
    return result;
  };
  let lat = 0;
  let lng = 0;
  let out = '';
  for (const [pLat, pLng] of pairs) {
    const eLat = Math.round(pLat * 1e5);
    const eLng = Math.round(pLng * 1e5);
    out += encode(eLat - lat) + encode(eLng - lng);
    lat = eLat;
    lng = eLng;
  }
  return out;
}

describe('RouteMap (MapLibre Native + MapTiler tiles)', () => {
  const prevKey = process.env.EXPO_PUBLIC_MAPTILER_KEY;
  beforeEach(() => {
    process.env.EXPO_PUBLIC_MAPTILER_KEY = 'test-maptiler-key';
  });
  afterEach(() => {
    if (prevKey === undefined) {
      delete process.env.EXPO_PUBLIC_MAPTILER_KEY;
    } else {
      process.env.EXPO_PUBLIC_MAPTILER_KEY = prevKey;
    }
  });

  it('renders nothing when stores is empty (parent shows cards-only view)', () => {
    const { queryByTestId } = render(
      <RouteMap stores={[]} polylineEncoded={null} />,
    );
    expect(queryByTestId('liste-route-map')).toBeNull();
  });

  it('renders the MapLibre map with the MapTiler style URL + one Marker per store', () => {
    const { getByTestId, getAllByTestId } = render(
      <RouteMap stores={makeStores(3)} polylineEncoded={null} />,
    );
    expect(getByTestId('liste-route-map')).toBeTruthy();
    const map = getByTestId('mock-maplibre-map');
    expect(map).toBeTruthy();
    // The MapTiler style URL carries the key from EXPO_PUBLIC_MAPTILER_KEY.
    expect(map.props['data-map-style']).toContain('api.maptiler.com');
    expect(map.props['data-map-style']).toContain('key=test-maptiler-key');
    expect(getAllByTestId('mock-marker')).toHaveLength(3);
  });

  it('passes [lng, lat] coordinates to each Marker', () => {
    const { getAllByTestId } = render(
      <RouteMap stores={makeStores(2)} polylineEncoded={null} />,
    );
    const markers = getAllByTestId('mock-marker');
    expect(Number(markers[0].props['data-lng'])).toBeCloseTo(2.35, 4);
    expect(Number(markers[0].props['data-lat'])).toBeCloseTo(48.85, 4);
  });

  it('decodes the encoded polyline when present and renders a line source with N points', () => {
    const encoded = encodePolyline([
      [48.85, 2.35],
      [48.86, 2.36],
      [48.87, 2.37],
    ]);
    const { getByTestId } = render(
      <RouteMap stores={makeStores(2)} polylineEncoded={encoded} />,
    );
    const source = getByTestId('mock-geojson-source');
    expect(Number(source.props['data-points'])).toBe(3);
    const layer = getByTestId('mock-line-layer');
    expect(layer.props['data-stroke']).toBe('#DA7756');
    expect(Number(layer.props['data-width'])).toBe(3);
    // First decoded point matches the first encoded pair within float
    // precision (1e-5 ≈ 1 m). MapLibre order is [lng, lat].
    expect(source.props['data-first-lng']).toBeCloseTo(2.35, 4);
    expect(source.props['data-first-lat']).toBeCloseTo(48.85, 4);
  });

  it('falls back to straight lines between stores when polyline is null', () => {
    const { getByTestId } = render(
      <RouteMap stores={makeStores(4)} polylineEncoded={null} />,
    );
    const source = getByTestId('mock-geojson-source');
    // Fallback connects each store directly — N stores → N points.
    expect(Number(source.props['data-points'])).toBe(4);
  });

  it('omits the line when only one store is present (no line to draw)', () => {
    const { queryByTestId } = render(
      <RouteMap stores={makeStores(1)} polylineEncoded={null} />,
    );
    expect(queryByTestId('mock-geojson-source')).toBeNull();
  });

  it('passes camera bounds covering all stores to the map', () => {
    const stores = makeStores(3); // lats 48.85→48.87, lngs 2.35→2.37
    const { getByTestId } = render(
      <RouteMap stores={stores} polylineEncoded={null} />,
    );
    const camera = getByTestId('mock-camera');
    const west = Number(camera.props['data-bounds-west']);
    const south = Number(camera.props['data-bounds-south']);
    const east = Number(camera.props['data-bounds-east']);
    const north = Number(camera.props['data-bounds-north']);
    // The bounds must contain every store coordinate.
    for (const s of stores) {
      expect(s.lng).toBeGreaterThanOrEqual(west);
      expect(s.lng).toBeLessThanOrEqual(east);
      expect(s.lat).toBeGreaterThanOrEqual(south);
      expect(s.lat).toBeLessThanOrEqual(north);
    }
  });
});

describe('RouteMap — missing MapTiler key fallback', () => {
  const prevKey = process.env.EXPO_PUBLIC_MAPTILER_KEY;
  afterEach(() => {
    if (prevKey === undefined) {
      delete process.env.EXPO_PUBLIC_MAPTILER_KEY;
    } else {
      process.env.EXPO_PUBLIC_MAPTILER_KEY = prevKey;
    }
  });

  it('renders a readable message instead of a blank tile canvas when the key is unset', () => {
    // The component reads the key inside its render body, so simply unsetting
    // the env var before render drives the fallback branch.
    delete process.env.EXPO_PUBLIC_MAPTILER_KEY;
    const { getByTestId, queryByTestId } = render(
      <RouteMap stores={makeStores(3)} polylineEncoded={null} />,
    );
    // The container still renders (so the parent layout is stable)…
    expect(getByTestId('liste-route-map')).toBeTruthy();
    // …but the native map is NOT mounted (no markers, no map).
    expect(queryByTestId('mock-maplibre-map')).toBeNull();
    // The fallback message ("Carte indisponible …") is shown.
    expect(getByTestId('liste-route-map')).toHaveTextContent(
      /Carte indisponible/,
    );
  });
});
