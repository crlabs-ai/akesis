import { useEffect, useRef, useSyncExternalStore } from 'react'
import { authStore } from '../services/authStore'

/**
 * Runs `reload` whenever the session read token changes after mount, so a page that was refused
 * for want of a token loads its data as soon as a valid one is entered from anywhere in the app.
 */
export function useTokenChangeReload(reload: () => void): void {
  const token = useSyncExternalStore(authStore.subscribe, authStore.getToken, authStore.getToken)
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
    // Only a change of token triggers a reload; the reload function is read through the ref.
  }, [token])
}
