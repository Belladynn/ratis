// ratis_client/__tests__/components/design-system/segmented-tabs.test.tsx
import React, { useState } from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { SegmentedTabs, type SegmentedTab } from '@/components/design-system/segmented-tabs';

jest.mock('expo-haptics', () => ({
  ImpactFeedbackStyle: { Light: 'light', Medium: 'medium', Heavy: 'heavy' },
  impactAsync: jest.fn(() => Promise.resolve()),
}));

const TABS: SegmentedTab[] = [
  { id: 'daily', label: 'Quotidien' },
  { id: 'weekly', label: 'Hebdo' },
];

describe('SegmentedTabs', () => {
  it('mounts with required props', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    expect(getByTestId('seg')).toBeTruthy();
  });

  it('renders one Pressable per tab with the right testID + label', () => {
    const onChange = jest.fn();
    const { getByTestId, getByText } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    expect(getByTestId('seg-tab-daily')).toBeTruthy();
    expect(getByTestId('seg-tab-weekly')).toBeTruthy();
    expect(getByText('Quotidien')).toBeTruthy();
    expect(getByText('Hebdo')).toBeTruthy();
  });

  it('exposes accessibilityRole="tablist" + tab="tab" + selected state', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="weekly" onChange={onChange} />,
    );
    const list = getByTestId('seg');
    expect(list.props.accessibilityRole).toBe('tablist');
    const dailyTab = getByTestId('seg-tab-daily');
    const weeklyTab = getByTestId('seg-tab-weekly');
    expect(dailyTab.props.accessibilityRole).toBe('tab');
    expect(dailyTab.props.accessibilityState).toEqual({ selected: false });
    expect(weeklyTab.props.accessibilityState).toEqual({ selected: true });
  });

  it('calls onChange with the new tab id when an inactive tab is pressed', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    fireEvent.press(getByTestId('seg-tab-weekly'));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('weekly');
  });

  it('does not call onChange when the active tab is pressed', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    fireEvent.press(getByTestId('seg-tab-daily'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('triggers haptic feedback on tab change by default', () => {
    const onChange = jest.fn();
    const haptics = require('expo-haptics');
    haptics.impactAsync.mockClear();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    fireEvent.press(getByTestId('seg-tab-weekly'));
    expect(haptics.impactAsync).toHaveBeenCalledWith('light');
  });

  it('skips haptic when hapticOnPress is false', () => {
    const onChange = jest.fn();
    const haptics = require('expo-haptics');
    haptics.impactAsync.mockClear();
    const { getByTestId } = render(
      <SegmentedTabs
        testID="seg"
        tabs={TABS}
        activeId="daily"
        onChange={onChange}
        hapticOnPress={false}
      />,
    );
    fireEvent.press(getByTestId('seg-tab-weekly'));
    expect(haptics.impactAsync).not.toHaveBeenCalled();
  });

  it('renders the indicator element', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={TABS} activeId="daily" onChange={onChange} />,
    );
    expect(getByTestId('seg-indicator')).toBeTruthy();
  });

  it('supports controlled state changes when activeId prop updates', () => {
    function Controlled() {
      const [active, setActive] = useState('daily');
      return (
        <SegmentedTabs
          testID="seg"
          tabs={TABS}
          activeId={active}
          onChange={setActive}
        />
      );
    }
    const { getByTestId } = render(<Controlled />);
    fireEvent.press(getByTestId('seg-tab-weekly'));
    expect(getByTestId('seg-tab-weekly').props.accessibilityState).toEqual({ selected: true });
    expect(getByTestId('seg-tab-daily').props.accessibilityState).toEqual({ selected: false });
  });

  it('supports >2 tabs', () => {
    const onChange = jest.fn();
    const tabs: SegmentedTab[] = [
      { id: 'a', label: 'A' },
      { id: 'b', label: 'B' },
      { id: 'c', label: 'C' },
      { id: 'd', label: 'D' },
    ];
    const { getByTestId } = render(
      <SegmentedTabs testID="seg" tabs={tabs} activeId="b" onChange={onChange} />,
    );
    expect(getByTestId('seg-tab-a')).toBeTruthy();
    expect(getByTestId('seg-tab-d')).toBeTruthy();
    fireEvent.press(getByTestId('seg-tab-c'));
    expect(onChange).toHaveBeenCalledWith('c');
  });
});
