// Tests for the diagnostic instrumentation added to flattenAndResize.
// We don't test the resize math — expo-image-manipulator is already mocked
// globally to passthrough. We do test that the Sentry breadcrumb is emitted
// with the expected shape, and that a missing file triggers a captureMessage.

import * as Sentry from '@sentry/react-native'
import * as FileSystem from 'expo-file-system/legacy'
import { flattenAndResize } from '@/services/image-pipeline'

beforeEach(() => {
  jest.clearAllMocks()
  // Default — file exists.
  ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValue({
    exists: true,
    uri: 'file:///cache/x.jpg',
    size: 250_000,
    isDirectory: false,
    modificationTime: 1_700_000_500,
  })
})

describe('flattenAndResize — diagnostic breadcrumbs', () => {
  it('emits a scan.pipeline breadcrumb with stat metadata after manipulateAsync', async () => {
    const result = await flattenAndResize('file:///input.jpg')

    // Passthrough — image-manipulator mock returns the input URI.
    expect(result).toBe('file:///input.jpg')
    expect(FileSystem.getInfoAsync).toHaveBeenCalledWith('file:///input.jpg')

    expect(Sentry.addBreadcrumb).toHaveBeenCalledTimes(1)
    const arg = (Sentry.addBreadcrumb as jest.Mock).mock.calls[0][0]
    expect(arg).toMatchObject({
      category: 'scan.pipeline',
      level: 'info',
    })
    expect(arg.data).toMatchObject({
      uri: 'file:///input.jpg',
      input_uri: 'file:///input.jpg',
      exists: true,
      size: 250_000,
      // Converted from seconds → ms epoch.
      modificationTime: 1_700_000_500_000,
    })
    expect(Sentry.captureMessage).not.toHaveBeenCalled()
  })

  it('captures a flatten.uri_missing warning when the manipulated file does not exist', async () => {
    ;(FileSystem.getInfoAsync as jest.Mock).mockResolvedValueOnce({
      exists: false,
      uri: 'file:///input.jpg',
      isDirectory: false,
    })

    await flattenAndResize('file:///input.jpg')

    expect(Sentry.captureMessage).toHaveBeenCalledTimes(1)
    const [msg, opts] = (Sentry.captureMessage as jest.Mock).mock.calls[0]
    expect(msg).toBe('flatten.uri_missing')
    expect(opts).toMatchObject({ level: 'warning' })
    expect(opts.extra).toMatchObject({
      uri: 'file:///input.jpg',
      input_uri: 'file:///input.jpg',
      exists: false,
    })
  })

  it('does not crash if getInfoAsync rejects', async () => {
    ;(FileSystem.getInfoAsync as jest.Mock).mockRejectedValueOnce(
      new Error('stat failed'),
    )

    await expect(flattenAndResize('file:///input.jpg')).resolves.toBe(
      'file:///input.jpg',
    )

    expect(Sentry.addBreadcrumb).not.toHaveBeenCalled()
    expect(Sentry.captureMessage).not.toHaveBeenCalled()
  })
})
