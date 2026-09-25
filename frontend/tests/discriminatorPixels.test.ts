import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildDiscriminatorWeights,
  computeDiscriminatorVariantError,
  type RgbaPixels,
} from '../src/standard/fanxiu/data-annotation/discriminatorPixels.ts';

const pixels = (values: number[]): RgbaPixels => ({
  width: values.length,
  height: 1,
  data: new Uint8ClampedArray(values.flatMap((value) => [value, value, value, 255])),
});

test('only the strongest reference differences receive weight', () => {
  const result = buildDiscriminatorWeights([
    { reference: pixels(Array(10).fill(0)), alpha: null },
    { reference: pixels([0, 0, 0, 0, 0, 0, 0, 0, 20, 100]), alpha: null },
  ], 10, 1);
  assert.equal(result.activePixels, 2);
  assert.deepEqual(Array.from(result.weights.slice(0, 8)), Array(8).fill(0));
  assert.ok(Math.abs(result.weights[8] - 0.2) < 1e-7);
  assert.equal(result.weights[9], 1);
});

test('identical references supply no discriminating evidence', () => {
  const reference = { reference: pixels([20, 40, 60]), alpha: null };
  const result = buildDiscriminatorWeights([reference, reference], 3, 1);
  assert.equal(result.activePixels, 0);
  assert.equal(computeDiscriminatorVariantError(
    reference.reference, reference.reference, result.weights, null,
  ), Infinity);
});

test('masked pixels cannot affect weighted mismatch', () => {
  assert.equal(computeDiscriminatorVariantError(
    pixels([255, 20]), pixels([0, 10]), new Float32Array([1, 1]),
    new Uint8ClampedArray([0, 255]),
  ), 10);
});
