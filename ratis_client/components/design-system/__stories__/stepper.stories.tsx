/**
 * Storybook stories for <Stepper /> (qty +/-).
 *
 * Coverage : default (interactive), at-min and at-max boundary states.
 */

import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Stepper, type StepperProps } from '../stepper';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type StepperStory = {
  args?: Partial<StepperProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<StepperProps> = {
  title: 'Design System/Stepper',
  component: Stepper,
};
export default meta;

function Controlled({
  initial = 1,
  min = 1,
  max = 9,
}: {
  initial?: number;
  min?: number;
  max?: number;
}) {
  const [value, setValue] = useState(initial);
  return (
    <View style={styles.row}>
      <Stepper value={value} onChange={setValue} min={min} max={max} />
      <Text style={[Typography.bodySm, { color: Colors.textSecondary }]}>
        value: {value}
      </Text>
    </View>
  );
}

export const Default: StepperStory = {
  render: () => <Controlled initial={1} min={1} max={9} />,
};

export const AtMin: StepperStory = {
  render: () => <Controlled initial={0} min={0} max={9} />,
};

export const AtMax: StepperStory = {
  render: () => <Controlled initial={9} min={0} max={9} />,
};

export const Disabled: StepperStory = {
  args: { value: 3, onChange: () => undefined, disabled: true },
};

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.md,
    padding: Spacing.lg,
    backgroundColor: Colors.bg,
  },
});
