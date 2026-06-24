/**
 * Storybook RN 8 — main config.
 *
 * Reads stories from `components/design-system/__stories__/*.stories.tsx`
 * (PR3+) and `components/dashboard/__stories__/*.stories.tsx` (PR4).
 * Storybook tournera **uniquement en mode dev** via la route
 * `app/_storybook.tsx` (cf README in this folder).
 */

import type { StorybookConfig } from '@storybook/react-native';

const config: StorybookConfig = {
  stories: [
    '../components/design-system/__stories__/**/*.stories.@(ts|tsx)',
    '../components/dashboard/__stories__/**/*.stories.@(ts|tsx)',
  ],
  addons: [],
};

export default config;
