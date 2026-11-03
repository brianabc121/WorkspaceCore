import { diff3Merge } from 'node-diff3'

// 编辑时别人（人或智能体）先保存了新版本：按行做三方合并（T-0099，方案 6.3）。
// base 是开始编辑时的版本，mine 是编辑器里的内容，theirs 是服务器上的最新版本。
// 两边改了不同的地方就自动合在一起；改了同一处的列出来，让人逐段选“用我的 / 用它的 / 都保留”。
export type MergeChunk =
  | { kind: 'ok'; lines: string[] }
  | { kind: 'conflict'; mine: string[]; theirs: string[]; base: string[]; pick: 'mine' | 'theirs' | 'both' | null }

export function threeWay(base: string, mine: string, theirs: string): MergeChunk[] {
  const regions = diff3Merge(mine.split('\n'), base.split('\n'), theirs.split('\n'), { excludeFalseConflicts: true })
  return regions.map((r): MergeChunk => r.conflict
    ? { kind: 'conflict', mine: r.conflict.a, theirs: r.conflict.b, base: r.conflict.o, pick: null }
    : { kind: 'ok', lines: r.ok ?? [] })
}

export const conflicts = (chunks: MergeChunk[]) => chunks.filter(c => c.kind === 'conflict').length
export const unresolved = (chunks: MergeChunk[]) => chunks.some(c => c.kind === 'conflict' && !c.pick)

export function assemble(chunks: MergeChunk[]) {
  const lines: string[] = []
  for (const c of chunks) {
    if (c.kind === 'ok') lines.push(...c.lines)
    else if (c.pick === 'theirs') lines.push(...c.theirs)
    else if (c.pick === 'both') lines.push(...c.mine, ...c.theirs)
    else lines.push(...c.mine)
  }
  return lines.join('\n')
}
