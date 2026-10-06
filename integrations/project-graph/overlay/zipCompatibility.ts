/** Older Chromium exposes the streams but rejects ZIP's raw DEFLATE format.
 * Test the format itself, so zip.js can select its bundled WASM codec before
 * opening a PRG instead of retrying the same unsupported native constructor. */
export function supportsNativeZipStreams(scope = globalThis): boolean {
  try {
    new scope.CompressionStream('deflate-raw');
    new scope.DecompressionStream('deflate-raw');
    return true;
  } catch {
    return false;
  }
}
