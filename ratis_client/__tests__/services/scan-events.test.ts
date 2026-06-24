import { scanEvents, type ScanEvent } from '@/services/scan-events'

describe('scanEvents', () => {
  beforeEach(() => {
    scanEvents._clearAll()
  })

  it('delivers events to subscribers', () => {
    const received: ScanEvent[] = []
    scanEvents.subscribe(e => received.push(e))

    scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' })
    scanEvents.emit({ type: 'batch_uploaded', store_status: 'confirmed' })

    expect(received).toEqual([
      { type: 'batch_uploaded', store_status: 'unknown' },
      { type: 'batch_uploaded', store_status: 'confirmed' },
    ])
  })

  it('delivers to multiple subscribers', () => {
    const a: ScanEvent[] = []
    const b: ScanEvent[] = []
    scanEvents.subscribe(e => a.push(e))
    scanEvents.subscribe(e => b.push(e))

    scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' })

    expect(a).toHaveLength(1)
    expect(b).toHaveLength(1)
  })

  it('unsubscribe stops delivery', () => {
    const received: ScanEvent[] = []
    const unsubscribe = scanEvents.subscribe(e => received.push(e))

    scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' })
    unsubscribe()
    scanEvents.emit({ type: 'batch_uploaded', store_status: 'confirmed' })

    expect(received).toEqual([
      { type: 'batch_uploaded', store_status: 'unknown' },
    ])
  })

  it('a throwing listener does not break sibling listeners', () => {
    const received: ScanEvent[] = []
    scanEvents.subscribe(() => {
      throw new Error('boom')
    })
    scanEvents.subscribe(e => received.push(e))

    scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' })

    expect(received).toHaveLength(1)
  })
})
