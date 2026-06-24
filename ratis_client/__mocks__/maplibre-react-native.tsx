// __mocks__/maplibre-react-native.tsx
//
// Lightweight Jest mock for @maplibre/maplibre-react-native.
//
// MapLibre Native ships TurboModules (e.g. `MLRNCameraModule`) that aren't
// registered in the Node Jest test environment — importing the real package
// throws `Invariant Violation: TurboModuleRegistry.getEnforcing(...)`. Tests
// don't care about real tile rendering, only that the React tree mounts and
// the right coordinates / style / paint props flow through.
//
// We re-export the symbols RouteMap touches (`Map`, `Camera`, `Marker`,
// `GeoJSONSource`, `Layer`) as passthrough RN Views that surface their props as
// `data-*` testID props so assertions can inspect what would be drawn without
// touching the native bridge.
//
// IMPORTANT : never import this file directly. It's wired via `moduleNameMapper`
// in `jest.config.js`.

import React from 'react';
import { View } from 'react-native';

type AnyProps = Record<string, any> & { children?: React.ReactNode };

export const Map = ({ children, testID, mapStyle }: AnyProps) => (
  <View testID={testID ?? 'mock-maplibre-map'} data-map-style={mapStyle}>
    {children}
  </View>
);

export const Camera = ({ initialViewState }: AnyProps) => (
  <View
    testID="mock-camera"
    data-bounds-west={initialViewState?.bounds?.[0]}
    data-bounds-south={initialViewState?.bounds?.[1]}
    data-bounds-east={initialViewState?.bounds?.[2]}
    data-bounds-north={initialViewState?.bounds?.[3]}
  />
);

export const Marker = ({ id, lngLat, children }: AnyProps) => (
  <View
    testID="mock-marker"
    data-marker-id={id}
    data-lng={lngLat?.[0]}
    data-lat={lngLat?.[1]}
  >
    {children}
  </View>
);

// GeoJSONSource carries the LineString coordinates; we surface point count and
// the first coordinate. The nested Layer carries the paint props.
export const GeoJSONSource = ({ children, data }: AnyProps) => {
  const coords = data?.geometry?.coordinates ?? [];
  return (
    <View
      testID="mock-geojson-source"
      data-points={coords.length}
      data-first-lng={coords[0]?.[0]}
      data-first-lat={coords[0]?.[1]}
    >
      {children}
    </View>
  );
};

export const Layer = ({ paint }: AnyProps) => (
  <View
    testID="mock-line-layer"
    data-stroke={paint?.['line-color']}
    data-width={paint?.['line-width']}
  />
);

export default { Map, Camera, Marker, GeoJSONSource, Layer };
