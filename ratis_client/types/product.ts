export type ProductField = 'name' | 'brand' | 'photoUrl' | 'category' | 'unit'

export interface ProductDetail {
  ean:      string
  name:     string | null
  brand:    string | null
  photoUrl: string | null
  category: string | null
  unit:     string | null   // ex: "400g", "1L", "1 unité"
}

export interface ProductPrice {
  storeName:  string
  priceCents: number
  updatedAt:  number   // timestamp ms
}

export interface IncompleteProduct extends ProductDetail {
  missingFields: ProductField[]
}
