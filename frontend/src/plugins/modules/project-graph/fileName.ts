/** Keep the native PRG extension consistent in the file list, editor and download. */
export function graphBaseName(name: string): string {
  return name.trim().replace(/(?:\.prg)+$/i, '')
}

export function graphFileName(name: string): string {
  return `${graphBaseName(name)}.prg`
}
