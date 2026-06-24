/**
 * Storybook RN 8 — entry view.
 *
 * Importé depuis `app/_storybook.tsx` quand on lance l'app en mode
 * Storybook (`EXPO_PUBLIC_STORYBOOK_ENABLED=true` au start).
 *
 * Note : Storybook RN 8 lit la liste des stories depuis un fichier
 * `storybook.requires.ts` généré par `sb-rn-get-stories`. Tant qu'aucune
 * story n'a été écrite (PR3+), `view.getStorybookUI` rend un écran vide
 * — pas un crash. Donc PR2 peut ship sans stories, PR3 ajoutera les
 * premières (Button) qui se collecteront automatiquement.
 */

import { view } from './storybook.requires';

export default view.getStorybookUI({
  enableWebsockets: false,
});
