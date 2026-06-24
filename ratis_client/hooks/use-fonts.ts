/**
 * Inter font loading hook — design system PR2.
 *
 * Pourquoi un hook dédié plutôt qu'un appel direct à `useFonts` dans
 * `_layout.tsx` :
 *
 *   1. **Encapsulation** — la liste des weights nécessaires vit à un seul
 *      endroit. Si on en ajoute (ou en retire) un, c'est ici qu'on bouge.
 *   2. **Test** — on teste que les 6 weights attendus sont bien forwardés
 *      à `expo-font.useFonts` sans avoir à mocker tout le root layout.
 *   3. **Re-use** — un futur écran peut appeler `useDesignSystemFonts()` si
 *      jamais on bouge le splash gate (ex: onboarding fullscreen).
 *
 * Référence : `ARCH_design_system.md` § Typographie (Inter uniquement,
 * weights 400-900 chargés via `expo-font` Google Fonts).
 */

import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  Inter_800ExtraBold,
  Inter_900Black,
  useFonts,
} from '@expo-google-fonts/inter';

/**
 * Liste publique des weights utilisés par le design system. L'ordre est
 * stable et les noms correspondent aux `fontFamily` de `Typography.*` dans
 * `constants/theme.ts`.
 */
export const INTER_WEIGHTS = [
  'Inter_400Regular',
  'Inter_500Medium',
  'Inter_600SemiBold',
  'Inter_700Bold',
  'Inter_800ExtraBold',
  'Inter_900Black',
] as const;

export type InterWeight = (typeof INTER_WEIGHTS)[number];

/**
 * Charge les 6 weights Inter du design system. Renvoie `[loaded, error]`
 * conforme à `expo-font.useFonts`. Tant que `loaded === false`, le splash
 * screen reste actif côté `_layout.tsx`.
 */
export function useDesignSystemFonts(): [boolean, Error | null] {
  return useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
    Inter_800ExtraBold,
    Inter_900Black,
  });
}
