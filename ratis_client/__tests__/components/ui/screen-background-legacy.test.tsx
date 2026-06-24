import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, testID }: any) => {
    const { View } = require('react-native');
    return <View testID={testID}>{children}</View>;
  },
}));

import { ScreenBackground } from '@/components/ui/screen-background-legacy';

describe('ScreenBackground (legacy — hors-V5 surfaces)', () => {
  it('renders without crash', () => {
    const { toJSON } = render(<ScreenBackground />);
    expect(toJSON()).not.toBeNull();
  });

  it('exposes testID for image, fog, and glows', () => {
    const { getByTestId } = render(<ScreenBackground />);
    expect(getByTestId('screen-bg-image')).toBeTruthy();
    expect(getByTestId('screen-bg-fog')).toBeTruthy();
    expect(getByTestId('screen-bg-glow-teal')).toBeTruthy();
    expect(getByTestId('screen-bg-glow-amber')).toBeTruthy();
  });
});
