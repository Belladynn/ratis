/**
 * Tests for the design system <Badge /> primitive.
 *
 * Coverage : the 4 rarity tiers map to the right gradient palette, label
 * renders, sizes affect padding/font, the legendary holo shine overlay is
 * mounted only when shine is on.
 */

import React from 'react';
import { Text } from 'react-native';
import { render } from '@testing-library/react-native';

import { Badge } from '@/components/design-system/badge';
import { Rarity } from '@/constants/theme';

jest.mock('expo-linear-gradient', () => {
  const React = require('react');
  const { View } = require('react-native');
  return {
    LinearGradient: ({ colors, style, children, ...rest }: any) =>
      React.createElement(
        View,
        {
          ...rest,
          style,
          testID: rest.testID ?? 'badge-gradient',
          accessibilityLabel: JSON.stringify(colors),
        },
        children,
      ),
  };
});

describe('<Badge />', () => {
  it('renders the label', () => {
    const { getByText } = render(<Badge rarity="common" label="Newbie" />);
    expect(getByText('Newbie')).toBeTruthy();
  });

  it('common rarity uses a neutral grey gradient', () => {
    const { getByTestId } = render(
      <Badge rarity="common" label="C" testID="b" />,
    );
    const grad = getByTestId('b-gradient');
    const colors = JSON.parse(grad.props.accessibilityLabel);
    // Neutral palette — start at #8B8B8B, end at #6B6B6B (per spec).
    expect(colors).toEqual(['#8B8B8B', '#6B6B6B']);
  });

  it('rare rarity uses cyan tone', () => {
    const { getByTestId } = render(<Badge rarity="rare" label="R" testID="b" />);
    const grad = getByTestId('b-gradient');
    const colors = JSON.parse(grad.props.accessibilityLabel);
    expect(colors[0]).toBe(Rarity.rare);
  });

  it('epic rarity uses violet tone', () => {
    const { getByTestId } = render(<Badge rarity="epic" label="E" testID="b" />);
    const grad = getByTestId('b-gradient');
    const colors = JSON.parse(grad.props.accessibilityLabel);
    expect(colors[0]).toBe(Rarity.epic);
  });

  it('legendary rarity uses gold tone', () => {
    const { getByTestId } = render(
      <Badge rarity="legendary" label="L" testID="b" />,
    );
    const grad = getByTestId('b-gradient');
    const colors = JSON.parse(grad.props.accessibilityLabel);
    expect(colors[0]).toBe(Rarity.legendary);
  });

  it('shows holo shine overlay for legendary by default', () => {
    const { getByTestId } = render(
      <Badge rarity="legendary" label="L" testID="b" />,
    );
    expect(getByTestId('b-shine')).toBeTruthy();
  });

  it('hides shine overlay when shine={false}', () => {
    const { queryByTestId } = render(
      <Badge rarity="legendary" label="L" shine={false} testID="b" />,
    );
    expect(queryByTestId('b-shine')).toBeNull();
  });

  it('does not render shine overlay for common rarity', () => {
    const { queryByTestId } = render(
      <Badge rarity="common" label="C" shine testID="b" />,
    );
    // Common stays static even when shine is requested — the rarity gates it.
    expect(queryByTestId('b-shine')).toBeNull();
  });

  it('renders icon when provided', () => {
    const { getByTestId } = render(
      <Badge
        rarity="rare"
        label="With icon"
        icon={<Text testID="badge-icon">⭐</Text>}
      />,
    );
    expect(getByTestId('badge-icon')).toBeTruthy();
  });

  it('honours size=lg by applying a larger font', () => {
    const { getByText } = render(
      <Badge rarity="rare" label="Large" size="lg" />,
    );
    const styles = getByText('Large').props.style;
    const flat = Array.isArray(styles)
      ? Object.assign({}, ...styles.flat())
      : styles;
    expect(flat.fontSize).toBeGreaterThanOrEqual(13);
  });
});
