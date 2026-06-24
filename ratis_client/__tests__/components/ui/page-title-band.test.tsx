// __tests__/components/ui/page-title-band.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Component re-created at `components/ui/page-title-band.tsx`. Test contents
// match the V5 contract (`title`, `leftIcon`, `rightIcons`, `titleSize`).

import React from 'react';
import { render } from '@testing-library/react-native';
import { Text, Pressable } from 'react-native';
import { PageTitleBand } from '@/components/ui/page-title-band';

describe('PageTitleBand', () => {
  it('renders the title', () => {
    const { getByText } = render(<PageTitleBand title="Ma liste" />);
    expect(getByText('Ma liste')).toBeTruthy();
  });

  it('renders left icon if provided', () => {
    const { getByTestId } = render(
      <PageTitleBand
        title="Détail"
        leftIcon={
          <Pressable testID="back-btn">
            <Text>←</Text>
          </Pressable>
        }
      />,
    );
    expect(getByTestId('back-btn')).toBeTruthy();
  });

  it('renders right icons in order', () => {
    const { getByText } = render(
      <PageTitleBand
        title="Ma liste"
        rightIcons={[<Text key="1">🗺️</Text>, <Text key="2">⋯</Text>]}
      />,
    );
    expect(getByText('🗺️')).toBeTruthy();
    expect(getByText('⋯')).toBeTruthy();
  });

  it('applies small title size when titleSize=small', () => {
    const { getByText } = render(
      <PageTitleBand title="Détail produit" titleSize="small" />,
    );
    const titleElement = getByText('Détail produit');
    const flatStyle = Array.isArray(titleElement.props.style)
      ? Object.assign({}, ...titleElement.props.style)
      : titleElement.props.style;
    expect(flatStyle).toEqual(
      expect.objectContaining({ fontSize: 14 }),
    );
  });
});
