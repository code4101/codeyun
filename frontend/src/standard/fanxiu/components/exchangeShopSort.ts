export type ExchangeShopDefaultSort = {
  key: 'source_order' | 'priority_order'
  direction: 'asc'
}

type ExchangeShopPriorityItem = {
  priority_order?: number | null
}

export function defaultExchangeShopSort(
  items: readonly ExchangeShopPriorityItem[] | null | undefined,
): ExchangeShopDefaultSort {
  return {
    key: items?.some(item => item.priority_order != null) ? 'priority_order' : 'source_order',
    direction: 'asc',
  }
}
