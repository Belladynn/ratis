// ratis_client/services/image-pipeline.ts
//
// Post-process a freshly-taken camera photo before upload :
// 1. Honor EXIF orientation by re-encoding pixels in the correct rotation
//    (without this, Android phones in portrait produce JPEGs with a
//    rotation tag but raw-sensor pixels — boto3 / Pillow / PaddleOCR all
//    ignore the tag and read the image upside down).
// 2. Resize down to a sensible max dimension. Modern phones produce 12 MP
//    photos that are 3-5 MB and 4000+ px on the long edge ; PaddleOCR
//    over-segments above ~2000 px (each text line gets split in 2-3
//    fragmented blocks), AND the upload is needlessly slow on a phone
//    network. 1600 px on the long edge is a sweet spot for receipt OCR.
//
// Cf alpha 2026-04-26 (AF-12 / EXIF rotation bug + AF-10 OCR quality).

import * as ImageManipulator from 'expo-image-manipulator';
// Legacy entry — `getInfoAsync` is deprecated on the default expo-file-system
// import (the new API uses `File`/`Directory` classes), and calling it via the
// default export now throws at runtime. The legacy entry remains supported.
import * as FileSystem from 'expo-file-system/legacy';
import * as Sentry from '@sentry/react-native';

/** Max dimension on the long edge after resize. */
const MAX_DIMENSION = 1600;

/** JPEG compression after re-encoding (0-1, higher = better quality, larger). */
const COMPRESS_QUALITY = 0.75;

/**
 * Re-encode a captured photo with EXIF orientation baked into pixels and
 * resize down to MAX_DIMENSION. Returns the new file URI (in cache dir).
 * The original file is kept by the OS and cleaned up automatically.
 *
 * Emits a Sentry breadcrumb (`category=scan.pipeline`) with the URI/size/mtime
 * of the manipulated file. If the file is unexpectedly missing right after
 * `manipulateAsync` returned a URI, captures a `flatten.uri_missing` warning.
 * No PII — only the cache-local file:// path and stat metadata.
 *
 * Investigation hook (alpha 2026-04-27): user reports that occasionally an
 * OLD photo is uploaded instead of the freshly-taken one. These breadcrumbs
 * let us see, scan after scan, which URI flows out of this stage.
 */
export async function flattenAndResize(uri: string): Promise<string> {
  const result = await ImageManipulator.manipulateAsync(
    uri,
    [{ resize: { width: MAX_DIMENSION } }],
    {
      compress: COMPRESS_QUALITY,
      format: ImageManipulator.SaveFormat.JPEG,
    },
  );

  // Best-effort stat — never let an instrumentation failure break the upload.
  try {
    const info = await FileSystem.getInfoAsync(result.uri);
    const data: Record<string, unknown> = {
      uri: result.uri,
      input_uri: uri,
      exists: info.exists,
      // `modificationTime` is in seconds (legacy API); convert to ms epoch
      // so it is comparable to `Date.now()` collected later in the pipeline.
      size: info.exists ? info.size : undefined,
      modificationTime:
        info.exists && typeof info.modificationTime === 'number'
          ? info.modificationTime * 1000
          : undefined,
    };
    Sentry.addBreadcrumb({
      category: 'scan.pipeline',
      message: 'flattenAndResize.done',
      level: 'info',
      data,
    });
    if (!info.exists) {
      Sentry.captureMessage('flatten.uri_missing', {
        level: 'warning',
        extra: data,
      });
    }
  } catch {
    // Stat failures are non-fatal — keep returning the URI.
  }

  return result.uri;
}
