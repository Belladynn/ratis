import React from 'react';
import { render } from '@testing-library/react-native';
import { MysteryProductCard } from '@/components/dashboard/mystery-product-card';

describe('MysteryProductCard', () => {
  it('renders question mark', () => {
    const { getByTestId } = render(<MysteryProductCard />);
    expect(getByTestId('mystery-question-mark')).toBeTruthy();
  });

  it('shows reward badge (CAB amount, design v4)', () => {
    // Design v4 (PR4.1) shifted the reward unit from XP "pts" to CAB.
    // Source: `.design-handoff-2026-05-03/project/lib/ratis-real-v4.jsx`
    // (MysteryProductCard) renders the badge as "+50 cab".
    const { getByText } = render(<MysteryProductCard />);
    expect(getByText(/cab/i)).toBeTruthy();
  });

  it('shows the "Produit du jour" title (design v4)', () => {
    const { getByText } = render(<MysteryProductCard />);
    expect(getByText(/Produit du jour/)).toBeTruthy();
  });
});
