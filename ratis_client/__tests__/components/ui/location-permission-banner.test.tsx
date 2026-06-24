import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { LocationPermissionBanner } from '@/components/ui/location-permission-banner';

describe('LocationPermissionBanner', () => {
  it('renders the scan context description', () => {
    const { getByText } = render(
      <LocationPermissionBanner context="scan" onRequestPermission={() => {}} />,
    );
    expect(getByText('Active ta localisation')).toBeTruthy();
    expect(
      getByText(
        "Nécessaire pour associer tes scans d'étiquettes au bon magasin.",
      ),
    ).toBeTruthy();
    expect(getByText('Activer')).toBeTruthy();
  });

  it('renders the liste context description', () => {
    const { getByText } = render(
      <LocationPermissionBanner context="liste" onRequestPermission={() => {}} />,
    );
    expect(
      getByText('Active la géoloc pour optimiser ton trajet shopping.'),
    ).toBeTruthy();
  });

  it('renders the produit context description', () => {
    const { getByText } = render(
      <LocationPermissionBanner context="produit" onRequestPermission={() => {}} />,
    );
    expect(
      getByText(
        'Active la géoloc pour voir les prix des magasins autour de toi.',
      ),
    ).toBeTruthy();
  });

  it('fires onRequestPermission when CTA is pressed', () => {
    const onRequest = jest.fn();
    const { getByTestId } = render(
      <LocationPermissionBanner context="scan" onRequestPermission={onRequest} />,
    );
    fireEvent.press(getByTestId('location-permission-banner-cta'));
    expect(onRequest).toHaveBeenCalledTimes(1);
  });

  it('fires onDismiss when dismiss is pressed', () => {
    const onDismiss = jest.fn();
    const { getByTestId } = render(
      <LocationPermissionBanner
        context="scan"
        onRequestPermission={() => {}}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.press(getByTestId('location-permission-banner-dismiss'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('omits the dismiss button when onDismiss is not provided', () => {
    const { queryByTestId } = render(
      <LocationPermissionBanner context="scan" onRequestPermission={() => {}} />,
    );
    expect(queryByTestId('location-permission-banner-dismiss')).toBeNull();
  });
});
