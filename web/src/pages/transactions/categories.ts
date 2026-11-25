import type { CategoryNode, CategorySource } from '@/api/ledger'

export interface CategoryInfo {
  id: string
  name: string
  /** The group it sits under, or null for a top-level category. */
  group: string | null
}

/** Every node in the tree (hidden ones too, so old rows still show their name). */
export function indexCategories(tree: CategoryNode[]): Map<string, CategoryInfo> {
  const map = new Map<string, CategoryInfo>()
  const walk = (nodes: CategoryNode[], group: string | null) => {
    for (const n of nodes) {
      map.set(n.id, { id: n.id, name: n.name, group })
      walk(n.children, n.name)
    }
  }
  walk(tree, null)
  return map
}

/** The id plus every descendant id: the API matches category ids exactly. */
export function withDescendants(tree: CategoryNode[], id: string): string[] {
  const find = (nodes: CategoryNode[]): CategoryNode | undefined => {
    for (const n of nodes) {
      if (n.id === id) return n
      const hit = find(n.children)
      if (hit) return hit
    }
    return undefined
  }
  const out: string[] = []
  const collect = (n: CategoryNode) => {
    out.push(n.id)
    n.children.forEach(collect)
  }
  const node = find(tree)
  if (node) collect(node)
  else out.push(id)
  return out
}

export interface PickerGroup {
  id: string
  label: string
  options: { id: string; name: string }[]
}

/** What a person can choose: visible categories, grouped by their parent. */
export function pickerGroups(tree: CategoryNode[], search = ''): PickerGroup[] {
  const needle = search.trim().toLowerCase()
  const groups: PickerGroup[] = []
  const add = (n: CategoryNode, label: string, nodes: CategoryNode[]) => {
    const options = nodes
      .filter((c) => !c.is_hidden)
      .filter(
        (c) =>
          !needle || c.name.toLowerCase().includes(needle) || label.toLowerCase().includes(needle),
      )
      .map((c) => ({ id: c.id, name: c.name }))
    if (options.length > 0) groups.push({ id: n.id, label, options })
  }
  for (const top of tree) {
    if (top.is_hidden) continue
    // A top-level category with children is a heading and a choice of its own; one with none is a
    // single-option group.
    add(top, top.name, top.children.length > 0 ? [top, ...top.children] : [top])
  }
  return groups
}

export const SOURCE_LABELS: Record<CategorySource, string> = {
  user: 'You',
  rule: 'A rule',
  plaid: 'Your bank',
  ml: 'A suggestion',
}

/** Where the current category came from, in a short plain phrase. */
export function sourcePhrase(source: CategorySource | null): string {
  switch (source) {
    case 'user':
      return 'Set by you'
    case 'rule':
      return 'Set by a rule'
    case 'plaid':
      return 'From your bank'
    case 'ml':
      return 'Suggested'
    case null:
      return 'Automation can change this'
  }
}

export const READ_ONLY_REASON =
  'This account belongs to someone else in your household, so only they can change its transactions.'
