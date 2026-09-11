import { inject } from 'vue'

export const workspaceKey = Symbol('SpiderFly workspace')

export function useWorkspaceContext() {
  const context = inject(workspaceKey)
  if (!context) throw new Error('Workspace components require a workspace provider')
  return context
}
