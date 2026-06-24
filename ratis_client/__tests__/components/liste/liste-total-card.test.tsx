// __tests__/components/liste/liste-total-card.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).

import React from 'react';
import { render } from '@testing-library/react-native';
import { ListeTotalCard } from '@/components/liste/liste-total-card';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

describe('ListeTotalCard', () => {
  it('renders both labels (TOTAL ESTIMÉ + ÉCONOMIES)', () => {
    const { getByText } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
      />,
    );
    expect(getByText('TOTAL ESTIMÉ')).toBeTruthy();
    expect(getByText('ÉCONOMIES')).toBeTruthy();
  });

  it('formats the total amount with comma separator and € suffix', () => {
    const { getByTestId } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
      />,
    );
    expect(getByTestId('liste-total-card-total').props.children).toBe('16,17€');
  });

  it('formats savings with leading minus sign', () => {
    const { getByTestId } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
      />,
    );
    expect(getByTestId('liste-total-card-savings').props.children).toEqual([
      '-',
      '4,35€',
    ]);
  });

  it('hides the checked subtitle when checkedCount is 0', () => {
    const { queryByTestId } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
      />,
    );
    expect(queryByTestId('liste-total-card-checked')).toBeNull();
  });

  it('renders the singular "1 coché · X,YY€" subtitle when checkedCount is 1', () => {
    const { getByText } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={1}
        checkedTotal={1.85}
      />,
    );
    expect(getByText('1 coché · 1,85€')).toBeTruthy();
  });

  it('renders the plural "N cochés · X,YY€" subtitle when checkedCount > 1', () => {
    const { getByText } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={3}
        checkedTotal={5.2}
      />,
    );
    expect(getByText('3 cochés · 5,20€')).toBeTruthy();
  });

  it('shows "après optimisation" when route is not yet ready', () => {
    const { getByText } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
      />,
    );
    expect(getByText('après optimisation')).toBeTruthy();
  });

  it('hides "après optimisation" once the route is ready', () => {
    const { queryByText } = render(
      <ListeTotalCard
        total={16.17}
        savings={4.35}
        checkedCount={0}
        checkedTotal={0}
        routeReady
      />,
    );
    expect(queryByText('après optimisation')).toBeNull();
  });
});
