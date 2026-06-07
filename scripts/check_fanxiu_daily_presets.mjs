import assert from 'node:assert/strict';

import {
  decideDailyFindResult,
  extractDailyProgress,
  getDailyStatusCode,
} from '../frontend/src/standard/fanxiu/data-annotation/dailyFindDecision.ts';
import {
  defaultDailyTaskPresets,
} from '../frontend/src/standard/fanxiu/data-annotation/dailyTaskPresets.ts';

const normalizeSearchText = (text) => text.replace(/\s+/g, '').trim();

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const wildcardToRegExp = (pattern) => new RegExp(
  `^${normalizeSearchText(pattern).split('').map((char) => {
    if (char === '*') return '.*';
    if (char === '?') return '.';
    return escapeRegExp(char);
  }).join('')}$`,
  'i',
);

const textMatches = (text, query, mode) => {
  const normalizedText = normalizeSearchText(text);
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedText || !normalizedQuery) return false;
  if (mode === 'exact') return normalizedText === normalizedQuery;
  if (mode === 'wildcard') return wildcardToRegExp(normalizedQuery).test(normalizedText);
  if (mode === 'regex') return new RegExp(normalizedQuery, 'i').test(normalizedText);
  return normalizedText.includes(normalizedQuery);
};

const presetByLabel = new Map(defaultDailyTaskPresets().map((preset) => [preset.label, preset]));

const assertPresetHit = (label, rowText, expectedProgress) => {
  const preset = presetByLabel.get(label);
  assert.ok(preset, `missing daily preset: ${label}`);
  assert.ok(
    textMatches(rowText, preset.query, preset.matchMode),
    `${label} should match OCR row: ${rowText}`,
  );
  const progress = extractDailyProgress(rowText);
  assert.deepEqual(progress, expectedProgress, `${label} progress should be normalized`);
  const summary = {
    statusCode: getDailyStatusCode('', progress),
    progress,
  };
  const result = decideDailyFindResult(preset, rowText, summary);
  assert.equal(result.decision, 'ready', `${label} should be ready when progress is not full`);
  assert.equal(result.statusCode, 0, `${label} statusCode should be 0 when progress is not full`);
};

const assertDecision = (label, summary, expectedDecision, expectedStatusCode, message) => {
  const preset = presetByLabel.get(label);
  assert.ok(preset, `missing daily preset: ${label}`);
  const result = decideDailyFindResult(preset, message, summary);
  assert.equal(result.decision, expectedDecision, `${label}: ${message}`);
  assert.equal(result.statusCode, expectedStatusCode, `${label}: ${message}`);
};

const assertPresetFields = (label, expected) => {
  const preset = presetByLabel.get(label);
  assert.ok(preset, `missing daily preset: ${label}`);
  for (const [key, value] of Object.entries(expected)) {
    assert.deepEqual(preset[key], value, `${label}.${key} should stay aligned with legacy scan strategy`);
  }
};

assertPresetHit('剑灵', '挑战或扫荡淬剑试炼活10/次80/1', { current: 0, total: 1 });
assertPresetHit('寻道历练', '寻道历练1次5/次80/4', { current: 0, total: 4 });
assertPresetHit('灵塔', '挑战或扫荡混沌灵塔15/次0/1', { current: 0, total: 1 });
assertPresetHit('双修', '完成双人修炼1次10/次80/3', { current: 0, total: 3 });

assertPresetFields('游历', { scanPlan: 'candidate_rows', notFoundStatus: 2 });
assertPresetFields('拜谒', { scanPlan: 'bidirectional', maxPages: 8, reversePages: 6, dragCount: 14, notFoundStatus: 2, requireProgress: false });
assertPresetFields('挑战仙缘', { scanPlan: 'bidirectional', maxPages: 14, reversePages: 18, dragCount: 32, notFoundStatus: 2 });

assert.deepEqual(extractDailyProgress('活10次03'), { current: 0, total: 3 }, 'progress fallback should fold 活10次03 to 0/3');
assert.deepEqual(extractDailyProgress('奖励15/次0.6'), { current: 0, total: 6 }, 'dot between digits should be treated as slash');

assertDecision(
  '灵塔',
  { statusCode: 2, progress: { current: 0, total: 1 } },
  'ready',
  0,
  'completed-looking status must not override incomplete progress',
);
assertDecision(
  '灵塔',
  { statusCode: 2, progress: null },
  'retry',
  2,
  'requireProgress preset with completed status and no progress should request retry',
);
assertDecision(
  '论道',
  { statusCode: 1, progress: null },
  'ongoing',
  1,
  'ongoing status should remain ongoing for no-progress task',
);

console.log('fanxiu daily presets ok');
