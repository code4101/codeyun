import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EARTH_EQUATOR_LENGTH,
  EVERYDAY_MIN_METERS,
  LENGTH_ANCHORS,
  LENGTH_RULERS,
  LENGTH_UNITS,
  UNITS_BY_SCALE,
  convertLength,
  findChain,
  findLengthUnit,
  findProportions,
  formatLength,
  formatSci,
  formatSignificant,
  nearestAnchor,
  parseLength,
  resolveLengthInput,
  solveProportion,
  toMeters,
  toSuperscript,
} from '../src/utils/lengthScale.ts'

test('length units resolve by id, symbol, name and alias', () => {
  assert.equal(findLengthUnit('nm')?.id, 'nm')
  assert.equal(findLengthUnit('纳米')?.id, 'nm')
  assert.equal(findLengthUnit('µm')?.id, 'um')
  assert.equal(findLengthUnit('μm')?.id, 'um')
  assert.equal(findLengthUnit('公里')?.id, 'km')
  assert.equal(findLengthUnit('里')?.factor, 500)
  assert.equal(findLengthUnit('不存在的单位'), undefined)
})

test('length conversions are constant-factor multiplications', () => {
  assert.equal(convertLength(1, 'km', 'm'), 1000)
  assert.equal(convertLength(1, 'nm', 'm'), 1e-9)
  assert.equal(toMeters(40075.017, 'km'), EARTH_EQUATOR_LENGTH)
  assert.ok(Math.abs(convertLength(1, 'au', 'km') - 1.495978707e8) < 1)
  assert.ok(Math.abs(convertLength(1, 'ly', 'm') - 9.4607304725808e15) < 1)
  assert.throws(() => convertLength(1, 'nope', 'm'))
})

test('parseLength reads value plus unit and tolerates whitespace', () => {
  assert.equal(parseLength('1 nm')?.value, 1e-9)
  assert.equal(parseLength('2.5e-9 m')?.value, 2.5e-9)
  assert.equal(parseLength('  40075.017 km ')?.value, EARTH_EQUATOR_LENGTH)
  assert.equal(parseLength('12'), null)
  assert.equal(parseLength('abc m'), null)
  assert.equal(parseLength('1 光年 x'), null)
  assert.equal(parseLength('1 光年')?.value, 9.4607304725808e15)
})

test('resolveLengthInput accepts either an anchor name or a value with a unit', () => {
  assert.equal(resolveLengthInput('人类头发直径')?.value, 7e-5)
  assert.equal(resolveLengthInput('1 米')?.value, 1)
  assert.equal(resolveLengthInput('1nm')?.value, 1e-9)
  assert.equal(resolveLengthInput('40075 km')?.value, 4.0075e7)
  assert.equal(resolveLengthInput('40075 km')?.name, '40075 km')
  assert.equal(resolveLengthInput(''), null)
  assert.equal(resolveLengthInput('随便写的'), null)
})

test('resolveLengthInput reads the coefficient prefix written in a suggestion', () => {
  assert.equal(resolveLengthInput('2 × 光年')?.value, 2 * 9.4607304725808e15)
  assert.equal(resolveLengthInput('2 × 光年')?.name, '2 × 光年')
  assert.equal(resolveLengthInput('1.5 x 乒乓球直径')?.value, 0.06)
  assert.ok(Math.abs(resolveLengthInput('3 * 人类头发直径')!.value - 2.1e-4) < 1e-18)
  assert.equal(resolveLengthInput('2 × 不存在的东西'), null)
})

test('numbers are rendered with significant digits and superscript exponents', () => {
  assert.equal(formatSignificant(33.72, 2), '34')
  assert.equal(formatSignificant(2.5, 3), '2.5')
  assert.equal(toSuperscript(-17), '⁻¹⁷')
  assert.equal(formatSci(2.495321e-17, 2), '2.50×10⁻¹⁷')
  assert.equal(formatLength(1e-9), '1 nm')
  assert.equal(formatLength(0.5), '50 cm')
  assert.equal(formatLength(EARTH_EQUATOR_LENGTH), '40075 km')
  assert.equal(nearestAnchor(1e-9)?.name, 'DNA 双螺旋直径')
})

test('a : b = c : d solves whichever term is unknown', () => {
  const known = { a: 1e-9, b: 1e-4, c: 1, d: 1e5 }
  assert.equal(solveProportion(known, 'd'), 1e5)
  assert.ok(Math.abs(solveProportion({ b: 1e-4, c: 1, d: 1e5 }, 'a')! - 1e-9) < 1e-24)
  assert.equal(solveProportion({ a: 1e-9, c: 1, d: 1e5 }, 'b'), 1e-4)
  assert.equal(solveProportion({ a: 1e-9, b: 1e-4, d: 1e5 }, 'c'), 1)
})

test('solveProportion keeps the proportion invariant and refuses incomplete input', () => {
  for (const unknown of ['a', 'b', 'c', 'd'] as const) {
    const terms = { a: 3, b: 6, c: 7, d: 14 }
    const solved = solveProportion({ ...terms, [unknown]: undefined }, unknown)
    assert.ok(solved !== null)
    const rebuilt = { ...terms, [unknown]: solved }
    assert.ok(Math.abs(rebuilt.a * rebuilt.d - rebuilt.b * rebuilt.c) < 1e-9)
  }
  assert.equal(solveProportion({ a: 1, b: 2 }, 'd'), null)
  assert.equal(solveProportion({ a: 1, b: 0, c: 3 }, 'd'), null)
})

test('findProportions returns only true proportions built from named objects', () => {
  const targets = [1e-9, 2e-9, 6e-7, 1e-6, 1e-3, 1.7, 1.3, 4.7e4, 1.2742e7, EARTH_EQUATOR_LENGTH, 1.495978707e11]
  for (const target of targets) {
    const suggestions = findProportions(target)
    assert.ok(suggestions.length > 0, `目标 ${target} 应该至少有一条建议`)
    for (const suggestion of suggestions) {
      const { a, b, c, d } = suggestion.terms
      assert.equal(a.value, target)
      assert.ok(suggestion.coefficient >= 1 && suggestion.coefficient <= 10, `系数应落在 [1,10]：${suggestion.coefficient}`)
      assert.ok(suggestion.span >= 1 && suggestion.span <= 7)

      // 把前缀系数带上之后，这条比例式必须真的成立
      const left = a.value / b.value
      const right = suggestion.coefficientSide === 'c'
        ? (c.value * suggestion.coefficient) / d.value
        : c.value / (d.value * suggestion.coefficient)
      assert.ok(Math.abs(right / left - 1) <= 0.25, `比例不成立：${suggestion.text}`)

      // 文本必须能原样读回输入框
      const prefix = suggestion.coefficient === 1 ? '' : `${formatSignificant(suggestion.coefficient, 3)} × `
      const leftText = suggestion.coefficientSide === 'c' ? `${prefix}${c.name}` : c.name
      const rightText = suggestion.coefficientSide === 'd' ? `${prefix}${d.name}` : d.name
      assert.equal(suggestion.text, `${a.name} : ${b.name} = ${leftText} : ${rightText}`)
      for (const term of [leftText, rightText]) {
        assert.ok(resolveLengthInput(term), `文本读不回来：${term}`)
      }

      const names = [a.name, b.name, c.name, d.name]
      assert.equal(new Set(names).size, names.length, '四个名字应互不相同')

      // 左边那个自动微调的数乘上去以后，式子必须精确成立
      const exactLeft = (suggestion.factor * a.value) / b.value
      const exactRight = c.value / d.value
      assert.ok(
        Math.abs(exactLeft / exactRight - 1) < 1e-9,
        `factor 应让比例精确成立：${suggestion.factor}，${suggestion.text}`,
      )
    }
  }
})

test('c、d 必须是看得见、在地球上的东西；b 只要是有名字的就行', () => {
  const everydayNames = new Set([
    ...LENGTH_ANCHORS.filter((anchor) => anchor.everyday && !anchor.abstract).map((anchor) => anchor.name),
    ...LENGTH_RULERS.map((ruler) => ruler.name),
  ])
  const namedNames = new Set([
    ...LENGTH_ANCHORS.filter((anchor) => !anchor.abstract).map((anchor) => anchor.name),
    ...LENGTH_RULERS.map((ruler) => ruler.name),
  ])
  const targets = [1e-9, 1e-15, 1e-6, 1e-3, 1.7, 4.7e4, 1.2742e7, EARTH_EQUATOR_LENGTH, 1.495978707e11, 9.46e20]
  for (const target of targets) {
    for (const suggestion of findProportions(target)) {
      const { b, c, d } = suggestion.terms
      assert.ok(namedNames.has(b.name), `b 必须是有名字的东西：${b.name}`)
      for (const term of [c, d]) {
        assert.ok(everydayNames.has(term.name), `c、d 必须生活化：${term.name}`)
        assert.ok(term.value <= EARTH_EQUATOR_LENGTH * 1.001, `c、d 不该超出地球：${term.name}`)
      }
      assert.ok(c.value >= EVERYDAY_MIN_METERS && d.value >= EVERYDAY_MIN_METERS, 'c、d 必须 ≥ 1 毫米')
    }
  }
})


test('findProportions never recycles the target itself as a comparison term', () => {
  const suggestions = findProportions(1.7)
  const recycled = suggestions.flatMap((item) => [item.terms.b, item.terms.c, item.terms.d])
    .filter((term) => term.name === '成年人身高')
  assert.deepEqual(recycled, [])
})

test('findProportions gives different bridges instead of repeating one', () => {
  const suggestions = findProportions(1e-9, { limit: 4 })
  const bridges = suggestions.map((item) => item.terms.b.name)
  assert.equal(new Set(bridges).size, bridges.length)
})

test('findProportions is empty for a non-positive target', () => {
  assert.deepEqual(findProportions(0), [])
  assert.deepEqual(findProportions(-3), [])
})

const named = (name: string) => {
  const anchor = LENGTH_ANCHORS.find((item) => item.name === name)
  if (anchor) return { value: anchor.value, name: anchor.name }
  const ruler = LENGTH_RULERS.find((item) => item.name === name)
  if (ruler) return { value: ruler.value, name: ruler.name }
  throw new Error(`没有这个参照物：${name}`)
}

/** 钉住任意几项之后，钉住的必须原样保留，其余项重新配出仍然成立的比例式。 */
function assertPinned(target: number, fix: { b?: string; c?: string; d?: string }) {
  const fixed = {
    b: fix.b ? named(fix.b) : undefined,
    c: fix.c ? named(fix.c) : undefined,
    d: fix.d ? named(fix.d) : undefined,
  }
  const suggestions = findProportions(target, { limit: 6, fix: fixed })
  assert.ok(suggestions.length > 0, `钉住 ${JSON.stringify(fix)} 应该还有结果`)
  for (const suggestion of suggestions) {
    const { a, b, c, d } = suggestion.terms
    assert.equal(a.value, target)
    for (const slot of ['b', 'c', 'd'] as const) {
      if (fixed[slot]) assert.equal(suggestion.terms[slot].name, fix[slot], `${slot} 必须保持钉住的值`)
    }
    const left = a.value / b.value
    const right = suggestion.coefficientSide === 'c'
      ? (c.value * suggestion.coefficient) / d.value
      : c.value / (d.value * suggestion.coefficient)
    assert.ok(Math.abs(right / left - 1) <= 0.25, `重配后比例必须成立：${suggestion.text}`)
  }
  return suggestions
}

test('钉住 b 之后 c、d 会重新配出一组成立的对照', () => {
  const suggestions = assertPinned(1e-9, { b: '蚂蚁体长' })
  assert.ok(suggestions.every((item) => item.terms.b.name === '蚂蚁体长'))
})

test('钉住两个项之后，剩下的那一项仍然配得出来', () => {
  assertPinned(1e-9, { b: '蚂蚁体长', d: '地球赤道周长' })
  assertPinned(40075e3, { b: '马拉松全程', c: '一辆汽车长度' })
})

test('钉住的组合根本对不上时，老实返回空', () => {
  // 三项都钉死、且互相矛盾：蚂蚁之于地球直径，不可能是乒乓球这个量级
  assert.deepEqual(
    findProportions(1e-9, { fix: { b: named('蚂蚁体长'), c: named('地球直径'), d: named('乒乓球直径') } }),
    [],
  )
})

test('跨度很大的 a 也能配上——因为 b 可以落到微观或天文那一端', () => {
  for (const target of [1e-15, 1.6e-11, 8.414e-16, 4e16, 9.46e20, 8.8e26]) {
    const suggestions = findProportions(target)
    assert.ok(suggestions.length > 0, `${target} 米应该配得出参照物`)
    for (const suggestion of suggestions) {
      const { c, d } = suggestion.terms
      assert.ok(c.value >= EVERYDAY_MIN_METERS && d.value >= EVERYDAY_MIN_METERS, 'c、d 仍须看得见')
      assert.ok(d.value <= EARTH_EQUATOR_LENGTH * 1.001, 'c、d 仍须在地球范围内')
    }
  }
})


test('长度单位表覆盖公制前缀、英美制、市制与天文，且能解析出标准值', () => {
  assert.equal(findLengthUnit('Å')?.factor, 1e-10)
  assert.equal(findLengthUnit('um')?.factor, 1e-6)
  assert.equal(findLengthUnit('公分')?.id, 'cm')
  assert.equal(findLengthUnit('英尺')?.factor, 0.3048)
  assert.equal(findLengthUnit('码')?.factor, 0.9144)
  assert.equal(findLengthUnit('海里')?.factor, 1852)
  assert.equal(findLengthUnit('里格')?.factor, 4828.032)
  assert.ok(Math.abs(findLengthUnit('测量英里')!.factor - 1609.3472186944375) < 1e-9)
  assert.equal(findLengthUnit('光秒')?.factor, 299792458)
  assert.equal(findLengthUnit('光日')?.factor, 299792458 * 86400)
  assert.equal(findLengthUnit('天文单位')?.factor, 149597870700)
  assert.equal(findLengthUnit('秒差距')?.factor, 3.0856775814913673e16)
  assert.equal(findLengthUnit('尧米')?.factor, 1e24)

  const ids = LENGTH_UNITS.map((unit) => unit.id)
  const symbols = LENGTH_UNITS.map((unit) => unit.symbol)
  assert.equal(new Set(ids).size, ids.length, 'id 不能重复')
  assert.equal(new Set(symbols).size, symbols.length, 'symbol 不能重复')
  for (const unit of LENGTH_UNITS) {
    assert.ok(unit.factor > 0, unit.name)
    assert.equal(findLengthUnit(unit.id)?.id, unit.id)
    assert.equal(findLengthUnit(unit.name)?.id, unit.id)
    assert.equal(findLengthUnit(unit.symbol)?.id, unit.id)
    for (const alias of unit.aliases ?? []) assert.equal(findLengthUnit(alias)?.id, unit.id, alias)
  }
})

test('a 的单位下拉覆盖全部长度单位并严格按尺度升序', () => {
  assert.equal(UNITS_BY_SCALE.length, LENGTH_UNITS.length)
  for (let index = 1; index < UNITS_BY_SCALE.length; index += 1) {
    assert.ok(
      UNITS_BY_SCALE[index].factor > UNITS_BY_SCALE[index - 1].factor,
      `${UNITS_BY_SCALE[index].name} 应该排在 ${UNITS_BY_SCALE[index - 1].name} 后面`,
    )
  }
  assert.equal(UNITS_BY_SCALE[0].id, 'ym')
  assert.equal(UNITS_BY_SCALE[UNITS_BY_SCALE.length - 1].id, 'Ym')
  for (const id of ['nm', 'mm', 'm', 'km', 'in', 'ft', 'mi', 'nmi', 'chi', 'li', 'au', 'ly', 'pc']) {
    assert.ok(UNITS_BY_SCALE.some((unit) => unit.id === id), `缺少单位 ${id}`)
  }
})

test('一组分式装不下时，右边会自己拆成几组相乘，而且仍旧精确', () => {
  const cases: [string, number][] = [
    ['拉尼亚凯亚超星系团', 1e-9],
    ['可观测宇宙直径', 1e-9],
    ['质子直径', 1e3],
    ['银河系直径', 1e-15],
    ['蚂蚁体长', 1.2742e7],
  ]
  for (const [bName, target] of cases) {
    const b = named(bName)
    const chain = findChain(target, { b })
    assert.ok(chain, `${target} 米 之于 ${bName} 应该拆得出来`)
    assert.ok(chain.groups.length >= 1 && chain.groups.length <= 4)
    for (const group of chain.groups) {
      assert.notEqual(group.c.name, group.d.name)
      assert.ok(group.c.value >= EVERYDAY_MIN_METERS && group.d.value >= EVERYDAY_MIN_METERS, '每组都必须是看得见的东西')
      assert.ok(group.c.value <= EARTH_EQUATOR_LENGTH * 1.001 && group.d.value <= EARTH_EQUATOR_LENGTH * 1.001, '每组都不该超出地球')
    }
    const left = (chain.factor * target) / b.value
    const right = chain.groups.reduce((acc, group) => acc * group.ratio, 1)
    assert.ok(Math.abs(left / right - 1) < 1e-9, `拆成 ${chain.groups.length} 组之后也必须精确`)
  }
})

test('跨度小的时候一组就够，不用拆', () => {
  const chain = findChain(1.2742e7, { b: named('蚂蚁体长') })
  assert.ok(chain)
  assert.equal(chain.groups.length, 1)
})

test('every ruler and anchor is a positive, plausible length', () => {


  for (const item of [...LENGTH_ANCHORS, ...LENGTH_RULERS]) {
    assert.ok(item.value > 0, item.name)
    assert.ok(Number.isFinite(item.value), item.name)
  }
  assert.equal(new Set(LENGTH_ANCHORS.map((item) => item.name)).size, LENGTH_ANCHORS.length)
})
