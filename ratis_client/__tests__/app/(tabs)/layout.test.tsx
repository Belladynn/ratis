// __tests__/app/(tabs)/layout.test.tsx
//
// Restored in chunk 2 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// `(tabs)/_layout.tsx` now wires the custom `RatisTabBar`. We mock both
// expo-router's `Tabs` and the tab bar to verify the layout renders without
// crash (smoke).

import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('expo-router', () => {
  const React = require('react');
  const MockTabsScreen = () => null;
  const MockTabs = ({ children }: { children: React.ReactNode }) => <>{children}</>;
  (MockTabs as any).Screen = MockTabsScreen;
  return { Tabs: MockTabs };
});

jest.mock('@/components/navigation/ratis-tab-bar', () => ({
  RatisTabBar: () => null,
}));

import TabLayout from '@/app/(tabs)/_layout';

describe('TabLayout (V5 strict iso)', () => {
  it('renders without crash', () => {
    expect(() => render(<TabLayout />)).not.toThrow();
  });
});
