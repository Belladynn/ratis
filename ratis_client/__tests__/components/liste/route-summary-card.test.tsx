// __tests__/components/liste/route-summary-card.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).

import React from 'react';
import { render } from '@testing-library/react-native';
import { RouteSummaryCard } from '@/components/liste/route-summary-card';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

describe('RouteSummaryCard', () => {
  it('renders Total / Économisé labels', () => {
    const { getByText } = render(
      <RouteSummaryCard total={16.17} savings={4.35} />,
    );
    expect(getByText('Total')).toBeTruthy();
    expect(getByText('Économisé')).toBeTruthy();
  });

  it('formats the total with comma separator and € suffix', () => {
    const { getByTestId } = render(
      <RouteSummaryCard total={16.17} savings={4.35} />,
    );
    expect(getByTestId('route-summary-card-total').props.children).toBe(
      '16,17€',
    );
  });

  it('renders savings with leading minus', () => {
    const { getByTestId } = render(
      <RouteSummaryCard total={16.17} savings={4.35} />,
    );
    expect(getByTestId('route-summary-card-savings').props.children).toEqual([
      '-',
      '4,35€',
    ]);
  });

  it('omits the trip column when no distance/duration is provided', () => {
    const { queryByTestId } = render(
      <RouteSummaryCard total={16.17} savings={4.35} />,
    );
    expect(queryByTestId('route-summary-card-trip')).toBeNull();
  });

  it('renders the trip column with km · min format', () => {
    const { getByTestId } = render(
      <RouteSummaryCard
        total={16.17}
        savings={4.35}
        distanceKm={4.4}
        durationMin={42}
      />,
    );
    expect(getByTestId('route-summary-card-trip').props.children).toBe(
      '4.4 km · 42min',
    );
  });
});
