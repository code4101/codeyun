import { Boot, IButtonMenu, IDomEditor } from '@wangeditor/editor'

class ImageMergeMenu implements IButtonMenu {
  title = '拼接图片'
  // iconSvg: 使用一个简单的图片拼接图标
  iconSvg = '<svg viewBox="0 0 1024 1024"><path d="M880 112H144c-17.7 0-32 14.3-32 32v736c0 17.7 14.3 32 32 32h736c17.7 0 32-14.3 32-32V144c0-17.7-14.3-32-32-32zM184 848V184h656v664H184z m120-432h416v80H304z m0 160h416v80H304z m0-320h416v80H304z" fill="currentColor"></path></svg>'
  tag = 'button'

  getValue(editor: IDomEditor): string | boolean {
    return false
  }

  isActive(editor: IDomEditor): boolean {
    return false
  }

  isDisabled(editor: IDomEditor): boolean {
    const { selection } = editor
    if (selection == null) return true
    return false
  }

  exec(editor: IDomEditor, value: string | boolean) {
    // 触发一个自定义事件，供 Vue 组件监听
    editor.emit('image-merge-click')
  }
}

let isRegistered = false

export const registerWangEditorPlugins = () => {
  if (isRegistered) return

  Boot.registerMenu({
    key: 'image-merge-button',
    factory() {
      return new ImageMergeMenu()
    },
  })
  
  isRegistered = true
}
