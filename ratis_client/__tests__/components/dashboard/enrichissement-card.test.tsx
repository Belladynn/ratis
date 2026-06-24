import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { useRouter } from 'expo-router';

import { EnrichissementCard } from '@/components/dashboard/enrichissement-card';
import { EnrichissementTask } from '@/types/gamification';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, style }: any) => {
    const { View } = require('react-native');
    return <View style={style}>{children}</View>;
  },
}));

jest.mock('expo-router', () => ({
  useRouter: jest.fn(),
}));

const TASK: EnrichissementTask = {
  product_ean: '3017620422003',
  product_name: 'Nutella 400g',
  missing_field: 'nutriscore',
  cab_reward: 120,
};

beforeEach(() => {
  (useRouter as jest.Mock).mockReturnValue({ push: jest.fn(), back: jest.fn() });
});

describe('EnrichissementCard', () => {
  it('renders product name', () => {
    const { getByText } = render(
      <EnrichissementCard task={TASK} onPress={jest.fn()} isLoading={false} />,
    );
    expect(getByText(/Nutella 400g/)).toBeTruthy();
  });

  it('renders CAB reward as "+120 ⚡" not "+1,20 €"', () => {
    const { getByText, queryByText } = render(
      <EnrichissementCard task={TASK} onPress={jest.fn()} isLoading={false} />,
    );
    expect(getByText('+120 ⚡')).toBeTruthy();
    expect(queryByText(/€/)).toBeNull();
    expect(queryByText(/\+1,20/)).toBeNull();
  });

  it('calls onPress with EAN when tapped', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <EnrichissementCard task={TASK} onPress={onPress} isLoading={false} />,
    );
    fireEvent.press(getByTestId('enrichissement-card-cta'));
    expect(onPress).toHaveBeenCalledWith('3017620422003');
  });

  it('navigates to /completer on press', () => {
    const push = jest.fn();
    (useRouter as jest.Mock).mockReturnValue({ push, back: jest.fn() });
    const { getByTestId } = render(<EnrichissementCard task={TASK} />);
    fireEvent.press(getByTestId('enrichissement-card-cta'));
    expect(push).toHaveBeenCalledWith('/completer');
  });

  it('returns null when task is null (no enrichissement available)', () => {
    const { toJSON } = render(
      <EnrichissementCard task={null} onPress={jest.fn()} isLoading={false} />,
    );
    expect(toJSON()).toBeNull();
  });
});
