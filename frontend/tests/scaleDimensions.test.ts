import assert from 'node:assert/strict'
import test from 'node:test'

import { areaSystem } from '../src/utils/dimensions/area.ts'
import { lengthSystem } from '../src/utils/dimensions/length.ts'
import { massSystem } from '../src/utils/dimensions/mass.ts'
import { speedSystem } from '../src/utils/dimensions/speed.ts'
import { timeSystem } from '../src/utils/dimensions/time.ts'
import { volumeSystem } from '../src/utils/dimensions/volume.ts'
import { findChain, findProportions, findUnit, formatWithUnits, unitsByScale } from '../src/utils/scale.ts'

const systems = [lengthSystem, timeSystem, massSystem, areaSystem, volumeSystem, speedSystem]

for (const system of systems) {
  test(`${system.title}：单位表完整且按大小升序`, () => {
    const ids = system.units.map((unit) => unit.id)
    const symbols = system.units.map((unit) => unit.symbol)
    assert.equal(new Set(ids).size, ids.length, 'id 不能重复')
    assert.equal(new Set(symbols).size, symbols.length, 'symbol 不能重复')
    for (const unit of system.units) {
      assert.ok(unit.factor > 0, unit.name)
      assert.equal(findUnit(system, unit.id)?.id, unit.id)
      assert.equal(findUnit(system, unit.symbol)?.id, unit.id)
      for (const alias of unit.aliases ?? []) assert.equal(findUnit(system, alias)?.id, unit.id, alias)
    }
    const sorted = unitsByScale(system)
    for (let index = 1; index < sorted.length; index += 1) {
      assert.ok(sorted[index].factor > sorted[index - 1].factor, `${sorted[index].name} 顺序不对`)
    }
  })

  test(`${system.title}：每个参照物都能配出精确的比例式`, () => {
    const usable = system.anchors.filter((anchor) => !anchor.abstract)
    assert.ok(usable.length >= 10, '参照物太少')
    for (const anchor of usable) {
      for (const suggestion of findProportions(system, anchor.value, { limit: 2 })) {
        const { a, b, c, d } = suggestion.terms
        assert.equal(a.value, anchor.value)
        // 左边那个自动微调值乘上去，两边必须精确相等
        const left = (suggestion.factor * a.value) / b.value
        const right = c.value / d.value
        assert.ok(Math.abs(left / right - 1) < 1e-9, `${system.title} 的 ${suggestion.text} 不精确`)
        // c、d 必须落在「看得见」的区间里
        for (const term of [c, d]) {
          assert.ok(term.value >= system.visible.min, `${term.name} 太小了`)
          assert.ok(term.value <= system.visible.max * 1.001, `${term.name} 超出了上限`)
        }
        for (const term of [b, c, d]) {
          assert.ok(term.name.length > 0)
        }
      }
    }
  })

  test(`${system.title}：跨度很大时会拆成几组相乘，且仍旧精确`, () => {
    const extremes = [...system.anchors].filter((anchor) => !anchor.abstract)
      .sort((left, right) => left.value - right.value)
    const smallest = extremes[0]
    const largest = extremes[extremes.length - 1]
    const chain = findChain(system, smallest.value, { b: { value: largest.value, name: largest.name } })
    if (chain) {
      assert.ok(chain.groups.length >= 1 && chain.groups.length <= 4)
      const left = (chain.factor * smallest.value) / largest.value
      const right = chain.groups.reduce((acc, group) => acc * (group.c.value / group.d.value), 1)
      assert.ok(Math.abs(left / right - 1) < 1e-9, '拆组之后也必须精确')
      for (const group of chain.groups) {
        assert.ok(group.c.value >= system.visible.min && group.d.value >= system.visible.min, '每组都得看得见')
      }
    }
  })

  test(`${system.title}：显示形式带单位且可读`, () => {
    const unit = findUnit(system, system.defaultUnit ?? '')
    assert.ok(unit, '要有默认单位')
    const text = formatWithUnits(system, unit.factor)
    assert.ok(text.includes(unit.symbol), `${text} 应该带上单位符号`)
  })
}
