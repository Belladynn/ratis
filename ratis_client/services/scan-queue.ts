import AsyncStorage from '@react-native-async-storage/async-storage'
import * as TaskManager from 'expo-task-manager'
import * as BackgroundFetch from 'expo-background-fetch'
// Legacy entry — `getInfoAsync` is deprecated on the default expo-file-system
// import (the new API uses File/Directory classes) and now throws at runtime.
// The legacy entry remains supported and is the right path for stat-only use.
import * as FileSystem from 'expo-file-system/legacy'
import * as Sentry from '@sentry/react-native'
import type { ScanItem, UploadQueueEntry } from '@/types/scan'
import { scanEvents, type BatchStoreStatus } from '@/services/scan-events'
import { tokenStorage } from '@/services/token-storage'
import { logger } from '@/services/logger'
import { requireEnv } from '@/services/env'

// ─── Constants ───────────────────────────────────────────────────────────────

export const SCAN_QUEUE_PROCESSOR = 'SCAN_QUEUE_PROCESSOR'
const QUEUE_KEY = 'scan_upload_queue'
const HISTORY_KEY = 'scan_history'
const MAX_ATTEMPTS = 3

// ─── Helpers ─────────────────────────────────────────────────────────────────

export function splitBatches<T>(arr: T[], size: number): T[][] {
  if (arr.length === 0) return []
  const batches: T[][] = []
  for (let i = 0; i < arr.length; i += size) {
    batches.push(arr.slice(i, i + size))
  }
  return batches
}

function uuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

function getApiBase(): string {
  // ratis_product_analyser hosts the scan endpoints — separate service from
  // ratis_auth (EXPO_PUBLIC_API_URL). The env var ALREADY includes the
  // `/api/v1` path prefix (cf eas.json), so callers must NOT re-add it.
  // requireEnv throws loudly on missing/empty — no silent fallback.
  return requireEnv('EXPO_PUBLIC_PRODUCT_API_URL', process.env.EXPO_PUBLIC_PRODUCT_API_URL)
}

// ─── Diagnostic stat helper ──────────────────────────────────────────────────
//
// Emits Sentry breadcrumbs at every checkpoint of the scan upload pipeline so
// we can correlate enqueue-time vs upload-time URI/size/mtime. Investigation
// hook (alpha 2026-04-27): users occasionally see an OLD photo uploaded in
// place of the freshly-taken one. With these breadcrumbs filtered by
// `category:scan.queue.*` on Sentry we can tell whether the stale URI is
// already wrong at enqueue (camera/pipeline issue) or whether the file got
// swapped/deleted between enqueue and upload (cache eviction / re-use of the
// same path by a later capture). Stat is best-effort — never let
// instrumentation break an upload.
type StatSnapshot = {
  exists: boolean
  size?: number
  /** ms epoch — legacy API returns seconds, we convert here for comparability with Date.now(). */
  mtime?: number
}

async function statForBreadcrumb(uri: string): Promise<StatSnapshot> {
  try {
    const info = await FileSystem.getInfoAsync(uri)
    if (info.exists) {
      const mtime =
        typeof info.modificationTime === 'number'
          ? info.modificationTime * 1000
          : undefined
      return { exists: true, size: info.size, mtime }
    }
    return { exists: false }
  } catch {
    return { exists: false }
  }
}

async function getAuthHeaders(): Promise<Record<string, string>> {
  // Source-of-truth for tokens is `tokenStorage` (SecureStore-backed). The
  // previous AsyncStorage('auth_token') lookup was a leftover that never
  // resolved → all scan uploads went out unauthenticated.
  const access = await tokenStorage.getAccess()
  return access ? { Authorization: `Bearer ${access}` } : {}
}

// ─── Queue persistence ───────────────────────────────────────────────────────

async function readQueue(): Promise<UploadQueueEntry[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY)
  return raw ? (JSON.parse(raw) as UploadQueueEntry[]) : []
}

async function writeQueue(entries: UploadQueueEntry[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(entries))
}

async function updateQueueEntry(
  id: string,
  update: Partial<UploadQueueEntry>
): Promise<void> {
  const entries = await readQueue()
  const idx = entries.findIndex(e => e.id === id)
  if (idx === -1) return
  entries[idx] = { ...entries[idx], ...update }
  await writeQueue(entries)
}

// ─── History persistence ─────────────────────────────────────────────────────

export async function getHistory(): Promise<ScanItem[]> {
  const raw = await AsyncStorage.getItem(HISTORY_KEY)
  return raw ? (JSON.parse(raw) as ScanItem[]) : []
}

async function writeHistory(items: ScanItem[]): Promise<void> {
  await AsyncStorage.setItem(HISTORY_KEY, JSON.stringify(items))
}

export async function updateHistoryItem(
  id: string,
  update: Partial<ScanItem>
): Promise<void> {
  const items = await getHistory()
  const idx = items.findIndex(i => i.id === id)
  if (idx === -1) return
  items[idx] = { ...items[idx], ...update }
  await writeHistory(items)
}

/**
 * Purge all locally-persisted scan state — the upload queue AND the history.
 *
 * These AsyncStorage keys are NOT namespaced per user. On a shared device,
 * failing to clear them at logout would leak user A's scans/history to user B
 * (and worse: a still-queued upload would be attributed to user B's account).
 * Call this from the auth signOut / force-logout path.
 */
export async function clearScanStorage(): Promise<void> {
  await AsyncStorage.multiRemove([QUEUE_KEY, HISTORY_KEY])
}

// ─── Enqueue ─────────────────────────────────────────────────────────────────

export async function enqueueReceipt(photoUri: string): Promise<string> {
  const id = uuid()
  const now = Date.now()
  const entry: UploadQueueEntry = {
    id, type: 'receipt', photoUris: [photoUri],
    status: 'queued', createdAt: now, attempt: 0,
    // Generated ONCE here, before the first upload attempt, and persisted
    // with the entry. Every retry of this receipt sends the same key so the
    // backend dedups a replayed upload (app killed after a successful POST
    // but before recording 'done') instead of creating a duplicate receipt.
    idempotencyKey: uuid(),
  }
  const item: ScanItem = { id, type: 'receipt', status: 'uploading', createdAt: now }
  const stat = await statForBreadcrumb(photoUri)
  const breadcrumbData = {
    entry_id: id,
    type: 'receipt',
    photo_uri: photoUri,
    exists: stat.exists,
    size: stat.size,
    mtime: stat.mtime,
  }
  Sentry.addBreadcrumb({
    category: 'scan.queue.enqueue',
    message: 'enqueueReceipt',
    level: 'info',
    data: breadcrumbData,
  })
  if (!stat.exists) {
    Sentry.captureMessage('enqueue.uri_missing_at_enqueue', {
      level: 'warning',
      extra: breadcrumbData,
    })
  }
  const [queue, history] = await Promise.all([readQueue(), getHistory()])
  await Promise.all([
    writeQueue([...queue, entry]),
    writeHistory([item, ...history]),
  ])
  processQueue().catch(() => {}) // fire-and-forget
  return id
}

export async function enqueueLabel(
  photoUri: string,
  lat: number,
  lng: number,
): Promise<string> {
  const id = uuid()
  const now = Date.now()
  const entry: UploadQueueEntry = {
    id, type: 'label', photoUris: [photoUri],
    status: 'queued', createdAt: now, attempt: 0,
    userLat: lat, userLng: lng,
  }
  const item: ScanItem = { id, type: 'label', status: 'uploading', createdAt: now }
  const stat = await statForBreadcrumb(photoUri)
  // userLat/userLng are PII — never include them in breadcrumbs.
  const breadcrumbData = {
    entry_id: id,
    type: 'label',
    photo_uri: photoUri,
    exists: stat.exists,
    size: stat.size,
    mtime: stat.mtime,
  }
  Sentry.addBreadcrumb({
    category: 'scan.queue.enqueue',
    message: 'enqueueLabel',
    level: 'info',
    data: breadcrumbData,
  })
  if (!stat.exists) {
    Sentry.captureMessage('enqueue.uri_missing_at_enqueue', {
      level: 'warning',
      extra: breadcrumbData,
    })
  }
  const [queue, history] = await Promise.all([readQueue(), getHistory()])
  await Promise.all([
    writeQueue([...queue, entry]),
    writeHistory([item, ...history]),
  ])
  processQueue().catch(() => {}) // fire-and-forget
  return id
}

let isProcessing = false

/** @internal Reset processing flag — test use only */
export function _resetProcessingState(): void {
  isProcessing = false
}

/**
 * Boot-time recovery — entries that were `status='uploading'` when the app was
 * killed mid-upload would otherwise stay stuck forever (`processQueue` only
 * picks `status='queued'`). Call this once at app boot to revive them. Idempotent:
 * a no-op when no orphaned entries exist.
 *
 * Diagnosed alpha 2026-04-27 — root cause #1 of "old photo uploaded under a
 * fresh receipt_id" symptom.
 */
export async function resetOrphanedUploads(): Promise<void> {
  const entries = await readQueue()
  let touched = 0
  const next = entries.map(e => {
    if (e.status === 'uploading') {
      touched += 1
      return { ...e, status: 'queued' as const }
    }
    return e
  })
  if (touched === 0) return
  await writeQueue(next)
  logger.info('scan.queue.reset_orphaned_uploads', { count: touched })
}

export async function processQueue(): Promise<void> {
  if (isProcessing) return
  isProcessing = true
  try {
    // Drain to zero — keep looping as long as there is at least one queued
    // entry. Without this, an entry enqueued while a previous pass is in-flight
    // would remain `queued` until the next external trigger (next scan, app
    // restart, AppState 'active'), which is the second root cause of stale
    // uploads diagnosed alpha 2026-04-27.
    //
    // Loop terminates because:
    //  - Each iteration moves every queued entry to 'done', 'uploading' (no-op
    //    in this run, set/cleared inside processReceiptEntry/Batch), 'error'
    //    (after MAX_ATTEMPTS=3), or back to 'queued' with attempt+1 (then
    //    eventually 'error'). Either way, attempt is bounded.
    //  - A failing entry that re-enters status='queued' WILL be retried inside
    //    the same run. That is intentional — the network may have recovered —
    //    but the bound is MAX_ATTEMPTS=3, after which it flips to 'error' and
    //    is no longer picked up.
    //
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const queue = await readQueue()
      const queued = queue
        .filter(e => e.status === 'queued')
        .sort((a, b) => a.createdAt - b.createdAt)
      if (queued.length === 0) break

      // Process receipts one by one
      const receipts = queued.filter(e => e.type === 'receipt')
      for (const entry of receipts) {
        await processReceiptEntry(entry)
      }

      // Batch label entries (≤10 per API call)
      const labels = queued.filter(e => e.type === 'label')
      const batches = splitBatches(labels, 10)
      for (const batch of batches) {
        await processLabelBatch(batch)
      }
    }
  } finally {
    isProcessing = false
  }
}

async function processReceiptEntry(entry: UploadQueueEntry): Promise<void> {
  await updateQueueEntry(entry.id, { status: 'uploading' })
  const url = `${getApiBase()}/scan/receipt`
  let httpStatus: number | undefined
  try {
    const uploadUri = entry.photoUris[0]
    const stat = await statForBreadcrumb(uploadUri)
    const uploadBreadcrumbData = {
      entry_id: entry.id,
      photo_uri: uploadUri,
      exists: stat.exists,
      size: stat.size,
      mtime: stat.mtime,
      ms_since_enqueue: Date.now() - entry.createdAt,
    }
    Sentry.addBreadcrumb({
      category: 'scan.queue.upload',
      message: 'processReceiptEntry.preUpload',
      level: 'info',
      data: uploadBreadcrumbData,
    })
    if (!stat.exists) {
      // This is THE prime suspect for the "old photo uploaded" symptom —
      // raise as `error` so it surfaces immediately on Sentry, not just as
      // a breadcrumb buried in the timeline.
      Sentry.captureMessage('upload.uri_missing_at_upload', {
        level: 'error',
        extra: uploadBreadcrumbData,
      })
    }
    const formData = new FormData()
    formData.append('image', {
      uri: uploadUri,
      type: 'image/jpeg',
      name: 'receipt.jpg',
    } as unknown as Blob)
    // Idempotency key — same value on every retry of this entry so the
    // backend returns the existing receipt instead of creating a duplicate.
    // Guarded for entries enqueued before this field existed (legacy queue).
    if (entry.idempotencyKey) {
      formData.append('idempotency_key', entry.idempotencyKey)
    }
    logger.info('scan.receipt.upload_start', { entry_id: entry.id, url, attempt: entry.attempt })
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      headers: await getAuthHeaders(),
    })
    httpStatus = response.status
    if (!response.ok) {
      const body = await response.text().catch(() => '<unreadable>')
      throw new Error(`HTTP ${response.status} on ${url} — body: ${body.slice(0, 200)}`)
    }
    const data = await response.json() as { receipt_id: string }
    await updateQueueEntry(entry.id, { status: 'done', backendScanId: data.receipt_id })
    await updateHistoryItem(entry.id, { status: 'processing', backendScanId: data.receipt_id })
    logger.info('scan.receipt.upload_done', { entry_id: entry.id, receipt_id: data.receipt_id })
  } catch (err) {
    const attempt = entry.attempt + 1
    // Surface the failure to Sentry — without this, every upload error was
    // silently swallowed (R2 stayed empty + Sentry blind, alpha 2026-04-26).
    logger.error('scan.receipt.upload_error', err, {
      entry_id: entry.id,
      url,
      http_status: httpStatus,
      attempt,
      will_retry: attempt < MAX_ATTEMPTS,
    })
    if (attempt >= MAX_ATTEMPTS) {
      await updateQueueEntry(entry.id, { status: 'error', attempt })
      await updateHistoryItem(entry.id, { status: 'error' })
    } else {
      await updateQueueEntry(entry.id, { status: 'queued', attempt })
    }
  }
}

function hasGeo(e: UploadQueueEntry): boolean {
  return e.userLat !== undefined && e.userLng !== undefined
}

async function processLabelBatch(rawBatch: UploadQueueEntry[]): Promise<void> {
  // A label scan without geo cannot be resolved to a store. Previously a
  // single geo-less entry poisoned the WHOLE batch (every entry, valid ones
  // included, was flagged 'error'). Partition instead: flag only the geo-less
  // entries as error, and upload the rest.
  const geoBatch = rawBatch.filter(hasGeo)
  const geoless = rawBatch.filter(e => !hasGeo(e))
  for (const e of geoless) {
    await updateQueueEntry(e.id, { status: 'error' })
    await updateHistoryItem(e.id, { status: 'error' })
  }
  if (geoBatch.length === 0) return

  const batch = geoBatch
  for (const e of batch) await updateQueueEntry(e.id, { status: 'uploading' })
  const url = `${getApiBase()}/scan/label/batch`
  let httpStatus: number | undefined
  try {
    // Use the geo captured at shutter time for the first entry — the backend
    // accepts a single (user_lat, user_lng) per batch and infers the store.
    // All entries in a batch should be from the same shopping session so the
    // geo is effectively shared; using the first is a safe approximation.
    // `batch` is the geo-filtered subset, so `first` always has geo here.
    const first = batch[0]
    // Stat each label image right before we serialize it into the multipart
    // body so we can correlate enqueue-time stats with the actual file used
    // at upload-time. One breadcrumb per entry — verbose but invaluable when
    // chasing "the wrong photo was uploaded".
    for (const e of batch) {
      const uploadUri = e.photoUris[0]
      const stat = await statForBreadcrumb(uploadUri)
      const uploadBreadcrumbData = {
        entry_id: e.id,
        photo_uri: uploadUri,
        exists: stat.exists,
        size: stat.size,
        mtime: stat.mtime,
        ms_since_enqueue: Date.now() - e.createdAt,
      }
      Sentry.addBreadcrumb({
        category: 'scan.queue.upload',
        message: 'processLabelBatch.preUpload',
        level: 'info',
        data: uploadBreadcrumbData,
      })
      if (!stat.exists) {
        // Same diagnosis as receipt path — raise `error` so it surfaces
        // straight away in Sentry issues, not just buried in breadcrumbs.
        Sentry.captureMessage('upload.uri_missing_at_upload', {
          level: 'error',
          extra: uploadBreadcrumbData,
        })
      }
    }
    const formData = new FormData()
    // first is from `batch` (geo-filtered) — both coords are defined.
    formData.append('user_lat', String(first.userLat as number))
    formData.append('user_lng', String(first.userLng as number))
    formData.append('hint', 'label')
    batch.forEach((e, i) => {
      formData.append('images', {
        uri: e.photoUris[0],
        type: 'image/jpeg',
        name: `label-${i}.jpg`,
      } as unknown as Blob)
    })
    logger.info('scan.label.batch_upload_start', { batch_size: batch.length, url })
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      headers: await getAuthHeaders(),
    })
    httpStatus = response.status
    if (!response.ok) {
      const body = await response.text().catch(() => '<unreadable>')
      throw new Error(`HTTP ${response.status} on ${url} — body: ${body.slice(0, 200)}`)
    }
    const data = (await response.json()) as {
      session_id: string
      scan_ids: string[]
      // store_status is populated since Part A of the "unknown store" feature.
      // Older backends without this field fall back to 'confirmed' so we
      // don't surface the unknown-store modal by mistake.
      store_status?: BatchStoreStatus
    }
    const storeStatus: BatchStoreStatus = data.store_status ?? 'confirmed'
    // Unknown-store scans are persisted but never OCR'd — they are pending
    // reconciliation against a future receipt. Reflect that distinctly in
    // the local history rather than pretending they are 'processing'.
    const newHistoryStatus =
      storeStatus === 'unknown' ? 'unknown_store' : 'processing'

    for (let i = 0; i < batch.length; i++) {
      const scanId = data.scan_ids[i]
      const sessionId = data.session_id
      if (!scanId) {
        // Backend returned fewer scan_ids than expected — mark this entry as error
        await updateQueueEntry(batch[i].id, { status: 'error' })
        await updateHistoryItem(batch[i].id, { status: 'error' })
        continue
      }
      await updateQueueEntry(batch[i].id, { status: 'done', backendScanId: scanId, sessionId })
      await updateHistoryItem(batch[i].id, {
        status: newHistoryStatus,
        backendScanId: scanId,
        sessionId,
      })
    }
    // Notify subscribers (e.g. ScanScreen) so they can surface the
    // "unknown store" modal. Emitted once per batch, not per scan.
    scanEvents.emit({ type: 'batch_uploaded', store_status: storeStatus })
    logger.info('scan.label.batch_upload_done', { batch_size: batch.length, store_status: storeStatus })
  } catch (err) {
    const firstAttempt = batch[0]?.attempt ?? 0
    logger.error('scan.label.batch_upload_error', err, {
      batch_size: batch.length,
      url,
      http_status: httpStatus,
      attempt: firstAttempt + 1,
      will_retry: firstAttempt + 1 < MAX_ATTEMPTS,
    })
    for (const e of batch) {
      const attempt = e.attempt + 1
      if (attempt >= MAX_ATTEMPTS) {
        await updateQueueEntry(e.id, { status: 'error', attempt })
        await updateHistoryItem(e.id, { status: 'error' })
      } else {
        await updateQueueEntry(e.id, { status: 'queued', attempt })
      }
    }
  }
}

export async function pollItem(item: ScanItem): Promise<void> {
  if (!item.backendScanId) return
  const headers = await getAuthHeaders()
  try {
    let response: Response
    if (item.type === 'receipt') {
      response = await fetch(
        `${getApiBase()}/scan/receipt/${item.backendScanId}`,
        { headers }
      )
    } else {
      if (!item.sessionId) return
      response = await fetch(
        `${getApiBase()}/scan/label/session/${item.sessionId}`,
        { headers }
      )
    }
    if (!response.ok) return
    const data = await response.json()
    if (data.status !== 'done') return

    if (item.type === 'receipt') {
      await updateHistoryItem(item.id, {
        status: 'done',
        storeName: data.store_name,
        totalCents: data.total_amount,
        items: (data.items ?? []).map((i: { name: string; qty: number; price: number }) => ({
          name: i.name,
          qty: i.qty,
          priceCents: i.price,
        })),
      })
    } else {
      const scan = (data.scans ?? []).find(
        (s: { scan_id: string }) => s.scan_id === item.backendScanId
      )
      if (!scan) return
      await updateHistoryItem(item.id, {
        status: 'done',
        productName: scan.product_name,
        priceCents: scan.price,
      })
    }
  } catch {
    // Network error during poll — silently skip, will retry next interval
  }
}

// Background task — must be defined at module level
TaskManager.defineTask(SCAN_QUEUE_PROCESSOR, async () => {
  try {
    await processQueue()
    return BackgroundFetch.BackgroundFetchResult.NewData
  } catch {
    return BackgroundFetch.BackgroundFetchResult.Failed
  }
})

export async function registerBackgroundProcessor(): Promise<void> {
  const isRegistered = await TaskManager.isTaskRegisteredAsync(SCAN_QUEUE_PROCESSOR)
  if (!isRegistered) {
    await BackgroundFetch.registerTaskAsync(SCAN_QUEUE_PROCESSOR, {
      minimumInterval: 60,
      stopOnTerminate: false,
      startOnBoot: true,
    })
  }
}
