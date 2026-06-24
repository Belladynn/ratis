// __tests__/components/ratis-tab-bar.test.tsx
//
// Restored in chunk 2 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Component lives at `@/components/navigation/ratis-tab-bar` (renamed from
// the V4 root path `@/components/ratis-tab-bar`). Original test contents at
// commit 01d62ff — we kept the same behavioural contract (5 tabs, focus
// state, navigate-on-press, defaultPrevented respect) but updated the
// import path + component name (`RatisTabBar` from new location).

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-blur', () => {
  const { View } = require('react-native');
  return { BlurView: ({ children, style }: any) => <View style={style}>{children}</View> };
});

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

jest.mock('@/components/ui/icon-symbol', () => ({
  IconSymbol: ({ name }: { name: string }) => {
    const { Text } = require('react-native');
    return <Text>{`icon:${name}`}</Text>;
  },
}));

import { RatisTabBar } from '@/components/navigation/ratis-tab-bar';

function makeProps(focusedIndex = 0) {
  const routes = [
    { key: 'index',   name: 'index',   params: undefined },
    { key: 'liste',   name: 'liste',   params: undefined },
    { key: 'scan',    name: 'scan',    params: undefined },
    { key: 'produit', name: 'produit', params: undefined },
    { key: 'profil',  name: 'profil',  params: undefined },
  ];
  return {
    state: {
      index: focusedIndex,
      routes,
      routeNames: routes.map(r => r.name),
    },
    navigation: {
      emit: jest.fn().mockReturnValue({ defaultPrevented: false }),
      navigate: jest.fn(),
    },
    descriptors: {},
    insets: { top: 0, bottom: 0, left: 0, right: 0 },
  } as any;
}

describe('RatisTabBar', () => {
  it('renders 5 tabs', () => {
    const { getByTestId } = render(<RatisTabBar {...makeProps()} />);
    expect(getByTestId('tab-index')).toBeTruthy();
    expect(getByTestId('tab-liste')).toBeTruthy();
    expect(getByTestId('tab-scan')).toBeTruthy();
    expect(getByTestId('tab-produit')).toBeTruthy();
    expect(getByTestId('tab-profil')).toBeTruthy();
  });

  it('marks the active tab with accessibilityState selected', () => {
    const { getByTestId } = render(<RatisTabBar {...makeProps(0)} />);
    const activeTab = getByTestId('tab-index');
    expect(activeTab.props.accessibilityState).toEqual(
      expect.objectContaining({ selected: true }),
    );
  });

  it('marks an inactive tab as not selected', () => {
    const { getByTestId } = render(<RatisTabBar {...makeProps(0)} />);
    const inactiveTab = getByTestId('tab-liste');
    expect(inactiveTab.props.accessibilityState).toEqual(
      expect.objectContaining({ selected: false }),
    );
  });

  it('calls navigate when an unfocused tab is pressed', () => {
    const props = makeProps(0);
    const { getByTestId } = render(<RatisTabBar {...props} />);
    fireEvent.press(getByTestId('tab-liste'));
    expect(props.navigation.navigate).toHaveBeenCalledWith('liste');
  });

  it('does NOT call navigate when the focused tab is pressed', () => {
    const props = makeProps(0);
    const { getByTestId } = render(<RatisTabBar {...props} />);
    fireEvent.press(getByTestId('tab-index'));
    expect(props.navigation.navigate).not.toHaveBeenCalled();
  });

  it('renders the scan button with label "Scan"', () => {
    const { getByTestId, getAllByText } = render(<RatisTabBar {...makeProps()} />);
    expect(getByTestId('tab-scan')).toBeTruthy();
    expect(getAllByText('Scan').length).toBeGreaterThan(0);
  });

  it('emits tabPress event on press', () => {
    const props = makeProps(0);
    const { getByTestId } = render(<RatisTabBar {...props} />);
    fireEvent.press(getByTestId('tab-liste'));
    expect(props.navigation.emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'tabPress', target: 'liste' }),
    );
  });

  it('does NOT navigate when defaultPrevented is true', () => {
    const props = makeProps(0);
    props.navigation.emit = jest.fn().mockReturnValue({ defaultPrevented: true });
    const { getByTestId } = render(<RatisTabBar {...props} />);
    fireEvent.press(getByTestId('tab-liste'));
    expect(props.navigation.navigate).not.toHaveBeenCalled();
  });
});
