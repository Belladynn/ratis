import React from 'react';
import { render } from '@testing-library/react-native';
import { ScanViewfinder } from '@/components/scan/scan-viewfinder';

describe('ScanViewfinder', () => {
  it('renders 4 corners', () => {
    const { getAllByTestId } = render(<ScanViewfinder />);
    expect(getAllByTestId('vf-corner')).toHaveLength(4);
  });
});
