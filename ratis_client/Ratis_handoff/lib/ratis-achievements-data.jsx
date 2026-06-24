// ─────────────────────────────────────────────────────────────────────
// Achievements data model — 100+ achievements across 7 categories, 5 rarities
// ─────────────────────────────────────────────────────────────────────

// Rarities (paliers Ratis) — du plus commun au plus rare.
// L'effet hologramme/reflet est réservé aux paliers >= émeraude (holo: true).
const RARITIES = {
  terracotta: { label: 'Terre cuite', color: '#B25A3C', glow: 'rgba(178,90,60,0.30)',  metal: 'linear-gradient(135deg, #6B2E1A, #B25A3C, #6B2E1A)', holo: false, xpRange: [10, 25] },
  bronze:     { label: 'Bronze',      color: '#A87233', glow: 'rgba(168,114,51,0.35)', metal: 'linear-gradient(135deg, #5C3A12, #CD7F32, #5C3A12)', holo: false, xpRange: [25, 50] },
  copper:     { label: 'Cuivre',      color: '#D27F4A', glow: 'rgba(210,127,74,0.40)', metal: 'linear-gradient(135deg, #7A3F1A, #E8915A, #7A3F1A)', holo: false, xpRange: [50, 100] },
  silver:     { label: 'Argent',      color: '#C8CDD4', glow: 'rgba(200,205,212,0.45)',metal: 'linear-gradient(135deg, #6B7280, #E5E7EB, #6B7280)', holo: false, xpRange: [100, 175] },
  gold:       { label: 'Or',          color: '#F2C744', glow: 'rgba(242,199,68,0.55)', metal: 'linear-gradient(135deg, #92400E, #FBBF24, #92400E)', holo: false, xpRange: [175, 300] },
  emerald:    { label: 'Émeraude',    color: '#34D399', glow: 'rgba(52,211,153,0.60)', metal: 'linear-gradient(135deg, #064E3B, #34D399, #064E3B)', holo: true,  xpRange: [300, 500] },
  sapphire:   { label: 'Saphir',      color: '#3B82F6', glow: 'rgba(59,130,246,0.65)', metal: 'linear-gradient(135deg, #1E3A8A, #60A5FA, #1E3A8A)', holo: true,  xpRange: [500, 750] },
  ruby:       { label: 'Rubis',       color: '#EF4444', glow: 'rgba(239,68,68,0.70)',  metal: 'linear-gradient(135deg, #7F1D1D, #FB7185, #7F1D1D)', holo: true,  xpRange: [750, 1100] },
  crystal:    { label: 'Cristal',     color: '#A5F3FC', glow: 'rgba(165,243,252,0.75)',metal: 'linear-gradient(135deg, #0E7490, #A5F3FC, #0E7490)', holo: true,  xpRange: [1100, 1600] },
  diamond:    { label: 'Diamant',     color: '#E0F2FE', glow: 'rgba(224,242,254,0.85)',metal: 'linear-gradient(135deg, #1E293B, #F8FAFC, #818CF8, #F8FAFC, #1E293B)', holo: true, xpRange: [1600, 2500] },
};

// Category metadata
const CATEGORIES = {
  volume:      { label: 'Scans',         icon: '📷', color: '#FB923C' },
  savings:     { label: 'Économies',     icon: '💰', color: '#FBBF24' },
  streak:      { label: 'Régularité',    icon: '🔥', color: '#F87171' },
  social:      { label: 'Social',        icon: '👥', color: '#60A5FA' },
  exploration: { label: 'Exploration',   icon: '🗺️', color: '#34D399' },
  seasonal:    { label: 'Saisonniers',   icon: '🌸', color: '#F472B6' },
  secret:      { label: 'Secrets',       icon: '❓', color: '#C084FC' },
};

// Helper to construct a tier of achievements (e.g. 10/100/1000 tickets scanned)
const tier = (id, label, description, icon, rarity, category, progress, target, status) => ({
  id, label, description, icon, rarity, category, progress, target, status,
});

const ACHIEVEMENTS = [
  // ── VOLUME (scans) — paliers ─────────────────────────────────────
  tier('v_first',   'Premier scan',         'Scanner ton tout premier ticket',          '🎬', 'terracotta',    'volume', 1, 1, 'unlocked'),
  tier('v_10',      'Habitué·e',            'Scanner 10 tickets',                       '📋', 'bronze',    'volume', 10, 10, 'unlocked'),
  tier('v_50',      'Cinquantaine',         'Scanner 50 tickets',                       '📑', 'copper',  'volume', 47, 50, 'in_progress'),
  tier('v_100',     'Centurion',            'Scanner 100 tickets',                      '💯', 'silver',      'volume', 47, 100, 'in_progress'),
  tier('v_500',     'Demi-millier',         'Scanner 500 tickets',                      '📊', 'gold',      'volume', 47, 500, 'in_progress'),
  tier('v_1000',    'Millier',              'Scanner 1000 tickets',                     '🏆', 'crystal', 'volume', 47, 1000, 'locked'),
  tier('v_barcode_first', 'Code-barres décodé', 'Scanner ton premier code-barres',     '📡', 'terracotta',    'volume', 1, 1, 'unlocked'),
  tier('v_barcode_100', 'Lecteur acharné', 'Scanner 100 codes-barres',                  '🔍', 'silver',      'volume', 23, 100, 'in_progress'),
  tier('v_label_first', 'Étiquette traquée', 'Scanner ta première étiquette magasin', '🏷️', 'bronze',    'volume', 1, 1, 'unlocked'),
  tier('v_label_50', 'Œil de lynx',        'Scanner 50 étiquettes',                    '👁️', 'silver',  'volume', 12, 50, 'in_progress'),
  tier('v_speed',   'Speedrunner',         'Scanner 5 tickets en moins de 2 min',      '⚡', 'gold',      'volume', 0, 5, 'locked'),
  tier('v_marathon', 'Marathon',           'Scanner 20 tickets en une journée',        '🏃', 'emerald',      'volume', 0, 20, 'locked'),

  // ── SAVINGS (économies) ──────────────────────────────────────────
  tier('s_1',      'Première éco',         'Économiser ton premier euro',              '🪙', 'terracotta',    'savings', 1, 1, 'unlocked'),
  tier('s_10',     '10 balles',            'Économiser 10 €',                          '💵', 'bronze',    'savings', 10, 10, 'unlocked'),
  tier('s_50',     'Demi-bil',             'Économiser 50 €',                          '💴', 'copper',  'savings', 47.95, 50, 'in_progress'),
  tier('s_100',    'Stack',                'Économiser 100 €',                         '💶', 'gold',      'savings', 47.95, 100, 'in_progress'),
  tier('s_500',    'Demi-millier',         'Économiser 500 €',                         '💷', 'sapphire',      'savings', 47.95, 500, 'in_progress'),
  tier('s_1000',   'Quatre chiffres',      'Économiser 1000 €',                        '💎', 'crystal', 'savings', 47.95, 1000, 'locked'),
  tier('s_day_5',  'Journée à 5€',         'Économiser 5 € en une journée',            '🌅', 'silver',  'savings', 5, 5, 'unlocked'),
  tier('s_day_20', 'Grosse journée',       'Économiser 20 € en une journée',           '🌟', 'emerald',      'savings', 12.40, 20, 'in_progress'),
  tier('s_week_50', 'Semaine record',      'Économiser 50 € en une semaine',           '📈', 'gold',      'savings', 0, 50, 'locked'),
  tier('s_month',  'Mois royal',           'Économiser 100 € en un mois',              '👑', 'emerald',      'savings', 0, 100, 'locked'),
  tier('s_first_promo', 'Bonne affaire',   'Trouver ta première promo',                '🎯', 'terracotta',    'savings', 1, 1, 'unlocked'),
  tier('s_promo_50', 'Chasseur de promos', 'Trouver 50 promos',                        '🎯', 'silver',      'savings', 18, 50, 'in_progress'),

  // ── STREAK (régularité) ──────────────────────────────────────────
  tier('r_3',      'Trio',                 'Streak de 3 jours',                        '🔥', 'bronze',    'streak', 3, 3, 'unlocked'),
  tier('r_7',      'Semaine pleine',       'Streak de 7 jours',                        '🔥', 'copper',  'streak', 7, 7, 'unlocked'),
  tier('r_14',     'Quinzaine',            'Streak de 14 jours',                       '🔥', 'silver',      'streak', 7, 14, 'in_progress'),
  tier('r_30',     'Mois sans rater',      'Streak de 30 jours',                       '🔥', 'sapphire',      'streak', 7, 30, 'in_progress'),
  tier('r_100',    'Centenaire',           'Streak de 100 jours',                      '🌋', 'crystal', 'streak', 7, 100, 'in_progress'),
  tier('r_365',    'Une année',            'Streak de 365 jours',                      '🌌', 'diamond', 'streak', 7, 365, 'locked'),
  tier('r_revive', 'Phénix',               'Récupérer un streak cassé avec une réparation', '🔄', 'gold',     'streak', 0, 1, 'locked'),
  tier('r_feed_7', 'Bon nourricier',       'Nourrir Cabé 7 jours d\'affilée',          '🍞', 'silver',  'streak', 7, 7, 'unlocked'),
  tier('r_feed_30','Maître des réserves',  'Nourrir Cabé 30 jours d\'affilée',         '🥖', 'gold',      'streak', 7, 30, 'in_progress'),
  tier('r_login_7','Fidèle',              'Se connecter 7 jours sur 7',               '📅', 'terracotta',    'streak', 7, 7, 'unlocked'),

  // ── SOCIAL ──────────────────────────────────────────────────────
  tier('soc_invite_1', 'Recruteur',        'Inviter 1 ami',                           '🤝', 'bronze',    'social', 0, 1, 'locked'),
  tier('soc_invite_5', 'Bande de potes',   'Inviter 5 amis',                          '👫', 'copper',  'social', 0, 5, 'locked'),
  tier('soc_invite_10','Réseau',           'Inviter 10 amis',                         '🌐', 'gold',      'social', 0, 10, 'locked'),
  tier('soc_invite_25','Influenceur',      'Inviter 25 amis',                         '📣', 'emerald',      'social', 0, 25, 'locked'),
  tier('soc_invite_50','Évangéliste',      'Inviter 50 amis',                         '🎤', 'crystal', 'social', 0, 50, 'locked'),
  tier('soc_share_1',  'Partageur',        'Partager ta première bonne affaire',     '📤', 'terracotta',    'social', 0, 1, 'locked'),
  tier('soc_share_25', 'Bouche à oreille', 'Partager 25 bonnes affaires',            '🗣️', 'emerald',      'social', 0, 25, 'locked'),
  tier('soc_friend_savings','Coach',       'Un ami invité économise 10 €',           '🎓', 'sapphire',      'social', 0, 1, 'locked'),
  tier('soc_team',    'Esprit d\'équipe',   'Compléter une mission collaborative',    '🏅', 'silver',      'social', 0, 1, 'locked'),

  // ── EXPLORATION ─────────────────────────────────────────────────
  tier('exp_brand_5',  'Curieux·se',        'Scanner dans 5 enseignes différentes', '🛒', 'bronze',    'exploration', 3, 5, 'in_progress'),
  tier('exp_brand_20', 'Globe-shoppeur',    'Scanner dans 20 enseignes différentes','🌍', 'silver',      'exploration', 3, 20, 'in_progress'),
  tier('exp_cat_5',    'Tour des rayons',  'Scanner dans 5 catégories différentes', '🥦', 'silver',  'exploration', 4, 5, 'in_progress'),
  tier('exp_cat_15',   'Encyclopédiste',   'Scanner dans 15 catégories différentes','📚', 'gold',      'exploration', 4, 15, 'in_progress'),
  tier('exp_first_unknown', 'Découvreur·se','Scanner un produit jamais vu sur Ratis','🔭', 'gold',      'exploration', 1, 1, 'unlocked'),
  tier('exp_unknown_10', 'Pionnier·e',     'Découvrir 10 produits jamais vus',     '🚀', 'emerald',      'exploration', 1, 10, 'in_progress'),
  tier('exp_organic',  'Bio-attitude',     'Scanner 20 produits bio',              '🌱', 'copper',  'exploration', 6, 20, 'in_progress'),
  tier('exp_local',    'Local et fier',    'Scanner 30 produits français',         '🇫🇷', 'gold',      'exploration', 11, 30, 'in_progress'),
  tier('exp_fillup_10','Remplisseur·se',   'Compléter 10 fiches produit',           '✏️', 'silver',  'exploration', 4, 10, 'in_progress'),
  tier('exp_fillup_50','Documentaliste',   'Compléter 50 fiches produit',           '📔', 'sapphire',      'exploration', 4, 50, 'in_progress'),

  // ── SEASONAL (saisonniers / événementiels) ───────────────────────
  tier('sea_winter',   'Hiver 25',         'Avoir participé au Pass Hiver 25',     '❄️', 'emerald',      'seasonal', 1, 1, 'unlocked'),
  tier('sea_spring_lvl10', 'Bourgeon',     'Atteindre le niveau 10 du Pass Printemps 26', '🌷', 'copper', 'seasonal', 10, 10, 'unlocked'),
  tier('sea_spring_lvl25', 'Floraison',    'Atteindre le niveau 25 du Pass Printemps 26', '🌸', 'silver',     'seasonal', 12, 25, 'in_progress'),
  tier('sea_spring_lvl50', 'Pleine éclosion', 'Terminer le Pass Printemps 26',    '🌺', 'crystal', 'seasonal', 12, 50, 'in_progress'),
  tier('sea_xmas',     'Noël 25',          'Scanner pendant les fêtes de Noël',    '🎄', 'silver',      'seasonal', 1, 1, 'unlocked'),
  tier('sea_blackfri', 'Black Friday',     'Économiser 50 € pendant le Black Friday','🛍️', 'gold',     'seasonal', 0, 50, 'locked'),
  tier('sea_anniv',    'Anniversaire',     'Être actif·ve un an après ton inscription','🎂', 'emerald',    'seasonal', 0, 365, 'locked'),
  tier('sea_summer',   'Été 26',           'Participer au Pass Été 26',            '☀️', 'gold',      'seasonal', 0, 1, 'locked'),

  // ── SECRETS (cachés / easter eggs) ──────────────────────────────
  tier('sec_3am',      '???',              'Succès secret',                       '❓', 'gold',      'secret', 0, 1, 'locked'),
  tier('sec_newyear',  '???',              'Succès secret',                       '❓', 'emerald',      'secret', 0, 1, 'locked'),
  tier('sec_friday13', '???',              'Succès secret',                       '❓', 'sapphire',      'secret', 0, 1, 'locked'),
  tier('sec_klein',    '???',              'Succès secret',                       '❓', 'gold',      'secret', 0, 1, 'locked'),
  tier('sec_konami',   '???',              'Succès secret',                       '❓', 'diamond', 'secret', 0, 1, 'locked'),
  tier('sec_1euro',    'Le centime perdu',  'Économiser exactement 1,00 € sur un seul ticket', '🔍', 'silver', 'secret', 1, 1, 'unlocked'),
  tier('sec_palindrome','???',             'Succès secret',                       '❓', 'emerald',      'secret', 0, 1, 'locked'),
  tier('sec_zerowaste','???',              'Succès secret',                       '❓', 'silver',      'secret', 0, 1, 'locked'),
  tier('sec_birthday', '???',              'Succès secret',                       '❓', 'diamond', 'secret', 0, 1, 'locked'),
  tier('sec_cabe_love','???',              'Succès secret',                       '❓', 'sapphire',      'secret', 0, 1, 'locked'),

  // Filler to push to 100+
  tier('v_morning',   'Lève-tôt',          'Scanner avant 8h du matin',            '🌅', 'silver',  'volume', 0, 1, 'locked'),
  tier('v_evening',   'Couche-tard',        'Scanner après 22h',                    '🌙', 'copper',  'volume', 1, 1, 'unlocked'),
  tier('v_weekend',   'Week-end shopping', 'Scanner samedi ET dimanche',           '🎒', 'terracotta',    'volume', 1, 1, 'unlocked'),
  tier('v_combo_3',   'Combo x3',          'Scanner 3 fois dans la même heure',    '🎯', 'silver',  'volume', 0, 3, 'locked'),
  tier('s_big_ticket', 'Gros ticket',     'Scanner un ticket de plus de 100 €',    '💳', 'silver',      'savings', 0, 1, 'locked'),
  tier('s_tiny_ticket','Mini-courses',    'Scanner un ticket de moins de 5 €',    '🥖', 'bronze',    'savings', 1, 1, 'unlocked'),
  tier('exp_5_stores_day','Tour de ville', 'Scanner dans 5 magasins en une journée', '🏪', 'gold',    'exploration', 0, 5, 'locked'),
  tier('soc_first_msg', 'Premier mot',    'Envoyer un premier message à un ami',  '💬', 'terracotta',    'social', 0, 1, 'locked'),
  tier('r_repair',    'Bricoleur·se',      'Réparer la maison de Cabé',             '🔧', 'bronze',    'streak', 1, 1, 'unlocked'),
  tier('r_mult_max',  'Multiplicateur max', 'Atteindre le multiplicateur maximum', '⚡', 'emerald',     'streak', 0, 1, 'locked'),
  tier('exp_jar_full','Tirelire pleine',  'Remplir ta tirelire pour la 1ère fois', '🏺', 'gold',     'exploration', 0, 1, 'locked'),
  tier('exp_jar_5',   'Collectionneur·se', 'Remplir 5 tirelires',                   '🏺', 'sapphire',    'exploration', 0, 5, 'locked'),
  tier('s_perfect',   'Pile poil',         'Économiser exactement le prix d\'un Pass', '🎯', 'gold', 'savings', 0, 1, 'locked'),
  tier('soc_top_friend','Meilleur·e ami·e','Avoir un ami qui scanne plus que toi', '🥇', 'gold',     'social', 0, 1, 'locked'),
  tier('exp_24_categories','Tout goûter','Scanner dans 24 catégories différentes','🌈', 'crystal','exploration', 4, 24, 'in_progress'),
  tier('v_5000',      '5K',                 'Scanner 5000 tickets',                 '🏆', 'diamond','volume', 47, 5000, 'locked'),
  tier('s_5000',      'Petit fortune',     'Économiser 5000 €',                    '💎', 'diamond','savings', 47.95, 5000, 'locked'),
  tier('r_500',       '500 jours',          'Streak de 500 jours',                  '🌋', 'crystal','streak', 7, 500, 'locked'),
  tier('soc_100_invites','Légende sociale','Inviter 100 amis',                     '👑', 'diamond','social', 0, 100, 'locked'),
  tier('exp_all_brands','Tour de France',   'Scanner dans 50 enseignes différentes','🗺️','crystal','exploration', 3, 50, 'in_progress'),
  tier('sea_all_passes','Collectionneur de passes', 'Avoir terminé 4 pass saisonniers consécutifs', '🎟️', 'crystal', 'seasonal', 0, 4, 'locked'),
];

window.RatisAchievements = { ACHIEVEMENTS, RARITIES, CATEGORIES };
