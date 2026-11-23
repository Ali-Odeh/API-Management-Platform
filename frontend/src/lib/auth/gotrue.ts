import { GoTrueClient } from '@supabase/auth-js'

/**
 * The two GoTrue servers, reached through the app's own origin (Vite
 * forwards the paths, see vite.config.ts):
 * - members (workspace members and the Platform Admin): `/auth/v1`
 * - developers on the portal (their own accounts and tokens): `/portal-auth/v1`
 *
 * Sessions live in localStorage under separate keys, so one browser can be
 * signed in to both (the trade-off is MT_ROLES_DECISIONS.md decision 4).
 * Links from emails are read by `takeAuthRedirect`, not by GoTrueClient, so
 * the app knows whether a link was an invitation or a password recovery.
 */
function createClient(path: string, storageKey: string): GoTrueClient {
  return new GoTrueClient({
    url: `${window.location.origin}${path}`,
    storageKey,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
  })
}

export const memberAuth = createClient('/auth/v1', 'apim-member-auth')
export const developerAuth = createClient('/portal-auth/v1', 'apim-developer-auth')
