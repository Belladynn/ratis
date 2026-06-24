/**
 * Learn more about light and dark modes:
 * https://docs.expo.dev/guides/color-schemes/
 */

import { LegacyColors } from '@/constants/theme';
import { useColorScheme } from '@/hooks/use-color-scheme';

// NOTE: this hook still drives the legacy light/dark scheme used by the
// untouched `Themed{Text,View}` + `Collapsible`. The pivot palette
// (`Colors` from `@/constants/theme`) is dark-only and is consumed
// directly by the new design-system components (PR3+). Migration of
// these legacy call-sites is scheduled with the Dashboard refactor (PR4).
export function useThemeColor(
  props: { light?: string; dark?: string },
  colorName: keyof typeof LegacyColors.light & keyof typeof LegacyColors.dark
) {
  const theme = useColorScheme() ?? 'light';
  const colorFromProps = props[theme];

  if (colorFromProps) {
    return colorFromProps;
  } else {
    return LegacyColors[theme][colorName];
  }
}
