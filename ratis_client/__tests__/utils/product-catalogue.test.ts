import { getRandomIncomplete, PRODUCT_CATALOGUE } from '@/utils/product-catalogue'

describe('product-catalogue', () => {
  it('getRandomIncomplete returns a product with at least one missing field', () => {
    const result = getRandomIncomplete()
    expect(result.missingFields.length).toBeGreaterThan(0)
  })

  it('getRandomIncomplete ean exists in PRODUCT_CATALOGUE', () => {
    const result = getRandomIncomplete()
    expect(PRODUCT_CATALOGUE.find(p => p.ean === result.ean)).toBeTruthy()
  })

  it('missingFields entries are actually null on the product', () => {
    const result = getRandomIncomplete()
    for (const field of result.missingFields) {
      expect(result[field]).toBeNull()
    }
  })

  // Note: getRandomIncomplete() throws if no incomplete products exist (see implementation guard).
  // Test for this edge case requires module mocking — deferred to integration tests.
})
