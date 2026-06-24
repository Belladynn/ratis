/**
 * Tests for the design system <Button /> primitive.
 *
 * Coverage : variants (primary / gold / danger / secondary), state machine
 * (disabled / loading), haptic feedback wiring, gradient palette per variant.
 *
 * Reanimated `useAnimatedStyle` is exercised on press in the component, but
 * Jest only renders the static tree — the press handler still fires onPress
 * synchronously, which is the contract we assert here.
 */

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

import { Button } from '@/components/design-system/button';
import { Colors } from '@/constants/theme';

const mockImpactAsync = jest.fn(async (..._args: unknown[]) => undefined);
jest.mock('expo-haptics', () => ({
  impactAsync: (style: string) => mockImpactAsync(style),
  ImpactFeedbackStyle: { Light: 'light', Medium: 'medium', Heavy: 'heavy' },
}));

// LinearGradient is a native module; in jest-expo it renders as a host
// component. We replace it by a plain View carrying its `colors` prop on
// `testID="ds-button-gradient"` so we can assert the active palette.
jest.mock('expo-linear-gradient', () => {
  const React = require('react');
  const { View } = require('react-native');
  return {
    LinearGradient: ({ colors, children, style, ...rest }: any) =>
      React.createElement(
        View,
        {
          ...rest,
          style,
          testID: rest.testID ?? 'ds-button-gradient',
          accessibilityLabel: JSON.stringify(colors),
        },
        children,
      ),
  };
});

describe('<Button />', () => {
  beforeEach(() => {
    mockImpactAsync.mockClear();
  });

  it('renders the label text', () => {
    const { getByText } = render(
      <Button label="Continuer" onPress={() => {}} />,
    );
    expect(getByText('Continuer')).toBeTruthy();
  });

  it('calls onPress when pressed', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <Button label="Tap" onPress={onPress} testID="cta" />,
    );
    fireEvent.press(getByTestId('cta'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not call onPress when disabled', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <Button label="Tap" onPress={onPress} disabled testID="cta" />,
    );
    fireEvent.press(getByTestId('cta'));
    expect(onPress).not.toHaveBeenCalled();
  });

  it('does not call onPress when loading', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <Button label="Tap" onPress={onPress} loading testID="cta" />,
    );
    fireEvent.press(getByTestId('cta'));
    expect(onPress).not.toHaveBeenCalled();
  });

  it('renders a spinner when loading', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} loading testID="cta" />,
    );
    expect(getByTestId('cta-spinner')).toBeTruthy();
  });

  it('triggers a haptic light impact on press by default', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} testID="cta" />,
    );
    fireEvent.press(getByTestId('cta'));
    expect(mockImpactAsync).toHaveBeenCalledTimes(1);
    expect(mockImpactAsync).toHaveBeenCalledWith('light');
  });

  it('skips haptic feedback when hapticOnPress is false', () => {
    const { getByTestId } = render(
      <Button
        label="Tap"
        onPress={() => {}}
        hapticOnPress={false}
        testID="cta"
      />,
    );
    fireEvent.press(getByTestId('cta'));
    expect(mockImpactAsync).not.toHaveBeenCalled();
  });

  it('applies the terracotta gradient for variant=primary (default)', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} testID="cta" />,
    );
    const gradient = getByTestId('cta-gradient');
    expect(gradient.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.terracottaHi, Colors.terracotta]),
    );
  });

  it('applies the gold gradient for variant=gold', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} variant="gold" testID="cta" />,
    );
    const gradient = getByTestId('cta-gradient');
    expect(gradient.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.goldHi, Colors.gold]),
    );
  });

  it('applies the coral palette for variant=danger', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} variant="danger" testID="cta" />,
    );
    const gradient = getByTestId('cta-gradient');
    expect(gradient.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.coralText, Colors.coral]),
    );
  });

  it('renders a transparent surface for variant=secondary', () => {
    const { getByTestId, queryByTestId } = render(
      <Button label="Tap" onPress={() => {}} variant="secondary" testID="cta" />,
    );
    // Secondary doesn't use a gradient — the surface is a plain View with a
    // 2px terracotta outline.
    expect(queryByTestId('cta-gradient')).toBeNull();
    expect(getByTestId('cta-surface')).toBeTruthy();
  });

  it('honours fullWidth by stretching the root', () => {
    const { getByTestId } = render(
      <Button label="Tap" onPress={() => {}} fullWidth testID="cta" />,
    );
    const styles = getByTestId('cta').props.style;
    const flat = Array.isArray(styles) ? Object.assign({}, ...styles.flat()) : styles;
    expect(flat.alignSelf).toBe('stretch');
  });
});
