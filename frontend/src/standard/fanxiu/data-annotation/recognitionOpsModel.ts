type RecognitionOpsCategory = { id: string; label: string; count: number }
type RecognitionOpsIssue = { id: string; category: string; label: string }

export type RecognitionOpsTreeNode = {
  id: string
  label: string
  type: 'category' | 'issue'
  issueId?: string
  children?: RecognitionOpsTreeNode[]
}

export const buildRecognitionOpsTree = (
  categories: RecognitionOpsCategory[],
  issues: RecognitionOpsIssue[],
): RecognitionOpsTreeNode[] => categories.map((category) => ({
  id: `category:${category.id}`,
  label: `${category.label} ${category.count}`,
  type: 'category',
  children: issues
    .filter((issue) => issue.category === category.id)
    .map((issue) => ({
      id: `issue:${issue.id}`,
      label: issue.label,
      type: 'issue',
      issueId: issue.id,
    })),
}))

export const formatAmbiguitySelectionCounts = (counts: Record<string, number>): string => (
  Object.entries(counts)
    .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
    .map(([sceneId, count]) => `${sceneId === 'unresolved' ? '未解决' : `#${sceneId}`} ${count}次`)
    .join(' / ')
)
