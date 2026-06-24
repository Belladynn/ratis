/**
 * Storybook stories for <Button />.
 *
 * Coverage : the 4 variants (primary / secondary / gold / danger), the
 * disabled + loading states, an icon-prefixed variant, and the fullWidth
 * layout. Stories render against the Storybook background (`Colors.bg`) per
 * `.storybook/preview.tsx`.
 */

import React from 'react';
import { View, Text } from 'react-native';

import { Button, type ButtonProps } from '../button';
import { Colors, Spacing, Typography } from '../../../constants/theme';

/**
 * Storybook RN 8 ne réexporte pas `Meta`/`StoryObj` depuis le package
 * principal (`@storybook/react` n'est pas une dep root). On définit donc des
 * types légers maison — strictement suffisants pour `sb-rn-get-stories` qui
 * lit l'`export default` + les exports nommés sans introspecter la signature.
 */
type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
  argTypes?: Record<string, unknown>;
};
type Story<P> = { args?: Partial<P>; render?: () => React.ReactElement };

const meta: StoryMeta<ButtonProps> = {
  title: 'Design System/Button',
  component: Button,
  argTypes: {
    variant: {
      control: { type: 'select' },
      options: ['primary', 'secondary', 'gold', 'danger'],
    },
    size: { control: { type: 'select' }, options: ['sm', 'md'] },
    fullWidth: { control: 'boolean' },
    disabled: { control: 'boolean' },
    loading: { control: 'boolean' },
  },
  args: {
    label: 'Continuer',
    onPress: () => {},
  },
};

export default meta;

type ButtonStory = Story<ButtonProps>;

export const Primary: ButtonStory = {
  args: { variant: 'primary' },
};

export const Secondary: ButtonStory = {
  args: { variant: 'secondary', label: 'Annuler' },
};

export const GoldClaim: ButtonStory = {
  args: { variant: 'gold', size: 'sm', label: 'Récupérer' },
};

export const Danger: ButtonStory = {
  args: { variant: 'danger', label: 'Réinitialiser' },
};

export const Loading: ButtonStory = {
  args: { variant: 'primary', loading: true },
};

export const Disabled: ButtonStory = {
  args: { variant: 'primary', disabled: true },
};

export const FullWidth: ButtonStory = {
  args: { variant: 'primary', fullWidth: true, label: 'Continuer' },
};

export const WithIcon: ButtonStory = {
  args: {
    variant: 'primary',
    label: 'Scanner',
    icon: <Text style={{ color: '#fff', fontSize: 14 }}>📷</Text>,
  },
};

/**
 * Composite gallery — the 4 variants stacked vertically for visual QA.
 */
export const Gallery: ButtonStory = {
  render: () => (
    <View style={{ gap: Spacing.lg }}>
      <Text style={[Typography.label, { color: Colors.textSecondary }]}>
        VARIANTS
      </Text>
      <Button variant="primary" label="Primary" onPress={() => {}} />
      <Button variant="secondary" label="Secondary" onPress={() => {}} />
      <Button variant="gold" size="sm" label="Récupérer" onPress={() => {}} />
      <Button variant="danger" label="Reset" onPress={() => {}} />
    </View>
  ),
};
