// Ratis — Liste tab data + helpers (suggestions, templates, animated counter)

const LIST_CATEGORIES = {
  dairy:    { label: 'Laitages',    icon: '🥛', color: '#A5F3FC' },
  produce:  { label: 'F&L',         icon: '🥦', color: '#86EFAC' },
  meat:     { label: 'Viande',      icon: '🥩', color: '#FDA4AF' },
  bakery:   { label: 'Pain',        icon: '🥖', color: '#FCD34D' },
  pantry:   { label: 'Épicerie',    icon: '🥫', color: '#FED7AA' },
  drinks:   { label: 'Boissons',    icon: '🧃', color: '#A78BFA' },
  frozen:   { label: 'Surgelés',    icon: '🧊', color: '#93C5FD' },
  snacks:   { label: 'Snacks',      icon: '🍪', color: '#F9A8D4' },
  hygiene:  { label: 'Hygiène',     icon: '🧴', color: '#C4B5FD' },
  other:    { label: 'Autre',       icon: '🛒', color: '#9CA3AF' },
};

// Suggestions based on user's scan history — "you often buy these"
const SUGGESTIONS = [
  { name: 'Pain de mie', brand: 'Harrys', est: 1.95, cat: 'bakery', freq: 'chaque semaine', lastBuy: 'il y a 6 jours' },
  { name: 'Œufs x12', brand: 'Matines', est: 3.40, cat: 'pantry', freq: '2× par mois', lastBuy: 'il y a 11 jours' },
  { name: 'Beurre demi-sel', brand: 'Président', est: 2.85, cat: 'dairy', freq: '1× par mois', lastBuy: 'il y a 18 jours' },
  { name: 'Riz basmati 1kg', brand: 'Taureau Ailé', est: 3.20, cat: 'pantry', freq: '1× par mois', lastBuy: 'il y a 22 jours' },
  { name: 'Salade verte', brand: '', est: 1.49, cat: 'produce', freq: 'chaque semaine', lastBuy: 'il y a 4 jours' },
  { name: 'Jambon blanc 4t.', brand: 'Fleury Michon', est: 2.95, cat: 'meat', freq: '1× par semaine', lastBuy: 'il y a 8 jours' },
  { name: 'Eau gazeuse 6×1L', brand: 'Perrier', est: 4.50, cat: 'drinks', freq: '2× par mois', lastBuy: 'il y a 13 jours' },
  { name: 'Lessive liquide', brand: 'Skip', est: 8.90, cat: 'hygiene', freq: '1× par mois', lastBuy: 'il y a 24 jours' },
];

// Quick-list templates
const LIST_TEMPLATES = [
  {
    id: 'breakfast', label: 'Petit-déj', icon: '☕', color: '#FCD34D',
    items: [
      { name: 'Pain de mie', brand: 'Harrys', est: 1.95, cat: 'bakery' },
      { name: 'Confiture fraises', brand: 'Bonne Maman', est: 2.95, cat: 'pantry' },
      { name: 'Lait demi-écrémé 1L', brand: 'Lactel', est: 1.05, cat: 'dairy' },
      { name: 'Beurre demi-sel', brand: 'Président', est: 2.85, cat: 'dairy' },
      { name: 'Café moulu 250g', brand: 'Carte Noire', est: 4.20, cat: 'drinks' },
    ],
  },
  {
    id: 'aperitif', label: 'Apéro', icon: '🍷', color: '#FB923C',
    items: [
      { name: 'Chips nature 150g', brand: 'Lay\'s', est: 2.50, cat: 'snacks' },
      { name: 'Olives vertes', brand: '', est: 3.20, cat: 'pantry' },
      { name: 'Saucisson sec', brand: 'Justin Bridou', est: 5.95, cat: 'meat' },
      { name: 'Rosé 75cl', brand: 'Côtes de Provence', est: 8.90, cat: 'drinks' },
      { name: 'Crackers', brand: 'Tuc', est: 1.85, cat: 'snacks' },
    ],
  },
  {
    id: 'week', label: 'Semaine basique', icon: '🛒', color: '#86EFAC',
    items: [
      { name: 'Pâtes penne 500g', brand: 'Barilla', est: 1.85, cat: 'pantry' },
      { name: 'Sauce tomate basilic', brand: 'Panzani', est: 2.10, cat: 'pantry' },
      { name: 'Œufs x12', brand: 'Matines', est: 3.40, cat: 'pantry' },
      { name: 'Lait 1L', brand: 'Lactel', est: 1.05, cat: 'dairy' },
      { name: 'Yaourts nature x16', brand: 'Danone', est: 3.95, cat: 'dairy' },
      { name: 'Pommes (kg)', brand: '', est: 1.99, cat: 'produce' },
      { name: 'Bananes (kg)', brand: '', est: 1.49, cat: 'produce' },
      { name: 'Poulet escalope', brand: 'Maître Coq', est: 6.50, cat: 'meat' },
    ],
  },
  {
    id: 'asian', label: 'Cuisine asiatique', icon: '🍜', color: '#F9A8D4',
    items: [
      { name: 'Sauce soja', brand: 'Kikkoman', est: 3.50, cat: 'pantry' },
      { name: 'Nouilles ramen', brand: 'Nissin', est: 2.20, cat: 'pantry' },
      { name: 'Gingembre frais', brand: '', est: 1.10, cat: 'produce' },
      { name: 'Coriandre fraîche', brand: '', est: 1.20, cat: 'produce' },
      { name: 'Tofu nature', brand: 'Soy', est: 2.80, cat: 'pantry' },
    ],
  },
  {
    id: 'bbq', label: 'Barbecue', icon: '🔥', color: '#FB7185',
    items: [
      { name: 'Merguez x10', brand: '', est: 6.95, cat: 'meat' },
      { name: 'Saucisses', brand: 'Herta', est: 3.95, cat: 'meat' },
      { name: 'Pain à hot-dog x6', brand: '', est: 1.85, cat: 'bakery' },
      { name: 'Charbon de bois', brand: '', est: 7.50, cat: 'other' },
      { name: 'Bière blonde 6×33cl', brand: 'Heineken', est: 5.90, cat: 'drinks' },
    ],
  },
];

// Local autocomplete pool — names of products the user has scanned before
const AUTOCOMPLETE_POOL = [
  'Pain de mie', 'Pain complet', 'Baguette tradition', 'Pain au lait',
  'Lait demi-écrémé 1L', 'Lait entier 1L', 'Lait sans lactose', 'Lait d\'amande',
  'Yaourts nature x16', 'Yaourts grec x4', 'Yaourts aux fruits x12',
  'Beurre doux', 'Beurre demi-sel', 'Beurre bio',
  'Œufs x6', 'Œufs x12', 'Œufs bio x6',
  'Pâtes penne 500g', 'Pâtes spaghetti 500g', 'Pâtes farfalle',
  'Riz basmati 1kg', 'Riz complet 500g', 'Riz arborio 500g',
  'Pommes (kg)', 'Bananes (kg)', 'Oranges (kg)', 'Citrons (kg)', 'Avocats x2',
  'Tomates cerises 250g', 'Tomates grappes (kg)', 'Concombre',
  'Salade verte', 'Salade roquette', 'Salade mâche',
  'Jambon blanc 4t.', 'Jambon cru 80g', 'Saucisson sec',
  'Poulet escalope', 'Steak haché 5%', 'Côte de porc',
  'Saumon frais 2 pavés', 'Cabillaud surgelé', 'Crevettes décortiquées',
  'Café moulu 250g', 'Café en grains 1kg', 'Capsules Nespresso x10', 'Thé vert 25 sachets',
  'Eau plate 6×1.5L', 'Eau gazeuse 6×1L', 'Coca-Cola 1.5L', 'Jus d\'orange pressé',
  'Chips nature 150g', 'Biscuits chocolat', 'Madeleines', 'Crackers',
  'Lessive liquide', 'Liquide vaisselle', 'Papier toilette x12', 'Mouchoirs',
  'Huile d\'olive 1L', 'Vinaigre balsamique', 'Sel fin', 'Poivre moulu',
  'Sucre en poudre 1kg', 'Farine T55 1kg', 'Levure boulangère',
];

// Nearby stores with full per-store data for the route
const ROUTE_STORES_V2 = [
  {
    id: 'lidl', name: 'Lidl Charonne', distance: 0.4, time: 6, items: 3, savings: 2.40,
    color: '#4DD4B3', logo: 'L',
    items_list: ['Lait demi-écrémé 1L', 'Bananes (kg)', 'Tomates cerises 250g'],
  },
  {
    id: 'carre', name: 'Carrefour Voltaire', distance: 1.2, time: 14, items: 2, savings: 1.10,
    color: '#A78BFA', logo: 'C',
    items_list: ['Pâtes penne 500g', 'Yaourts grec x4'],
  },
  {
    id: 'auchan', name: 'Auchan Nation', distance: 2.8, time: 22, items: 1, savings: 0.85,
    color: '#FFB800', logo: 'A',
    items_list: ['Café Nespresso x10'],
  },
];

// Animated counter — smoothly tweens between values when target changes
function AnimatedNumber({ value, format, duration = 600, style }) {
  const [displayed, setDisplayed] = React.useState(value);
  const fromRef = React.useRef(value);
  const startRef = React.useRef(performance.now());

  React.useEffect(() => {
    if (value === displayed) return;
    fromRef.current = displayed;
    startRef.current = performance.now();
    let raf;
    const tick = (now) => {
      const t = Math.min(1, (now - startRef.current) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      const v = fromRef.current + (value - fromRef.current) * eased;
      setDisplayed(v);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return <span style={style}>{format ? format(displayed) : displayed.toFixed(2)}</span>;
}

window.RatisListeData = {
  LIST_CATEGORIES, SUGGESTIONS, LIST_TEMPLATES, AUTOCOMPLETE_POOL, ROUTE_STORES_V2,
  AnimatedNumber,
};
