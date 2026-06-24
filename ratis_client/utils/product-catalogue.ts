import type { ProductDetail, ProductPrice, ProductField, IncompleteProduct } from '@/types/product'

export const PRODUCT_CATALOGUE: ProductDetail[] = [
  { ean: '3017620422003', name: 'Nutella 400g',            brand: 'Ferrero', photoUrl: null, category: null,               unit: '400g'     },
  { ean: '8076809513401', name: 'Pâtes Barilla Penne',     brand: 'Barilla', photoUrl: null, category: null,               unit: '500g'     },
  { ean: '3760168232003', name: 'Lait demi-écrémé',        brand: null,      photoUrl: null, category: 'Produits laitiers', unit: '1L'       },
  { ean: '3017804030703', name: 'Biscuits petit-déjeuner', brand: null,      photoUrl: null, category: null,               unit: null       },
  { ean: '3229820592292', name: 'Yaourt nature x8',        brand: null,      photoUrl: null, category: 'Produits laitiers', unit: '8×125g'  },
  { ean: '3270160510003', name: 'Poulet fermier',          brand: null,      photoUrl: null, category: 'Viandes',           unit: '1kg'      },
  { ean: '3356542001018', name: 'Tomates cerises',         brand: null,      photoUrl: null, category: 'Fruits & légumes',  unit: '250g'     },
  { ean: '3270160022002', name: 'Café moulu',              brand: null,      photoUrl: null, category: null,               unit: '250g'     },
  { ean: '3095751014543', name: "Jus d'orange",            brand: null,      photoUrl: null, category: null,               unit: '1L'       },
]

// Static demo timestamps — captured at module load, not live
const NOW = Date.now()
const DAY = 24 * 60 * 60 * 1000

export const PRODUCT_PRICES: Record<string, ProductPrice[]> = {
  '3017620422003': [
    { storeName: 'Lidl',      priceCents: 325, updatedAt: NOW - 3 * DAY  },
    { storeName: 'Leclerc',   priceCents: 349, updatedAt: NOW - 14 * DAY },
    { storeName: 'Carrefour', priceCents: 359, updatedAt: NOW - 7 * DAY  },
  ],
  '8076809513401': [
    { storeName: 'Leclerc',   priceCents: 179, updatedAt: NOW - 5 * DAY },
    { storeName: 'Carrefour', priceCents: 185, updatedAt: NOW - 2 * DAY },
  ],
  '3229820592292': [
    { storeName: 'Lidl', priceCents: 199, updatedAt: NOW - 1 * DAY },
  ],
}

const EDITABLE_FIELDS: ProductField[] = ['name', 'brand', 'category', 'unit', 'photoUrl']

export function getRandomIncomplete(): IncompleteProduct {
  const incomplete = PRODUCT_CATALOGUE.filter(p =>
    EDITABLE_FIELDS.some(f => p[f] === null)
  )
  if (incomplete.length === 0) {
    throw new Error('No incomplete products in catalogue')
  }
  const product = incomplete[Math.floor(Math.random() * incomplete.length)]
  const missingFields = EDITABLE_FIELDS.filter(f => product[f] === null)
  return { ...product, missingFields }
}
