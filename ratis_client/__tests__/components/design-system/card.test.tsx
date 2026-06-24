/**
 * Tests for the design system <Card /> primitive.
 *
 * Coverage : standard render, accent variant border-color matches accent,
 * onPress wiring, custom padding override.
 */

import React from 'react';
import { Text } from 'react-native';
import { render, fireEvent } from '@testing-library/react-native';

import { Card } from '@/components/design-system/card';
import { Colors } from '@/constants/theme';

function flattenStyle(style: unknown): Record<string, unknown> {
  if (Array.isArray(style)) {
    return Object.assign({}, ...style.flat().map(flattenStyle));
  }
  if (style && typeof style === 'object') {
    return style as Record<string, unknown>;
  }
  return {};
}

describe('<Card />', () => {
  it('renders children', () => {
    const { getByText } = render(
      <Card>
        <Text>Hello card</Text>
      </Card>,
    );
    expect(getByText('Hello card')).toBeTruthy();
  });

  it('uses the surface background by default (standard variant)', () => {
    const { getByTestId } = render(
      <Card testID="card">
        <Text>Body</Text>
      </Card>,
    );
    const styles = flattenStyle(getByTestId('card').props.style);
    expect(styles.backgroundColor).toBe(Colors.surface);
  });

  it('applies the accent border for variant=accent + accentColor=jarPink', () => {
    const { getByTestId } = render(
      <Card variant="accent" accentColor="jarPink" testID="card">
        <Text>Body</Text>
      </Card>,
    );
    const styles = flattenStyle(getByTestId('card').props.style);
    // Accent variant uses a tinted border in the accent color (alpha
    // 0.35 per ARCH § Cards / Accent).
    expect(styles.borderLeftWidth).toBe(4);
    expect(styles.borderLeftColor).toBe(Colors.jarPink);
  });

  it('honours a custom padding override', () => {
    const { getByTestId } = render(
      <Card padding={28} testID="card">
        <Text>Body</Text>
      </Card>,
    );
    const styles = flattenStyle(getByTestId('card').props.style);
    expect(styles.padding).toBe(28);
  });

  it('fires onPress when tappable', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <Card onPress={onPress} testID="card">
        <Text>Tappable</Text>
      </Card>,
    );
    fireEvent.press(getByTestId('card'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not require onPress (renders as plain View)', () => {
    // Smoke test : un Card non-tappable doit rendre sans crash et exposer
    // son testID.
    const { getByTestId } = render(
      <Card testID="card">
        <Text>Static</Text>
      </Card>,
    );
    expect(getByTestId('card')).toBeTruthy();
  });

  it('rejects unknown accent colors gracefully (falls back to terracotta)', () => {
    const { getByTestId } = render(
      // @ts-expect-error testing fallback
      <Card variant="accent" accentColor="unknown_color" testID="card">
        <Text>Body</Text>
      </Card>,
    );
    const styles = flattenStyle(getByTestId('card').props.style);
    expect(styles.borderLeftColor).toBe(Colors.terracotta);
  });
});
