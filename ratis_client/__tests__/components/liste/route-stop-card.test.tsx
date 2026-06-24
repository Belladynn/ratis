// __tests__/components/liste/route-stop-card.test.tsx
//
// Created at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// V5 supersedes the V4 `RouteStoreCard` (collapsible header + initial avatar)
// with `RouteStopCard` per `Ratis_handoff/lib/ratis-liste.jsx` lines 328-394
// (numbered stop marker + dashed connector + savings pill + flat item list).
// The legacy `route-store-card.test.tsx` placeholder remains skipped — see
// the comment in that file for context.

import React from 'react';
import { Alert, Linking } from 'react-native';
import { fireEvent, render } from '@testing-library/react-native';
import { RouteStopCard } from '@/components/liste/route-stop-card';
import type { RouteStore } from '@/hooks/use-active-route';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

const baseStore: RouteStore = {
  store_id: 'store-1',
  store_name: 'Lidl Charonne',
  retailer: 'Lidl',
  address: '12 rue de Charonne, Paris',
  lat: 48.85,
  lng: 2.38,
  order: 1,
  subtotal: 6.13,
  items: [
    {
      item_id: 'i1',
      product_ean: '3428270000019',
      product_name: 'Lait demi-écrémé 1L',
      quantity: 1,
      price: 1.05,
      price_source: 'consensus',
      trust_score: 0.95,
    },
    {
      item_id: 'i2',
      product_ean: '3330710000028',
      product_name: 'Bananes (kg)',
      quantity: 1,
      price: 1.49,
      price_source: 'consensus',
      trust_score: 0.9,
    },
  ],
};

describe('RouteStopCard', () => {
  it('renders the store name', () => {
    const { getByText } = render(<RouteStopCard store={baseStore} />);
    expect(getByText('Lidl Charonne')).toBeTruthy();
  });

  it('renders the numbered marker (defaults to store.order)', () => {
    const { getByText } = render(<RouteStopCard store={baseStore} />);
    expect(getByText('1')).toBeTruthy();
  });

  it('honours the explicit `index` override', () => {
    const { getByText, queryByText } = render(
      <RouteStopCard store={baseStore} index={3} />,
    );
    expect(getByText('3')).toBeTruthy();
    expect(queryByText('1')).toBeNull();
  });

  it('renders the dashed connector when not the last stop', () => {
    const { getByTestId } = render(<RouteStopCard store={baseStore} />);
    expect(getByTestId('route-stop-card-connector')).toBeTruthy();
  });

  it('omits the connector when last=true', () => {
    const { queryByTestId } = render(
      <RouteStopCard store={baseStore} last />,
    );
    expect(queryByTestId('route-stop-card-connector')).toBeNull();
  });

  it('renders the subtitle with N articles (plural form)', () => {
    const { getByText } = render(<RouteStopCard store={baseStore} />);
    expect(getByText('2 articles')).toBeTruthy();
  });

  it('renders the singular "1 article" when only one item is in the stop', () => {
    const single: RouteStore = {
      ...baseStore,
      items: [baseStore.items[0]],
    };
    const { getByText } = render(<RouteStopCard store={single} />);
    expect(getByText('1 article')).toBeTruthy();
  });

  it('renders distance + time + items count when distance/duration are provided', () => {
    const { getByText } = render(
      <RouteStopCard
        store={baseStore}
        distanceKm={0.4}
        durationMin={6}
      />,
    );
    expect(getByText('0.4 km · 6 min · 2 articles')).toBeTruthy();
  });

  it('renders the savings pill when savings > 0', () => {
    const { getByTestId } = render(
      <RouteStopCard store={baseStore} savings={2.4} />,
    );
    const pill = getByTestId('route-stop-card-savings');
    expect(pill).toBeTruthy();
  });

  it('omits the savings pill when savings is null/undefined', () => {
    const { queryByTestId } = render(<RouteStopCard store={baseStore} />);
    expect(queryByTestId('route-stop-card-savings')).toBeNull();
  });

  it('renders each item name in the flat list', () => {
    const { getByText } = render(<RouteStopCard store={baseStore} />);
    expect(getByText('Lait demi-écrémé 1L')).toBeTruthy();
    expect(getByText('Bananes (kg)')).toBeTruthy();
  });

  describe('« Ouvrir dans Maps » deeplink (RGPD-aware)', () => {
    let alertSpy: jest.SpyInstance;
    let openURLSpy: jest.SpyInstance;

    beforeEach(() => {
      alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
      openURLSpy = jest
        .spyOn(Linking, 'openURL')
        .mockImplementation(() => Promise.resolve(true));
    });

    afterEach(() => {
      alertSpy.mockRestore();
      openURLSpy.mockRestore();
    });

    it('renders the Maps CTA button', () => {
      const { getByTestId } = render(<RouteStopCard store={baseStore} />);
      expect(getByTestId('route-stop-card-open-in-maps')).toBeTruthy();
    });

    it('shows a confirm Alert when the CTA is tapped (RGPD warning)', () => {
      const { getByTestId } = render(<RouteStopCard store={baseStore} />);
      fireEvent.press(getByTestId('route-stop-card-open-in-maps'));
      expect(alertSpy).toHaveBeenCalledTimes(1);
      expect(openURLSpy).not.toHaveBeenCalled();
      // Verify the alert mentions data leaving Ratis (RGPD signal).
      const [title, body] = alertSpy.mock.calls[0];
      expect(title).toMatch(/quitter/i);
      expect(body).toContain('Lidl Charonne');
    });

    it('does NOT open the URL when the user cancels the Alert', () => {
      const { getByTestId } = render(<RouteStopCard store={baseStore} />);
      fireEvent.press(getByTestId('route-stop-card-open-in-maps'));
      const buttons = alertSpy.mock.calls[0][2] as Array<{
        text: string;
        onPress?: () => void;
        style?: string;
      }>;
      const cancel = buttons.find((b) => b.style === 'cancel');
      cancel?.onPress?.();
      expect(openURLSpy).not.toHaveBeenCalled();
    });

    it('opens the Google Maps deeplink URL when the user confirms', () => {
      const { getByTestId } = render(<RouteStopCard store={baseStore} />);
      fireEvent.press(getByTestId('route-stop-card-open-in-maps'));
      const buttons = alertSpy.mock.calls[0][2] as Array<{
        text: string;
        onPress?: () => void;
        style?: string;
      }>;
      const confirm = buttons.find((b) => b.style !== 'cancel');
      confirm?.onPress?.();
      expect(openURLSpy).toHaveBeenCalledTimes(1);
      const url = openURLSpy.mock.calls[0][0] as string;
      expect(url).toContain('google.com/maps/dir/');
      expect(url).toContain(`destination=${baseStore.lat},${baseStore.lng}`);
      expect(url).toContain('travelmode=driving');
    });
  });
});
