/** Four significant digits; promote the unit if rounding crosses its boundary. */
export function formatCharacterCount(count:number):string {
  if (!count) return '0';
  const rounded = Number(count.toPrecision(4));
  const unit = rounded >= 1e8 ? 1e8 : rounded >= 1e4 ? 1e4 : 1;
  const value = rounded / unit;
  return value.toFixed(Math.max(0, 3 - Math.floor(Math.log10(value)))) + (unit === 1e8 ? '亿' : unit === 1e4 ? '万' : '');
}
