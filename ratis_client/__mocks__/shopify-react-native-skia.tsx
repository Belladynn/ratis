// __mocks__/shopify-react-native-skia.tsx
//
// Lightweight Jest mock for @shopify/react-native-skia.
//
// Skia is a C++ native module that doesn't load under the Jest "node" test
// environment. The dashboard components use it for the Tirelire (Jar) hero
// rendering — but tests don't care about pixel output, only that the React
// tree mounts without crashing.
//
// We re-export every symbol the dashboard touches as either a passthrough
// React component (rendering a `<View />`) or a simple function/identity. If
// a new symbol is introduced and a test fails with `undefined is not a
// function`, add it here.
//
// IMPORTANT : never import this file directly. It's wired via
// `moduleNameMapper` in `jest.config.js`.

import React from 'react';
import { View, type ViewProps } from 'react-native';

type AnyProps = ViewProps & { children?: React.ReactNode } & Record<string, unknown>;

// Generic passthrough component — renders children inside a View so that
// React Test Renderer can walk the tree. We strip Skia-specific props that
// would warn under React Native (e.g. `cx`, `cy`, `r`, `path`, `transform`
// objects).
function makeComponent(displayName: string) {
  const Comp = ({ children, ...rest }: AnyProps) => {
    // Forward only View-friendly props (testID, style if it's a stylesheet).
    const { testID } = rest as { testID?: string };
    return <View testID={testID}>{children}</View>;
  };
  Comp.displayName = displayName;
  return Comp;
}

// Containers
export const Canvas = makeComponent('Canvas');
export const Group = makeComponent('Group');
export const Mask = makeComponent('Mask');

// Shapes
export const Rect = makeComponent('Rect');
export const RoundedRect = makeComponent('RoundedRect');
export const Circle = makeComponent('Circle');
export const Path = makeComponent('Path');
export const Line = makeComponent('Line');
export const Oval = makeComponent('Oval');

// Paint / fills
export const Fill = makeComponent('Fill');
export const Paint = makeComponent('Paint');

// Gradients (rendered as no-op components, just children)
export const LinearGradient = makeComponent('SkiaLinearGradient');
export const RadialGradient = makeComponent('SkiaRadialGradient');

// Text
export const Text = makeComponent('SkiaText');

// Hooks / utilities returning safe defaults
export const useFont = () => null;
export const useImage = () => null;
export const useClock = () => ({ value: 0 });
export const useFrameCallback = () => ({ setActive: () => {}, isActive: false });
export const useSharedValueEffect = () => {};
export const useValue = (init: unknown) => ({ current: init });
export const useComputedValue = (fn: () => unknown) => ({ current: fn() });
export const useTouchHandler = () => () => {};

// Skia object factory used to build paths / rrects
const makePathStub = () => ({
  moveTo: function () { return this; },
  lineTo: function () { return this; },
  cubicTo: function () { return this; },
  quadTo: function () { return this; },
  addCircle: function () { return this; },
  addRect: function () { return this; },
  addRRect: function () { return this; },
  addOval: function () { return this; },
  close: function () { return this; },
  reset: function () { return this; },
});
export const Skia = {
  Path: {
    Make: makePathStub,
    MakeFromSVGString: (_svg: string) => makePathStub(),
  },
  XYWHRect: (x: number, y: number, w: number, h: number) => ({ x, y, width: w, height: h }),
  RRectXY: (rect: unknown, rx: number, ry: number) => ({ rect, rx, ry }),
  Color: (c: string) => c,
  Matrix: () => ({
    translate: function () { return this; },
    rotate: function () { return this; },
    scale: function () { return this; },
  }),
};

// Common typed exports
export const vec = (x: number, y: number) => ({ x, y });
export const rect = (x: number, y: number, width: number, height: number) => ({
  x, y, width, height,
});
export const rrect = (r: unknown, rx: number, ry: number) => ({ rect: r, rx, ry });

// Default export — some tests import default
export default {
  Canvas, Group, Rect, Circle, Path, Fill, Paint,
  LinearGradient, RadialGradient, Skia, vec, rect, rrect,
};
