import React from 'react';
import { render } from '@testing-library/react-native';
import { Text } from 'react-native';
import { ScreenCard } from '@/components/ui/screen-card';

describe('ScreenCard', () => {
  it('renders children', () => {
    const { getByText } = render(
      <ScreenCard><Text>content</Text></ScreenCard>,
    );
    expect(getByText('content')).toBeTruthy();
  });

  it('applies accent coral border when accent=coral', () => {
    const { getByTestId } = render(
      <ScreenCard testID="card" accent="coral"><Text>x</Text></ScreenCard>,
    );
    const flatStyle = Array.isArray(getByTestId('card').props.style)
      ? Object.assign({}, ...getByTestId('card').props.style)
      : getByTestId('card').props.style;
    expect(flatStyle.borderColor).toMatch(/251,113,133/);
  });

  it('removes padding when noPadding is set', () => {
    const { getByTestId } = render(
      <ScreenCard testID="card" noPadding><Text>x</Text></ScreenCard>,
    );
    const flatStyle = Array.isArray(getByTestId('card').props.style)
      ? Object.assign({}, ...getByTestId('card').props.style)
      : getByTestId('card').props.style;
    expect(flatStyle.padding).toBe(0);
  });
});
