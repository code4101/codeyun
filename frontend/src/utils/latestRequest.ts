/** A result may commit only while its request is still the latest.
 * Invalidating does not cancel I/O; it revokes the old request's write access.
 */
export function createLatestRequest() {
  let generation = 0;
  return {
    begin() {
      const token = ++generation;
      return () => token === generation;
    },
    invalidate() { generation += 1; },
  };
}
