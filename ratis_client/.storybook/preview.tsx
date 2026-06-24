/**
 * Storybook RN 8 — preview / global decorators.
 *
 * - Background `Colors.bg` (#1a2428 — règle cardinale du design system).
 * - Le Provider de fonts (Inter) est inutile ici : Storybook tourne dans
 *   l'app Expo au runtime, donc `useDesignSystemFonts()` au root layout
 *   (cf `app/_layout.tsx`) a déjà chargé les weights.
 */

import React from 'react';
import { View } from 'react-native';
import type { Preview } from '@storybook/react-native';

import { Colors } from '../constants/theme';

const preview: Preview = {
  parameters: {
    backgrounds: {
      default: 'ratis-bg',
      values: [
        { name: 'ratis-bg', value: Colors.bg },
        { name: 'surface', value: Colors.surface },
        { name: 'overlay', value: Colors.overlay },
      ],
    },
  },
  decorators: [
    (Story) => (
      <View
        style={{
          flex: 1,
          backgroundColor: Colors.bg,
          padding: 16,
          justifyContent: 'center',
        }}
      >
        <Story />
      </View>
    ),
  ],
};

export default preview;
