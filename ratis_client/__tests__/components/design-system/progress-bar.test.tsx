/**
 * Tests for the design system <ProgressBar /> primitive.
 *
 * Coverage : value clamping (0..1 contract from ARCH § Progress Bars), gold /
 * jarPink / terracotta / cyan variants exposing the right gradient palette,
 * height override, optional label render.
 */

import React from 'react';
import { render } from '@testing-library/react-native';

import { ProgressBar } from '@/components/design-system/progress-bar';
import { Colors } from '@/constants/theme';

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
          testID: rest.testID ?? 'pb-gradient',
          accessibilityLabel: JSON.stringify(colors),
        },
        children,
      ),
  };
});

describe('<ProgressBar />', () => {
  it('renders the track and the fill', () => {
    const { getByTestId } = render(
      <ProgressBar value={0.5} variant="gold" testID="bar" />,
    );
    expect(getByTestId('bar')).toBeTruthy();
    expect(getByTestId('bar-fill')).toBeTruthy();
  });

  it('applies the gold gradient for variant=gold', () => {
    const { getByTestId } = render(
      <ProgressBar value={0.5} variant="gold" testID="bar" />,
    );
    const grad = getByTestId('bar-gradient');
    expect(grad.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.goldHi, Colors.gold]),
    );
  });

  it('applies the jarPink gradient for variant=jarPink', () => {
    const { getByTestId } = render(
      <ProgressBar value={0.5} variant="jarPink" testID="bar" />,
    );
    const grad = getByTestId('bar-gradient');
    expect(grad.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.jarPinkHi, Colors.jarPink]),
    );
  });

  it('applies the terracotta gradient for variant=terracotta', () => {
    const { getByTestId } = render(
      <ProgressBar value={0.5} variant="terracotta" testID="bar" />,
    );
    const grad = getByTestId('bar-gradient');
    expect(grad.props.accessibilityLabel).toBe(
      JSON.stringify([Colors.terracottaHi, Colors.terracotta]),
    );
  });

  it('clamps value below 0 to 0', () => {
    const { getByTestId } = render(
      <ProgressBar value={-0.5} variant="gold" testID="bar" />,
    );
    const fill = getByTestId('bar-fill');
    const flat = Array.isArray(fill.props.style)
      ? Object.assign({}, ...fill.props.style.flat())
      : fill.props.style;
    expect(flat.width).toBe('0%');
  });

  it('clamps value above 1 to 100', () => {
    const { getByTestId } = render(
      <ProgressBar value={1.4} variant="gold" testID="bar" />,
    );
    const fill = getByTestId('bar-fill');
    const flat = Array.isArray(fill.props.style)
      ? Object.assign({}, ...fill.props.style.flat())
      : fill.props.style;
    expect(flat.width).toBe('100%');
  });

  it('honours height override', () => {
    const { getByTestId } = render(
      <ProgressBar value={0.5} variant="gold" height={20} testID="bar" />,
    );
    const flat = Array.isArray(getByTestId('bar').props.style)
      ? Object.assign({}, ...getByTestId('bar').props.style.flat())
      : getByTestId('bar').props.style;
    expect(flat.height).toBe(20);
  });

  it('renders label text when showLabel=true', () => {
    const { getByText } = render(
      <ProgressBar value={0.75} variant="gold" showLabel testID="bar" />,
    );
    expect(getByText('75%')).toBeTruthy();
  });

  it('does not render label when showLabel=false (default)', () => {
    const { queryByText } = render(
      <ProgressBar value={0.5} variant="gold" testID="bar" />,
    );
    expect(queryByText('50%')).toBeNull();
  });
});
