// ratis_client/__tests__/components/dashboard/jar-prestige.test.tsx
//
// Smoke tests for JarPrestige (Skia-rendered tirelire — PR4.1 pivot).
// Skia is mocked via `__mocks__/shopify-react-native-skia.tsx` so the tree
// mounts under jest's "node" env. We assert behaviour, not pixel output :
//   - mounts with various fill / prestige combinations
//   - cycles tier color via prestigeLevel % 5 (lid color flows from theme)
//   - renders the percent overlay & EUR label
//   - tapability via onPress
//   - prefers `totalAbonnements` over percent when provided

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { JarPrestige } from '@/components/dashboard/jar-prestige';
import { JarTiers, getJarTier } from '@/constants/theme';

describe('JarPrestige', () => {
  it('mounts with minimum props', () => {
    const { getByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={0}
        prestigeLevel={0}
        totalSaved={0}
      />,
    );
    expect(getByTestId('jar')).toBeTruthy();
  });

  it('renders the rounded percent overlay', () => {
    const { getByTestId, getByText } = render(
      <JarPrestige
        testID="jar"
        currentFill={42.7}
        prestigeLevel={0}
        totalSaved={1234}
      />,
    );
    expect(getByTestId('jar-percent')).toBeTruthy();
    expect(getByText('43%')).toBeTruthy();
  });

  it('renders abonnements label instead of percent when provided', () => {
    const { getByTestId, queryByTestId, getByText } = render(
      <JarPrestige
        testID="jar"
        currentFill={50}
        prestigeLevel={0}
        totalSaved={1234}
        totalAbonnements={12.4}
      />,
    );
    expect(getByTestId('jar-abonnements')).toBeTruthy();
    expect(queryByTestId('jar-percent')).toBeNull();
    expect(getByText('12,4')).toBeTruthy();
  });

  it('renders savings amount in EUR with comma decimal', () => {
    const { getByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={100}
        prestigeLevel={0}
        totalSaved={4567} // 45,67€
      />,
    );
    const eur = getByTestId('jar-eur');
    expect(eur).toBeTruthy();
    expect(eur.props.children).toBe('45,67€');
  });

  it('clamps currentFill outside 0..100', () => {
    const { getByText } = render(
      <JarPrestige
        testID="jar"
        currentFill={150}
        prestigeLevel={0}
        totalSaved={0}
      />,
    );
    expect(getByText('100%')).toBeTruthy();
  });

  it('treats negative currentFill as 0', () => {
    const { getByText } = render(
      <JarPrestige
        testID="jar"
        currentFill={-10}
        prestigeLevel={0}
        totalSaved={0}
      />,
    );
    expect(getByText('0%')).toBeTruthy();
  });

  it('cycles through 5 tier palettes via prestigeLevel modulo', () => {
    // Sanity check on the helper — exhaustive on getJarTier rather than the
    // rendered DOM (Skia mock doesn't expose colors easily).
    expect(getJarTier(0)).toBe(JarTiers[0]);
    expect(getJarTier(4)).toBe(JarTiers[4]);
    expect(getJarTier(5)).toBe(JarTiers[0]); // cycle
    expect(getJarTier(7)).toBe(JarTiers[2]);
    expect(getJarTier(-1)).toBe(JarTiers[4]); // negative wraparound
  });

  it('mounts with all 5 tier values without crash', () => {
    for (let tier = 0; tier < 5; tier++) {
      const { unmount } = render(
        <JarPrestige
          testID={`jar-tier-${tier}`}
          currentFill={50}
          prestigeLevel={tier}
          totalSaved={1000 * (tier + 1)}
        />,
      );
      unmount();
    }
  });

  it('calls onPress when tapped', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={50}
        prestigeLevel={0}
        totalSaved={0}
        onPress={onPress}
      />,
    );
    fireEvent.press(getByTestId('jar-press'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('renders next-tier footer when nextTierRemainingCents > 0', () => {
    const { getByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={62}
        prestigeLevel={0}
        totalSaved={4795}
        nextTierRemainingCents={5200}
      />,
    );
    const footer = getByTestId('jar-footer');
    expect(footer.props.children).toBe('Plus que 52€ → palier suivant');
  });

  it('omits footer when nextTierRemainingCents is 0 or undefined', () => {
    const { queryByTestId, rerender } = render(
      <JarPrestige
        testID="jar"
        currentFill={62}
        prestigeLevel={0}
        totalSaved={4795}
      />,
    );
    expect(queryByTestId('jar-footer')).toBeNull();

    rerender(
      <JarPrestige
        testID="jar"
        currentFill={62}
        prestigeLevel={0}
        totalSaved={4795}
        nextTierRemainingCents={0}
      />,
    );
    expect(queryByTestId('jar-footer')).toBeNull();
  });

  it('uses i18n template when provided', () => {
    const { getByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={50}
        prestigeLevel={0}
        totalSaved={5000}
        nextTierRemainingCents={500}
        nextTierFooterTemplate="Encore {{amount}} pour le palier"
      />,
    );
    expect(getByTestId('jar-footer').props.children).toBe('Encore 5€ pour le palier');
  });

  it('renders as non-pressable when onPress is omitted', () => {
    const { queryByTestId } = render(
      <JarPrestige
        testID="jar"
        currentFill={50}
        prestigeLevel={0}
        totalSaved={0}
      />,
    );
    expect(queryByTestId('jar-press')).toBeNull();
  });
});
