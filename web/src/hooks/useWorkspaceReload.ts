import { useEffect, useRef, useSyncExternalStore } from 'react'
import { session } from '../services/session'

const workspaceId = () => session.workspaceId()

/**
 * Runs `reload` whenever the selected workspace changes after mount, so every page shows the
 * newly selected workspace's data, and only that workspace's data.
 */
export function useWorkspaceReload(reload: () => void): void {
  const current = useSyncExternalStore(session.subscribe, workspaceId, workspaceId)
  const reloadRef = useRef(reload)
  const mounted = useRef<boolean>(false)

  useEffect(() => {
    reloadRef.current = reload
  }, [reload])

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    reloadRef.current()
    // Only a change of workspace triggers a reload; the reload function is read through the ref.
  }, [current])
}
