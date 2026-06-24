// V5 industrial dark teal background (used by all (tabs) surfaces).
// Hors-V5 surfaces use `screen-background-legacy` instead.
import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, testID }: any) => {
    const { View } = require('react-native');
    return <View testID={testID}>{children}</View>;
  },
}));

import { ScreenBackground } from '@/components/ui/screen-background';

describe('ScreenBackground (V5 — tabs surfaces)', () => {
  it('renders without crash', () => {
    const { toJSON } = render(<ScreenBackground />);
    expect(toJSON()).not.toBeNull();
  });

  it('exposes testID for base, fog, and glows', () => {
    const { getByTestId } = render(<ScreenBackground />);
    expect(getByTestId('screen-bg-image')).toBeTruthy();
    expect(getByTestId('screen-bg-fog')).toBeTruthy();
    expect(getByTestId('screen-bg-glow-teal')).toBeTruthy();
    expect(getByTestId('screen-bg-glow-amber')).toBeTruthy();
  });

  it('forwards a custom testID on the root', () => {
    const { getByTestId } = render(<ScreenBackground testID="bg-root" />);
    expect(getByTestId('bg-root')).toBeTruthy();
  });
});
