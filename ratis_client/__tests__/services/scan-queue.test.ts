import AsyncStorage from '@react-native-async-storage/async-storage'
import * as Sentry from '@sentry/react-native'
import * as FileSystem from 'expo-file-system/legacy'

// scan-queue's `getApiBase()` calls `requireEnv('EXPO_PUBLIC_PRODUCT_API_URL')`
// which throws when the var is missing. Tests mock `global.fetch` and assert
// on the URL, so we must provide a non-empty value at module-load time. Set
// BEFORE importing scan-queue (the import triggers TaskManager.defineTask
// synchronously).
process.env.EXPO_PUBLIC_PRODUCT_API_URL =
  process.env.EXPO_PUBLIC_PRODUCT_API_URL || 'http://test.local/api/v1'

// Must mock expo-task-manager before importing scan-queue
// (scan-queue calls TaskManager.defineTask at module level)
jest.mock('expo-task-manager')
jest.mock('expo-background-fetch')

import {
  splitBatches,
  enqueueReceipt,
  enqueueLabel,
  getHistory,
  updateHistoryItem,
  processQueue,
  pollItem,
  resetOrphanedUploads,
  _resetProcessingState,
} from '@/services/scan-queue'
import type { ScanItem, UploadQueueEntry } from '@/types/scan'

function makeStat(overrides: {
  exists?: boolean
  size?: number
  modificationTime?: number
} = {}) {
  if (overrides.exists === false) {
    return { exists: false, uri: 'file:///x', isDirectory: false }
  }
  return {
    exists: true,
    uri: 'file:///x',
    isDirectory: false,
    size: overrides.size ?? 1234,
    modificationTime: overrides.modificationTime ?? 1_700_000_000,
  }
}

// Shared seed state — allows seedQueue + seedHistory to compose in the same test
const _seed: { queue: string | null; history: string | null } = { queue: null, history: null }

function _applySeed() {
  ;(AsyncStorage.getItem as jest.Mock).mockImplementation((key: string) => {
    if (key === 'scan_upload_queue') return Promise.resolve(_seed.queue)
    if (key === 'scan_history') return Promise.resolve(_seed.history)
    return Promise.resolve(null)
  })
  // Reflect writes back into the seed so subsequent reads observe the
  // mutation. Without this, the while-loop drain inside processQueue would
  // never terminate (the seeded entry stays 'queued' forever after a write).
  ;(AsyncStorage.setItem as jest.Mock).mockImplementation(
    (key: string, value: string) => {
      if (key === 'scan_upload_queue') _seed.queue = value
      if (key === 'scan_history') _seed.history = value
      return Promise.resolve()
    },
  )
}

// Helper: seed the AsyncStorage queue
async function seedQueue(entries: UploadQueueEntry[]) {
  _seed.queue = JSON.stringify(entries)
  _applySeed()
}

async function seedHistory(items: ScanItem[]) {
  _seed.history = JSON.stringify(items)
  _applySeed()
}

beforeEach(() => {
  jest.clearAllMocks()
  _seed.queue = null
  _seed.history = null
  ;(AsyncStorage.getItem as jest.Mock).mockResolvedValue(null)
  ;(AsyncStorage.setItem as jest.Mock).mockResolvedValue(undefined)
})

// ─── splitBatches ────────────────────────────────────────────────────────────

describe('splitBatches', () => {
  it('splits 25 items into batches of 10, 10, 5', () => {
    const arr = Array.from({ length: 25 }, (_, i) => `file-${i}`)
    expect(splitBatches(arr, 10)).toEqual([
      arr.slice(0, 10),
      arr.slice(10, 20),
      arr.slice(20),
    ])
  })

  it('returns single batch when array fits in one batch', () => {
    const arr = ['a', 'b', 'c']
    expect(splitBatches(arr, 10)).toEqual([['a', 'b', 'c']])
  })

  it('returns empty array for empty input', () => {
    expect(splitBatches([], 10)).toEqual([])
  })
})

// ─── enqueueReceipt ──────────────────────────────────────────────────────────

describe('enqueueReceipt', () => {
  it('writes one queue entry and one ScanItem to AsyncStorage', async () => {
    const id = await enqueueReceipt('file:///receipt.jpg')

    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls.find(
      ([key]: [string]) => key === 'scan_upload_queue'
    )
    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls.find(
      ([key]: [string]) => key === 'scan_history'
    )

    expect(queueCall).toBeTruthy()
    const queue: UploadQueueEntry[] = JSON.parse(queueCall[1])
    expect(queue).toHaveLength(1)
    expect(queue[0]).toMatchObject({
      id,
      type: 'receipt',
      photoUris: ['file:///receipt.jpg'],
      status: 'queued',
      attempt: 0,
    })

    expect(historyCall).toBeTruthy()
    const history: ScanItem[] = JSON.parse(historyCall[1])
    expect(history).toHaveLength(1)
    expect(history[0]).toMatchObject({ id, type: 'receipt', status: 'uploading' })
  })

  it('returns a non-empty string id', async () => {
    const id = await enqueueReceipt('file:///receipt.jpg')
    expect(typeof id).toBe('string')
    expect(id.length).toBeGreaterThan(0)
  })

  it('persists a UUID idempotencyKey distinct from the entry id', async () => {
    await enqueueReceipt('file:///receipt.jpg')
    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls.find(
      ([key]: [string]) => key === 'scan_upload_queue'
    )
    const queue: UploadQueueEntry[] = JSON.parse(queueCall![1])
    // Stable key generated at enqueue time — reused on every upload retry so
    // the backend dedups a replayed upload instead of creating a duplicate.
    expect(queue[0].idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
    )
    expect(queue[0].idempotencyKey).not.toBe(queue[0].id)
  })
})

// ─── enqueueLabel ────────────────────────────────────────────────────────────

describe('enqueueLabel', () => {
  it('persists the user geo captured at shutter time', async () => {
    const id = await enqueueLabel('file:///label.jpg', 48.8566, 2.3522)

    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls.find(
      ([key]: [string]) => key === 'scan_upload_queue'
    )
    const queue: UploadQueueEntry[] = JSON.parse(queueCall![1])
    expect(queue[0]).toMatchObject({
      id,
      type: 'label',
      photoUris: ['file:///label.jpg'],
      status: 'queued',
      userLat: 48.8566,
      userLng: 2.3522,
    })
  })
})

// ─── getHistory ──────────────────────────────────────────────────────────────

describe('getHistory', () => {
  it('returns empty array when AsyncStorage has no history', async () => {
    const history = await getHistory()
    expect(history).toEqual([])
  })

  it('returns parsed ScanItem array when history exists', async () => {
    const items: ScanItem[] = [
      { id: '1', type: 'receipt', status: 'done', createdAt: 1000 },
    ]
    await seedHistory(items)
    const history = await getHistory()
    expect(history).toEqual(items)
  })
})

// ─── updateHistoryItem ───────────────────────────────────────────────────────

describe('updateHistoryItem', () => {
  it('updates the matching item in history', async () => {
    const items: ScanItem[] = [
      { id: 'abc', type: 'receipt', status: 'processing', createdAt: 1000 },
      { id: 'xyz', type: 'label', status: 'uploading', createdAt: 2000 },
    ]
    await seedHistory(items)

    await updateHistoryItem('abc', { status: 'done', storeName: 'Lidl' })

    const call = (AsyncStorage.setItem as jest.Mock).mock.calls.find(
      ([key]: [string]) => key === 'scan_history'
    )
    const saved: ScanItem[] = JSON.parse(call![1])
    expect(saved.find(i => i.id === 'abc')).toMatchObject({
      id: 'abc',
      status: 'done',
      storeName: 'Lidl',
    })
    // other item unchanged
    expect(saved.find(i => i.id === 'xyz')).toMatchObject({ status: 'uploading' })
  })

  it('does nothing if id not found', async () => {
    await seedHistory([])
    await updateHistoryItem('missing', { status: 'done' })
    // setItem may or may not be called — no throw expected
  })
})

// ─── processQueue ────────────────────────────────────────────────────────────

describe('processQueue', () => {
  beforeEach(() => {
    global.fetch = jest.fn()
    _resetProcessingState()
  })

  it('POSTs receipt to /api/v1/scan/receipt and updates status to processing', async () => {
    const entry: UploadQueueEntry = {
      id: 'r1', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
    }
    const item: ScanItem = { id: 'r1', type: 'receipt', status: 'uploading', createdAt: 1000 }
    await seedQueue([entry])
    await seedHistory([item])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r1' }),
    })

    await processQueue()

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/scan/receipt'),
      expect.objectContaining({ method: 'POST' })
    )
    // ScanItem updated to 'processing'
    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({ id: 'r1', status: 'processing', backendScanId: 'backend-r1' })
  })

  it('sends the entry idempotencyKey in the receipt upload body', async () => {
    const entry: UploadQueueEntry = {
      id: 'r_idem', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
      idempotencyKey: 'key-abc-123',
    }
    await seedQueue([entry])
    await seedHistory([{ id: 'r_idem', type: 'receipt', status: 'uploading', createdAt: 1000 }])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r_idem' }),
    })

    await processQueue()

    const body = (global.fetch as jest.Mock).mock.calls[0][1].body as FormData
    expect(body.get('idempotency_key')).toBe('key-abc-123')
  })

  it('reuses the SAME idempotencyKey across every retry attempt', async () => {
    // App-killed-after-POST scenario : the upload is retried up to
    // MAX_ATTEMPTS=3. Every attempt must carry the IDENTICAL key so the
    // backend recognises the replay and returns the existing receipt.
    const entry: UploadQueueEntry = {
      id: 'r_retry', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
      idempotencyKey: 'stable-key-xyz',
    }
    await seedQueue([entry])
    await seedHistory([{ id: 'r_retry', type: 'receipt', status: 'uploading', createdAt: 1000 }])

    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network'))

    await processQueue()

    const calls = (global.fetch as jest.Mock).mock.calls
    expect(calls).toHaveLength(3) // MAX_ATTEMPTS
    const keys = calls.map(([, init]) =>
      (init.body as FormData).get('idempotency_key')
    )
    expect(keys).toEqual(['stable-key-xyz', 'stable-key-xyz', 'stable-key-xyz'])
  })

  it('omits idempotency_key for legacy entries enqueued before the field existed', async () => {
    const entry: UploadQueueEntry = {
      id: 'r_legacy', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
    }
    await seedQueue([entry])
    await seedHistory([{ id: 'r_legacy', type: 'receipt', status: 'uploading', createdAt: 1000 }])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r_legacy' }),
    })

    await processQueue()

    const body = (global.fetch as jest.Mock).mock.calls[0][1].body as FormData
    expect(body.get('idempotency_key')).toBeNull()
  })

  it('batches label entries 10 at a time and forwards the geo', async () => {
    const entries: UploadQueueEntry[] = Array.from({ length: 12 }, (_, i) => ({
      id: `l${i}`, type: 'label' as const, photoUris: [`file:///l${i}.jpg`],
      status: 'queued' as const, createdAt: 1000 + i, attempt: 0,
      userLat: 48.8566, userLng: 2.3522,
    }))
    const items: ScanItem[] = entries.map(e => ({
      id: e.id, type: 'label' as const, status: 'uploading' as const, createdAt: e.createdAt,
    }))
    await seedQueue(entries)
    await seedHistory(items)

    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: 'sess1',
        scan_ids: Array.from({ length: 10 }, (_, i) => `sid${i}`),
      }),
    })

    await processQueue()

    // 2 batch POST calls (10 + 2)
    const postCalls = (global.fetch as jest.Mock).mock.calls.filter(
      ([url]: [string]) => url.includes('/scan/label/batch')
    )
    expect(postCalls).toHaveLength(2)

    // Hits the product_analyser base URL (EXPO_PUBLIC_PRODUCT_API_URL), not auth.
    expect(postCalls[0][0]).toContain('/api/v1/scan/label/batch')
  })

  it('marks all entries as error when a label batch has no geo', async () => {
    const entries: UploadQueueEntry[] = [
      {
        id: 'l_nogeo', type: 'label', photoUris: ['file:///l.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0,
      },
    ]
    const items: ScanItem[] = [
      { id: 'l_nogeo', type: 'label', status: 'uploading', createdAt: 1000 },
    ]
    await seedQueue(entries)
    await seedHistory(items)

    await processQueue()

    // No network call — rejected before fetch.
    expect(global.fetch).not.toHaveBeenCalled()
    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({ id: 'l_nogeo', status: 'error' })
  })

  it('retries on network failure and flips to error after MAX_ATTEMPTS=3', async () => {
    // After PR #fix-scan-queue-drain : processQueue drains in the SAME run
    // (while-loop). A persistently-failing entry no longer ends a run as
    // status='queued, attempt=1' — it is retried in-place up to MAX_ATTEMPTS=3
    // and flips to 'error'. This is intentional (see SESSION_LOG.md
    // 2026-04-27 entry, root cause #2). The exact retry-budget contract
    // (MAX_ATTEMPTS=3) is locked down by the dedicated test below.
    const entry: UploadQueueEntry = {
      id: 'r2', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
    }
    await seedQueue([entry])
    await seedHistory([{ id: 'r2', type: 'receipt', status: 'uploading', createdAt: 1000 }])

    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network'))

    await processQueue()

    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_upload_queue')
      .at(-1)
    const saved: UploadQueueEntry[] = JSON.parse(queueCall![1])
    expect(saved[0]).toMatchObject({ id: 'r2', status: 'error', attempt: 3 })
    expect((global.fetch as jest.Mock).mock.calls).toHaveLength(3)
  })

  it('marks error after 3 failed attempts', async () => {
    const entry: UploadQueueEntry = {
      id: 'r3', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 2, // already at max-1
    }
    await seedQueue([entry])
    await seedHistory([{ id: 'r3', type: 'receipt', status: 'uploading', createdAt: 1000 }])

    ;(global.fetch as jest.Mock).mockRejectedValueOnce(new Error('network'))

    await processQueue()

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({ id: 'r3', status: 'error' })
  })

  it("flags history as 'unknown_store' and emits when backend returns store_status='unknown'", async () => {
    // Load the real events module so we can subscribe to emissions
    const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events')
    scanEvents._clearAll()
    const events: unknown[] = []
    const unsubscribe = scanEvents.subscribe(e => events.push(e))

    const entries: UploadQueueEntry[] = [
      {
        id: 'l_unknown', type: 'label', photoUris: ['file:///l.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0,
        userLat: 0.0, userLng: -30.0,
      },
    ]
    await seedQueue(entries)
    await seedHistory([
      { id: 'l_unknown', type: 'label', status: 'uploading', createdAt: 1000 },
    ])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: 'sess_u',
        scan_ids: ['sid_u'],
        store_status: 'unknown',
      }),
    })

    await processQueue()
    unsubscribe()

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({
      id: 'l_unknown',
      status: 'unknown_store',
      backendScanId: 'sid_u',
      sessionId: 'sess_u',
    })
    expect(events).toEqual([
      { type: 'batch_uploaded', store_status: 'unknown' },
    ])
  })

  it("emits 'confirmed' when backend returns store_status='confirmed'", async () => {
    const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events')
    scanEvents._clearAll()
    const events: unknown[] = []
    const unsubscribe = scanEvents.subscribe(e => events.push(e))

    const entries: UploadQueueEntry[] = [
      {
        id: 'l_ok', type: 'label', photoUris: ['file:///l.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0,
        userLat: 48.8566, userLng: 2.3522,
      },
    ]
    await seedQueue(entries)
    await seedHistory([
      { id: 'l_ok', type: 'label', status: 'uploading', createdAt: 1000 },
    ])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: 'sess_c',
        scan_ids: ['sid_c'],
        store_status: 'confirmed',
      }),
    })

    await processQueue()
    unsubscribe()

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({ id: 'l_ok', status: 'processing' })
    expect(events).toEqual([
      { type: 'batch_uploaded', store_status: 'confirmed' },
    ])
  })
})

// ─── pollItem ─────────────────────────────────────────────────────────────────

describe('pollItem', () => {
  beforeEach(() => {
    global.fetch = jest.fn()
  })

  it('updates receipt ScanItem to done with store data', async () => {
    const item: ScanItem = {
      id: 'r1', type: 'receipt', status: 'processing',
      createdAt: 1000, backendScanId: 'backend-r1',
    }
    await seedHistory([item])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: 'done',
        store_name: 'Lidl',
        total_amount: 1255,
        items: [{ name: 'Yaourt', qty: 2, price: 178 }],
      }),
    })

    await pollItem(item)

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({
      status: 'done',
      storeName: 'Lidl',
      totalCents: 1255,
      items: [{ name: 'Yaourt', qty: 2, priceCents: 178 }],
    })
  })

  it('updates label ScanItem to done with product data', async () => {
    const item: ScanItem = {
      id: 'l1', type: 'label', status: 'processing',
      createdAt: 1000, backendScanId: 'scan-id-1', sessionId: 'sess1',
    }
    await seedHistory([item])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: 'done',
        scans: [{ scan_id: 'scan-id-1', product_name: 'Nutella 400g', price: 325 }],
      }),
    })

    await pollItem(item)

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    expect(saved[0]).toMatchObject({
      status: 'done',
      productName: 'Nutella 400g',
      priceCents: 325,
    })
  })

  it('does nothing when backend returns non-done status', async () => {
    const item: ScanItem = {
      id: 'r1', type: 'receipt', status: 'processing',
      createdAt: 1000, backendScanId: 'backend-r1',
    }
    await seedHistory([item])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'processing' }),
    })

    await pollItem(item)

    // No setItem call for history expected (status unchanged)
    const historySetCalls = (AsyncStorage.setItem as jest.Mock).mock.calls.filter(
      ([key]: [string]) => key === 'scan_history'
    )
    expect(historySetCalls).toHaveLength(0)
  })
})

// ─── resetOrphanedUploads (boot-time reset) ──────────────────────────────────

describe('resetOrphanedUploads', () => {
  it("rewrites entries with status='uploading' back to 'queued'", async () => {
    const entries: UploadQueueEntry[] = [
      {
        id: 'orphan', type: 'receipt', photoUris: ['file:///a.jpg'],
        status: 'uploading', createdAt: 1000, attempt: 1,
      },
    ]
    await seedQueue(entries)

    await resetOrphanedUploads()

    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_upload_queue')
      .at(-1)
    expect(queueCall).toBeTruthy()
    const saved: UploadQueueEntry[] = JSON.parse(queueCall![1])
    expect(saved[0]).toMatchObject({ id: 'orphan', status: 'queued', attempt: 1 })
  })

  it("does not modify entries with status='queued', 'done' or 'error'", async () => {
    const entries: UploadQueueEntry[] = [
      {
        id: 'q', type: 'receipt', photoUris: ['file:///q.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0,
      },
      {
        id: 'd', type: 'receipt', photoUris: ['file:///d.jpg'],
        status: 'done', createdAt: 1001, attempt: 0,
      },
      {
        id: 'e', type: 'receipt', photoUris: ['file:///e.jpg'],
        status: 'error', createdAt: 1002, attempt: 3,
      },
    ]
    await seedQueue(entries)

    await resetOrphanedUploads()

    const queueCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_upload_queue')
      .at(-1)
    // Either we rewrote with identical contents, or did not write at all —
    // either way, the resulting state must preserve all three statuses.
    if (queueCall) {
      const saved: UploadQueueEntry[] = JSON.parse(queueCall[1])
      expect(saved.find(e => e.id === 'q')).toMatchObject({ status: 'queued' })
      expect(saved.find(e => e.id === 'd')).toMatchObject({ status: 'done' })
      expect(saved.find(e => e.id === 'e')).toMatchObject({ status: 'error' })
    }
  })

  it('is a no-op when the queue is empty', async () => {
    await seedQueue([])

    await resetOrphanedUploads()

    // No throw — that's the contract.
    expect(true).toBe(true)
  })
})

// ─── processQueue cascade re-trigger (drain to zero) ─────────────────────────

describe('processQueue cascade drain', () => {
  beforeEach(() => {
    global.fetch = jest.fn()
    _resetProcessingState()
  })

  it('picks up a new entry that was added during the first pass (same run)', async () => {
    // First pass sees one queued entry. While it is being processed we inject
    // a second entry directly into AsyncStorage. The cascade re-trigger inside
    // processQueue must pick it up in the SAME run, without an external call.
    const initial: UploadQueueEntry[] = [
      {
        id: 'first', type: 'receipt', photoUris: ['file:///first.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0,
      },
    ]
    const second: UploadQueueEntry = {
      id: 'second', type: 'receipt', photoUris: ['file:///second.jpg'],
      status: 'queued', createdAt: 2000, attempt: 0,
    }

    // Track every state of the queue keyed by call order. The mock for
    // setItem captures writes; getItem returns whatever was last written.
    let queueState = JSON.stringify(initial)
    let historyState = JSON.stringify([
      { id: 'first', type: 'receipt', status: 'uploading', createdAt: 1000 },
    ])
    ;(AsyncStorage.getItem as jest.Mock).mockImplementation((key: string) => {
      if (key === 'scan_upload_queue') return Promise.resolve(queueState)
      if (key === 'scan_history') return Promise.resolve(historyState)
      return Promise.resolve(null)
    })
    ;(AsyncStorage.setItem as jest.Mock).mockImplementation(
      (key: string, value: string) => {
        if (key === 'scan_upload_queue') queueState = value
        if (key === 'scan_history') historyState = value
        return Promise.resolve()
      },
    )

    let firstResolved = false
    ;(global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      if (!firstResolved) {
        // After the first fetch resolves we will inject a new queued entry —
        // simulating the user enqueuing a fresh scan while a pass is in-flight.
        firstResolved = true
        const current: UploadQueueEntry[] = JSON.parse(queueState)
        queueState = JSON.stringify([...current, second])
        const hist: ScanItem[] = JSON.parse(historyState)
        historyState = JSON.stringify([
          ...hist,
          { id: 'second', type: 'receipt', status: 'uploading', createdAt: 2000 },
        ])
        return {
          ok: true,
          json: async () => ({ receipt_id: `backend-first` }),
        }
      }
      return {
        ok: true,
        json: async () => ({ receipt_id: `backend-second` }),
      }
    })

    await processQueue()

    // Both entries must have been POSTed during the same run.
    const receiptCalls = (global.fetch as jest.Mock).mock.calls.filter(
      ([url]: [string]) => url.includes('/scan/receipt'),
    )
    expect(receiptCalls).toHaveLength(2)

    // Final queue state: both entries done.
    const saved: UploadQueueEntry[] = JSON.parse(queueState)
    expect(saved.find(e => e.id === 'first')).toMatchObject({ status: 'done' })
    expect(saved.find(e => e.id === 'second')).toMatchObject({ status: 'done' })
  })

  it('terminates when no more queued entries remain (no infinite loop)', async () => {
    const entry: UploadQueueEntry = {
      id: 'r-term', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 0,
    }
    await seedQueue([entry])
    await seedHistory([
      { id: 'r-term', type: 'receipt', status: 'uploading', createdAt: 1000 },
    ])

    ;(global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r-term' }),
    })

    // Wrap in a timeout assertion — the ultimate proof that we don't loop.
    await expect(
      Promise.race([
        processQueue(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('processQueue did not terminate')), 2000),
        ),
      ]),
    ).resolves.toBeUndefined()

    // Exactly one fetch — once 'first' is done the seed mock keeps returning
    // it as 'queued' on subsequent reads (since seedQueue doesn't track writes)
    // but the entry is now status='done' in the persisted state. So the second
    // pass sees zero queued entries and the while loop exits cleanly.
    // We only assert termination here, not call-count, because seed semantics
    // are a test-fixture concern.
  })

  it('respects MAX_ATTEMPTS=3 even with the while loop in place', async () => {
    // Single entry that has already failed twice — third failure must mark
    // 'error' and the loop must NOT keep retrying it forever.
    const entry: UploadQueueEntry = {
      id: 'r-max', type: 'receipt', photoUris: ['file:///r.jpg'],
      status: 'queued', createdAt: 1000, attempt: 2,
    }
    let queueState = JSON.stringify([entry])
    let historyState = JSON.stringify([
      { id: 'r-max', type: 'receipt', status: 'uploading', createdAt: 1000 },
    ])
    ;(AsyncStorage.getItem as jest.Mock).mockImplementation((key: string) => {
      if (key === 'scan_upload_queue') return Promise.resolve(queueState)
      if (key === 'scan_history') return Promise.resolve(historyState)
      return Promise.resolve(null)
    })
    ;(AsyncStorage.setItem as jest.Mock).mockImplementation(
      (key: string, value: string) => {
        if (key === 'scan_upload_queue') queueState = value
        if (key === 'scan_history') historyState = value
        return Promise.resolve()
      },
    )

    ;(global.fetch as jest.Mock).mockRejectedValue(new Error('network'))

    await expect(
      Promise.race([
        processQueue(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('processQueue stuck on MAX_ATTEMPTS')), 2000),
        ),
      ]),
    ).resolves.toBeUndefined()

    // Exactly one fetch attempt — the entry was at attempt=2 so its third
    // failure flips it to 'error', and the while loop has no more queued
    // entries to retry.
    expect((global.fetch as jest.Mock).mock.calls).toHaveLength(1)
    const saved: UploadQueueEntry[] = JSON.parse(queueState)
    expect(saved[0]).toMatchObject({ id: 'r-max', status: 'error', attempt: 3 })
  })
})

// ─── diagnostic breadcrumbs (alpha 2026-04-27 investigation) ─────────────────

describe('diagnostic breadcrumbs', () => {
  beforeEach(() => {
    global.fetch = jest.fn()
    _resetProcessingState()
    ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValue(makeStat())
  })

  it('enqueueReceipt emits a scan.queue.enqueue breadcrumb with stat metadata', async () => {
    ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValueOnce(
      makeStat({ size: 222_222, modificationTime: 1_700_000_001 }),
    )

    const id = await enqueueReceipt('file:///fresh-receipt.jpg')

    const enqueueCrumbs = (Sentry.addBreadcrumb as jest.Mock).mock.calls.filter(
      ([c]: [{ category: string }]) => c.category === 'scan.queue.enqueue',
    )
    expect(enqueueCrumbs).toHaveLength(1)
    expect(enqueueCrumbs[0][0]).toMatchObject({
      category: 'scan.queue.enqueue',
      level: 'info',
      data: {
        entry_id: id,
        type: 'receipt',
        photo_uri: 'file:///fresh-receipt.jpg',
        exists: true,
        size: 222_222,
        mtime: 1_700_000_001_000,
      },
    })
  })

  it('enqueueReceipt captures uri_missing_at_enqueue (warning) when file does not exist', async () => {
    ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValueOnce(
      makeStat({ exists: false }),
    )
    const id = await enqueueReceipt('file:///gone.jpg')

    expect(Sentry.captureMessage).toHaveBeenCalledTimes(1)
    const [msg, opts] = (Sentry.captureMessage as jest.Mock).mock.calls[0]
    expect(msg).toBe('enqueue.uri_missing_at_enqueue')
    expect(opts).toMatchObject({ level: 'warning' })
    expect(opts.extra).toMatchObject({
      entry_id: id,
      photo_uri: 'file:///gone.jpg',
      exists: false,
    })
  })

  it('enqueueLabel emits a scan.queue.enqueue breadcrumb without leaking lat/lng', async () => {
    const id = await enqueueLabel('file:///fresh-label.jpg', 48.8566, 2.3522)

    const enqueueCrumbs = (Sentry.addBreadcrumb as jest.Mock).mock.calls.filter(
      ([c]: [{ category: string }]) => c.category === 'scan.queue.enqueue',
    )
    expect(enqueueCrumbs).toHaveLength(1)
    const data = enqueueCrumbs[0][0].data as Record<string, unknown>
    expect(data).toMatchObject({
      entry_id: id,
      type: 'label',
      photo_uri: 'file:///fresh-label.jpg',
      exists: true,
    })
    // Hard guarantee — userLat/userLng are PII and must NEVER appear in
    // breadcrumb payloads (R30 / RGPD).
    expect(data).not.toHaveProperty('userLat')
    expect(data).not.toHaveProperty('userLng')
    expect(JSON.stringify(data)).not.toMatch(/48\.8566|2\.3522/)
  })

  it('processReceiptEntry emits a scan.queue.upload breadcrumb with ms_since_enqueue right before fetch', async () => {
    const createdAt = Date.now() - 5000
    const entry: UploadQueueEntry = {
      id: 'r-up', type: 'receipt', photoUris: ['file:///rcpt-up.jpg'],
      status: 'queued', createdAt, attempt: 0,
    }
    await seedQueue([entry])
    await seedHistory([
      { id: 'r-up', type: 'receipt', status: 'uploading', createdAt },
    ])
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r-up' }),
    })

    await processQueue()

    const uploadCrumbs = (Sentry.addBreadcrumb as jest.Mock).mock.calls.filter(
      ([c]: [{ category: string }]) => c.category === 'scan.queue.upload',
    )
    expect(uploadCrumbs).toHaveLength(1)
    const crumb = uploadCrumbs[0][0]
    expect(crumb).toMatchObject({
      category: 'scan.queue.upload',
      level: 'info',
      data: expect.objectContaining({
        entry_id: 'r-up',
        photo_uri: 'file:///rcpt-up.jpg',
        exists: true,
      }),
    })
    expect(typeof crumb.data.ms_since_enqueue).toBe('number')
    expect(crumb.data.ms_since_enqueue).toBeGreaterThanOrEqual(5000)
  })

  it('processReceiptEntry captures upload.uri_missing_at_upload (error level) when the file is gone', async () => {
    const entry: UploadQueueEntry = {
      id: 'r-gone', type: 'receipt', photoUris: ['file:///rcpt-gone.jpg'],
      status: 'queued', createdAt: Date.now(), attempt: 0,
    }
    await seedQueue([entry])
    await seedHistory([
      { id: 'r-gone', type: 'receipt', status: 'uploading', createdAt: Date.now() },
    ])
    // First call (in receipt path) reports missing; later calls default to exists.
    ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValueOnce(
      makeStat({ exists: false }),
    )
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ receipt_id: 'backend-r-gone' }),
    })

    await processQueue()

    const calls = (Sentry.captureMessage as jest.Mock).mock.calls
    const missing = calls.find(([m]: [string]) => m === 'upload.uri_missing_at_upload')
    expect(missing).toBeTruthy()
    expect(missing![1]).toMatchObject({ level: 'error' })
    expect(missing![1].extra).toMatchObject({
      entry_id: 'r-gone',
      photo_uri: 'file:///rcpt-gone.jpg',
      exists: false,
    })
  })

  it('processLabelBatch emits one scan.queue.upload breadcrumb per entry', async () => {
    const entries: UploadQueueEntry[] = Array.from({ length: 3 }, (_, i) => ({
      id: `lup-${i}`,
      type: 'label' as const,
      photoUris: [`file:///label-${i}.jpg`],
      status: 'queued' as const,
      createdAt: Date.now() - 2000,
      attempt: 0,
      userLat: 48.8566,
      userLng: 2.3522,
    }))
    await seedQueue(entries)
    await seedHistory(entries.map(e => ({
      id: e.id, type: 'label' as const, status: 'uploading' as const, createdAt: e.createdAt,
    })))
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: 'sess1',
        scan_ids: ['s0', 's1', 's2'],
      }),
    })

    await processQueue()

    const uploadCrumbs = (Sentry.addBreadcrumb as jest.Mock).mock.calls.filter(
      ([c]: [{ category: string }]) => c.category === 'scan.queue.upload',
    )
    expect(uploadCrumbs).toHaveLength(3)
    const ids = uploadCrumbs.map(([c]) => (c.data as { entry_id: string }).entry_id)
    expect(ids).toEqual(['lup-0', 'lup-1', 'lup-2'])
    // No PII leak in the per-entry crumbs.
    for (const [c] of uploadCrumbs) {
      expect(JSON.stringify(c.data)).not.toMatch(/48\.8566|2\.3522/)
    }
  })
})

// ─── clearScanStorage ────────────────────────────────────────────────────────

describe('clearScanStorage', () => {
  it('removes both the upload queue and history keys', async () => {
    const { clearScanStorage } = require('@/services/scan-queue') as typeof import('@/services/scan-queue')
    await clearScanStorage()
    expect(AsyncStorage.multiRemove).toHaveBeenCalledWith([
      'scan_upload_queue',
      'scan_history',
    ])
  })

  it('leaves no scans readable afterwards', async () => {
    await seedQueue([
      { id: 'q1', type: 'receipt', photoUris: ['file:///r.jpg'],
        status: 'queued', createdAt: 1, attempt: 0 },
    ])
    await seedHistory([
      { id: 'q1', type: 'receipt', status: 'uploading', createdAt: 1 },
    ])
    const { clearScanStorage } = require('@/services/scan-queue') as typeof import('@/services/scan-queue')
    // Wire the seed-backed mock so multiRemove actually clears the seed.
    ;(AsyncStorage.multiRemove as jest.Mock).mockImplementation((keys: string[]) => {
      if (keys.includes('scan_upload_queue')) _seed.queue = null
      if (keys.includes('scan_history')) _seed.history = null
      return Promise.resolve()
    })
    await clearScanStorage()
    await expect(getHistory()).resolves.toEqual([])
  })
})

// ─── processLabelBatch — partial geo ─────────────────────────────────────────

describe('processLabelBatch — partial geo', () => {
  it('marks only geo-less entries as error, uploads the valid ones', async () => {
    global.fetch = jest.fn()
    _resetProcessingState()
    const entries: UploadQueueEntry[] = [
      // No geo — must be flagged 'error' individually.
      { id: 'l_nogeo', type: 'label', photoUris: ['file:///a.jpg'],
        status: 'queued', createdAt: 1000, attempt: 0 },
      // Valid geo — must still be uploaded.
      { id: 'l_geo', type: 'label', photoUris: ['file:///b.jpg'],
        status: 'queued', createdAt: 1001, attempt: 0,
        userLat: 48.8566, userLng: 2.3522 },
    ]
    await seedQueue(entries)
    await seedHistory([
      { id: 'l_nogeo', type: 'label', status: 'uploading', createdAt: 1000 },
      { id: 'l_geo', type: 'label', status: 'uploading', createdAt: 1001 },
    ])

    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: 'sess_x',
        scan_ids: ['sid_x'],
        store_status: 'confirmed',
      }),
    })

    await processQueue()

    const historyCall = (AsyncStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'scan_history')
      .at(-1)
    const saved: ScanItem[] = JSON.parse(historyCall![1])
    const byId = Object.fromEntries(saved.map(s => [s.id, s]))
    expect(byId['l_nogeo'].status).toBe('error')
    expect(byId['l_geo'].status).toBe('processing')
  })
})
