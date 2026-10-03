import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc = workerUrl
export function openOfflinePdf(blob: Blob): Promise<PDFDocumentProxy> {
  return blob.arrayBuffer().then(data => getDocument({
    data: new Uint8Array(data), cMapUrl: '/offline-library/pdfjs/cmaps/', cMapPacked: true,
    standardFontDataUrl: '/offline-library/pdfjs/standard_fonts/', wasmUrl: '/offline-library/pdfjs/wasm/',
    useSystemFonts: true,
  }).promise)
}
