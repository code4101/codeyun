import type { Plugin } from 'postcss'

/** 全站普通 CSS 声明的动态/小/大视口单位回退，编译期完成，无运行时监听。
 * 保留现代单位在后，旧内核忽略它时使用前面的静态单位；同样处理 calc/min/clamp。
 * 引号内容、注释和 url 不属于尺寸表达式。自定义属性的 token 不会被旧内核
 * 自动忽略，必须由作者用 @supports 设置，不能通过重复声明模拟回退。
 */
export function staticViewportValue(value: string): string {
  return value.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\*[\s\S]*?\*\/|url\((?:\\.|[^)])*\)|(?<![\w-])([+-]?(?:\d*\.)?\d+(?:e[+-]?\d+)?)([dsl])(v(?:min|max|h|w|i|b))\b/gi,
    (match, number: string | undefined, _kind: string, unit: string) => number == null ? match : `${number}${unit}`)
}

export function viewportUnitFallback(): Plugin {
  return {
    postcssPlugin: 'codeyun-viewport-unit-fallback',
    Declaration(declaration) {
      if (declaration.prop.startsWith('--')) return
      const fallback = staticViewportValue(declaration.value)
      if (fallback === declaration.value) return
      let previous = declaration.prev()
      while (previous?.type === 'comment') previous = previous.prev()
      if (previous?.type === 'decl' && previous.prop === declaration.prop
        && previous.value === fallback && previous.important === declaration.important) return
      declaration.cloneBefore({ value: fallback })
    },
  }
}
