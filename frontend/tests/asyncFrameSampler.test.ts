import assert from 'node:assert/strict';
import test from 'node:test';
import { createAsyncFrameSampler } from '../src/utils/asyncFrameSampler.ts';

test('async sampling discards a stopped generation and cannot spawn a second loop', async (t) => {
  const callbacks = new Map<number, () => Promise<void>>();
  let nextId = 0;
  t.after(() => { Reflect.deleteProperty(globalThis, 'window'); });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    requestAnimationFrame(callback: () => Promise<void>) {
      callbacks.set(++nextId, callback);
      return nextId;
    },
    cancelAnimationFrame(id: number) { callbacks.delete(id); },
  } });
  const reads: Array<(value: number) => void> = [];
  const accepted: number[] = [];
  const errors: unknown[] = [];
  const sampler = createAsyncFrameSampler({
    active: () => true,
    read: () => new Promise<number>((resolve) => reads.push(resolve)),
    accept: (value) => accepted.push(value),
    onError: (error) => errors.push(error),
  });
  const fire = () => {
    const [id, callback] = callbacks.entries().next().value!;
    callbacks.delete(id);
    return callback();
  };
  sampler.start();
  sampler.start();
  assert.equal(callbacks.size, 1);
  const oldRead = fire();
  assert.equal(callbacks.size, 0); // No overlap while awaiting a read.
  sampler.stop();
  sampler.start();
  reads[0](1);
  await oldRead;
  assert.deepEqual(accepted, []);
  assert.equal(callbacks.size, 1);
  const currentRead = fire();
  reads[1](2);
  await currentRead;
  assert.deepEqual(accepted, [2]);
  assert.equal(callbacks.size, 1);
  sampler.stop();
  assert.equal(callbacks.size, 0);
  assert.deepEqual(errors, []);
});

test('read failures stop sampling and are reported once', async (t) => {
  let callback: (() => Promise<void>) | undefined;
  t.after(() => { Reflect.deleteProperty(globalThis, 'window'); });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    requestAnimationFrame(value: () => Promise<void>) { callback = value; return 1; },
    cancelAnimationFrame() { callback = undefined; },
  } });
  const failure = new Error('capture failed');
  const errors: unknown[] = [];
  const sampler = createAsyncFrameSampler({
    active: () => true,
    read: async () => { throw failure; },
    accept: () => assert.fail('a failed capture has no result'),
    onError: (error) => errors.push(error),
  });
  sampler.start();
  const run = callback!;
  callback = undefined;
  await run();
  assert.equal(callback, undefined);
  assert.deepEqual(errors, [failure]);
});
