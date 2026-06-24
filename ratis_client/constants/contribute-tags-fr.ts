// ratis_client/constants/contribute-tags-fr.ts
//
// Curated French tag lists for the « Compléter ce produit » screen.
// Used by `components/completer/field-input-tags.tsx` when the
// missing field is `categories_tags` or `labels_tags`.
//
// V1 : hardcoded FE constants. Updates ship via OTA. Backend accepts
// any string for these fields (no taxonomy validation), so a typo
// here just persists a slightly different tag string — no crash.
// V2 may replace this with a backend autocomplete endpoint.

export interface CuratedTag {
  /** OFF taxonomy slug, e.g. "en:dairies" — sent to backend as-is. */
  tag: string;
  /** Human-readable French label, shown on the chip. */
  label: string;
}

export const CATEGORY_TAGS_FR: readonly CuratedTag[] = [
  { tag: 'en:dairies', label: 'Produits laitiers' },
  { tag: 'en:cheeses', label: 'Fromages' },
  { tag: 'en:yogurts', label: 'Yaourts' },
  { tag: 'en:meats', label: 'Viandes' },
  { tag: 'en:poultry', label: 'Volailles' },
  { tag: 'en:fishes', label: 'Poissons' },
  { tag: 'en:eggs', label: 'Œufs' },
  { tag: 'en:fruits', label: 'Fruits' },
  { tag: 'en:vegetables', label: 'Légumes' },
  { tag: 'en:breads', label: 'Pains' },
  { tag: 'en:pastas', label: 'Pâtes' },
  { tag: 'en:rices', label: 'Riz' },
  { tag: 'en:cereals', label: 'Céréales' },
  { tag: 'en:beverages', label: 'Boissons' },
  { tag: 'en:waters', label: 'Eaux' },
  { tag: 'en:juices', label: 'Jus' },
  { tag: 'en:sodas', label: 'Sodas' },
  { tag: 'en:alcoholic-beverages', label: 'Boissons alcoolisées' },
  { tag: 'en:snacks', label: 'Snacks' },
  { tag: 'en:chocolates', label: 'Chocolats' },
  { tag: 'en:biscuits', label: 'Biscuits' },
  { tag: 'en:ice-creams', label: 'Glaces' },
  { tag: 'en:frozen-foods', label: 'Surgelés' },
  { tag: 'en:condiments', label: 'Condiments' },
  { tag: 'en:sauces', label: 'Sauces' },
  { tag: 'en:oils', label: 'Huiles' },
  { tag: 'en:sweeteners', label: 'Sucres et édulcorants' },
  { tag: 'en:plant-based-foods', label: 'Aliments végétaux' },
  { tag: 'en:prepared-meals', label: 'Plats préparés' },
  { tag: 'en:baby-foods', label: 'Aliments pour bébés' },
];

export const LABEL_TAGS_FR: readonly CuratedTag[] = [
  { tag: 'en:organic', label: 'Bio 🌱' },
  { tag: 'en:fair-trade', label: 'Commerce équitable' },
  { tag: 'fr:label-rouge', label: 'Label Rouge' },
  { tag: 'fr:aop', label: 'AOP' },
  { tag: 'fr:aoc', label: 'AOC' },
  { tag: 'fr:igp', label: 'IGP' },
  { tag: 'en:no-gluten', label: 'Sans gluten' },
  { tag: 'en:no-lactose', label: 'Sans lactose' },
  { tag: 'en:vegan', label: 'Vegan' },
  { tag: 'en:vegetarian', label: 'Végétarien' },
  { tag: 'en:no-added-sugar', label: 'Sans sucres ajoutés' },
  { tag: 'en:low-fat', label: 'Allégé en matières grasses' },
  { tag: 'en:nutriscore-a', label: 'Nutri-Score A' },
  { tag: 'en:nutriscore-b', label: 'Nutri-Score B' },
  { tag: 'en:eco-score-a', label: 'Eco-Score A' },
  { tag: 'fr:fabrique-en-france', label: 'Fabriqué en France 🇫🇷' },
  { tag: 'fr:origine-france', label: 'Origine France 🇫🇷' },
  { tag: 'en:msc-certified', label: 'MSC (pêche durable)' },
];
