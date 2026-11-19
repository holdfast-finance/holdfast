import { Navigate, useParams } from 'react-router'

/**
 * Older invite links put the token in the path (/invite/<token>). Move it into the fragment,
 * replacing the history entry, so the token leaves the address bar and history; InvitePage then
 * reads it from the fragment and strips that too.
 */
export function LegacyInviteRedirect() {
  const { token } = useParams()
  return <Navigate to={{ pathname: '/invite', hash: token ? `#${token}` : '' }} replace />
}
