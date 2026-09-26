import { test } from 'node:test'
import assert from 'node:assert/strict'
import { countries, countryAt, countryDetails, countryGroup } from './countryData'
import { encyclopediaLinks, loadCountrySummary } from './countryEncyclopedia'

test('country selection handles land, oceans, longitude wrapping and grouped regions', () => {
  assert.equal(countryAt(116.4, 39.9), '中国')
  assert.equal(countryAt(-100, 40), '美国')
  assert.equal(countryAt(260, 40), '美国')
  assert.equal(countryAt(2.35, 48.85), '法国')
  assert.equal(countryAt(121, 23.5), '中国')
  assert.equal(countryGroup('中国台湾'), '中国')
  assert.equal(countryAt(-140, 0), '')
})

test('every map group has facts, dated population and a valid flag code when available', () => {
  for (const feature of countries.features) {
    const info = countryDetails[feature.properties.group]
    assert.ok(info, feature.properties.name)
    assert.ok(!info.code || /^[A-Z]{2}$/.test(info.code))
    if (info.population !== null) {
      assert.ok(info.population > 0)
      assert.ok(info.populationYear > 1900 && info.populationYear <= 2026)
    }
  }
  assert.equal(countryDetails['中国']!.code, 'CN')
  assert.equal(countryDetails['南极洲']!.population, null)
})

test('encyclopedia handles missing pages, errors, cancellation and cached plain text', async () => {
  const originalFetch = globalThis.fetch
  const signal = new AbortController().signal
  try {
    globalThis.fetch = async () => Response.json({ query: { pages: [{ missing: true }] } })
    assert.equal(await loadCountrySummary('missing', signal), null)
    globalThis.fetch = async () => Response.json({ query: { pages: [{ extract: 'ambiguous', pageprops: { disambiguation: '' } }] } })
    assert.equal(await loadCountrySummary('ambiguous', signal), null)
    globalThis.fetch = async () => new Response('', { status: 403 })
    await assert.rejects(loadCountrySummary('blocked', signal))
    globalThis.fetch = async (_url, init) => { init?.signal?.throwIfAborted(); return Response.json({ query: { pages: [{ extract: '简介正文' }] } }) }
    const controller = new AbortController()
    controller.abort()
    await assert.rejects(loadCountrySummary('aborted', controller.signal))
    const value = await loadCountrySummary('测试国家', signal)
    assert.equal(value?.text, '简介正文')
    globalThis.fetch = async () => { throw new Error('cached results must not fetch again') }
    assert.deepEqual(await loadCountrySummary('测试国家', signal), value)
    assert.ok(encyclopediaLinks('中国').baidu.endsWith(encodeURIComponent('中国')))
  } finally { globalThis.fetch = originalFetch }
})
