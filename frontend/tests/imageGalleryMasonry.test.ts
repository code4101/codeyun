import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getUnresolvedMasonryImages,
} from '../src/utils/imageGalleryMasonry.ts'
import type { GalleryImage } from '../src/utils/imageGallery.ts'

const createImage = (id: string, overrides: Partial<GalleryImage> = {}): GalleryImage => ({
  id,
  name: `${id}.jpg`,
  relativePath: `${id}.jpg`,
  folderPath: '',
  size: 1,
  modifiedAt: 0,
  url: null,
  width: null,
  height: null,
  ...overrides,
})

test('rendered masonry placeholders are returned for thumbnail recovery', () => {
  const pending = createImage('pending')
  const ready = createImage('ready', { url: 'blob:ready', urlVariant: 'thumbnail' })
  const failed = createImage('failed', { thumbnailFailed: true })

  assert.deepEqual(
    getUnresolvedMasonryImages(
      [['pending', 'ready'], ['failed', 'pending']],
      [pending, ready, failed],
    ).map((image) => image.id),
    ['pending'],
  )
})
