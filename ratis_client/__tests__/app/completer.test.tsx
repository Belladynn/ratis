import { render, fireEvent, waitFor } from '@testing-library/react-native';
import React from 'react';

import CompleterScreen from '@/app/completer';

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: jest.fn() }),
}));

const mockUseIncomplete = jest.fn();
jest.mock('@/hooks/use-incomplete-products', () => ({
  useIncompleteProducts: () => mockUseIncomplete(),
}));

const mockContributeMutate = jest.fn();
jest.mock('@/hooks/use-contribute-field', () => ({
  useContributeField: () => ({
    mutateAsync: mockContributeMutate,
    isPending: false,
  }),
}));

const makeTask = (ean: string, field = 'brands') => ({
  product_ean: ean,
  product_name: `Produit-${ean}`,
  missing_field: field,
  cab_reward: 5,
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('CompleterScreen', () => {
  it('renders EmptyState when batch is empty', () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    const { getByTestId } = render(<CompleterScreen />);
    expect(getByTestId('empty-state-back')).toBeTruthy();
  });

  it('renders ErrorState when fetch fails', () => {
    mockUseIncomplete.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('Network'),
      refetch: jest.fn(),
    });
    const { getByTestId } = render(<CompleterScreen />);
    expect(getByTestId('error-state-retry')).toBeTruthy();
  });

  it('renders FormState for task[0] on initial load', () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [makeTask('111')] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    const { getByText } = render(<CompleterScreen />);
    expect(getByText('Produit-111')).toBeTruthy();
  });

  it('advances to next task on Suivant after submit', async () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [makeTask('111'), makeTask('222')] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    mockContributeMutate.mockResolvedValue({ status: 'applied' });
    const { getByTestId, getByText, queryByText } = render(<CompleterScreen />);
    // Form 1 visible
    expect(getByText('Produit-111')).toBeTruthy();
    // Submit value
    fireEvent.changeText(getByTestId('field-input-text-input'), 'Lactel');
    fireEvent.press(getByTestId('field-input-text-submit'));
    await waitFor(() => expect(mockContributeMutate).toHaveBeenCalled());
    // SuccessState visible
    await waitFor(() => expect(getByTestId('success-state-next')).toBeTruthy());
    // Tap Suivant
    fireEvent.press(getByTestId('success-state-next'));
    // Form 2 visible
    await waitFor(() => expect(getByText('Produit-222')).toBeTruthy());
    expect(queryByText('Produit-111')).toBeNull();
  });

  it('skip advances directly without backend call', () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [makeTask('111'), makeTask('222')] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    const { getByTestId, getByText } = render(<CompleterScreen />);
    fireEvent.press(getByTestId('field-input-text-skip'));
    expect(mockContributeMutate).not.toHaveBeenCalled();
    expect(getByText('Produit-222')).toBeTruthy();
  });

  it('shows ExhaustedState after last task', async () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [makeTask('111')] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    mockContributeMutate.mockResolvedValue({ status: 'applied' });
    const { getByTestId } = render(<CompleterScreen />);
    fireEvent.changeText(getByTestId('field-input-text-input'), 'Lactel');
    fireEvent.press(getByTestId('field-input-text-submit'));
    await waitFor(() => expect(getByTestId('success-state-next')).toBeTruthy());
    fireEvent.press(getByTestId('success-state-next'));
    await waitFor(() => expect(getByTestId('exhausted-state-back')).toBeTruthy());
  });

  it('Retour from SuccessState calls router.back', async () => {
    mockUseIncomplete.mockReturnValue({
      data: { items: [makeTask('111')] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    mockContributeMutate.mockResolvedValue({ status: 'applied' });
    const { getByTestId } = render(<CompleterScreen />);
    fireEvent.changeText(getByTestId('field-input-text-input'), 'Lactel');
    fireEvent.press(getByTestId('field-input-text-submit'));
    await waitFor(() => expect(getByTestId('success-state-done')).toBeTruthy());
    fireEvent.press(getByTestId('success-state-done'));
    expect(mockBack).toHaveBeenCalled();
  });
});
