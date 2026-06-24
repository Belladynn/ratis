// __tests__/components/liste/sheets.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { SuggestionsSheet } from '@/components/liste/suggestions-sheet';
import { TemplatesSheet } from '@/components/liste/templates-sheet';
import { VoiceSheet } from '@/components/liste/voice-sheet';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

describe('SuggestionsSheet', () => {
  it('renders title and eyebrow when open', () => {
    const { getByText } = render(
      <SuggestionsSheet open onClose={jest.fn()} />,
    );
    expect(getByText('Tu rachètes souvent…')).toBeTruthy();
    expect(getByText('Suggestions')).toBeTruthy();
  });

  it('renders empty placeholder when no suggestions', () => {
    const { getByText } = render(
      <SuggestionsSheet open onClose={jest.fn()} />,
    );
    expect(getByText('Bientôt disponible')).toBeTruthy();
  });

  it('renders each suggestion and triggers onAdd on press', () => {
    const onAdd = jest.fn();
    const { getByTestId } = render(
      <SuggestionsSheet
        open
        onClose={jest.fn()}
        suggestions={[
          { id: 's1', name: 'Lait demi-écrémé 1L', brand: 'Lactel', est: 1.05 },
        ]}
        onAdd={onAdd}
      />,
    );
    fireEvent.press(getByTestId('liste-suggestion-s1'));
    expect(onAdd).toHaveBeenCalledWith({
      id: 's1',
      name: 'Lait demi-écrémé 1L',
      brand: 'Lactel',
      est: 1.05,
    });
  });
});

describe('TemplatesSheet', () => {
  it('renders title when open', () => {
    const { getByText } = render(
      <TemplatesSheet open onClose={jest.fn()} />,
    );
    expect(getByText('Listes type')).toBeTruthy();
  });

  it('renders each template and triggers onApply on press', () => {
    const onApply = jest.fn();
    const tmpl = {
      id: 't1',
      label: 'Courses de la semaine',
      icon: '🛒',
      color: '#A78BFA',
      itemCount: 12,
      estimatedTotal: 24.5,
    };
    const { getByTestId } = render(
      <TemplatesSheet
        open
        onClose={jest.fn()}
        templates={[tmpl]}
        onApply={onApply}
      />,
    );
    fireEvent.press(getByTestId('liste-template-t1'));
    expect(onApply).toHaveBeenCalledWith(tmpl);
  });
});

describe('VoiceSheet', () => {
  it('renders the V1 stub label when open', () => {
    const { getByText } = render(<VoiceSheet open onClose={jest.fn()} />);
    expect(getByText('Bientôt disponible')).toBeTruthy();
  });
});
