// ratis_client/hooks/use-contribute-field.ts
//
// Mutation wrapper for POST /product/{ean}/contribute. Used by the
// « Compléter ce produit » screen after the user submits a value
// for a missing field. On success, invalidates the 3 queryKeys
// downstream so the dashboard card (enrichissement), the completer
// batch (incomplete-products), and the CAB balance (cab-balance)
// all reflect the contribution.

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { productClient } from '@/services/product-client';

export type ContributeField =
  | 'name'
  | 'brands'
  | 'categories_tags'
  | 'labels_tags';

export interface ContributeParams {
  ean: string;
  field: ContributeField;
  value: string | string[];
}

export function useContributeField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ean, field, value }: ContributeParams) =>
      productClient.post(`/product/${ean}/contribute`, { field, value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['enrichissement'] });
      qc.invalidateQueries({ queryKey: ['incomplete-products'] });
      qc.invalidateQueries({ queryKey: ['cab-balance'] });
    },
  });
}
