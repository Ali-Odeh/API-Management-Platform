import { Navigate, type RouteObject } from 'react-router'
import { AccountPage } from '@/features/account/account-page'
import { ForgotPasswordPage, LoginPage, SetPasswordPage } from '@/features/auth/pages'
import { WorkspacesPage } from '@/features/me/workspaces-page'
import { PlatformLayout } from '@/features/platform/layout'
import { WorkspaceLayout } from '@/features/workspace/layout'
import { RequireMember } from './guards'
import { MemberLayout, MemberRoot } from './member-layout'
import { NotFoundPage, RouteErrorPage } from './not-found'

/**
 * A page loaded when first opened, so the first download stays small.
 * `load` imports the page's module and picks its component.
 */
function page(path: string | undefined, load: () => Promise<React.ComponentType>): RouteObject {
  const lazy = async () => ({ Component: await load() })
  return path === undefined ? { index: true, lazy } : { path, lazy }
}

/** The dashboard's pages under `/w/:slug` (PLAN.md section 8). */
const dashboardPages: RouteObject[] = [
  page(undefined, async () => (await import('@/features/workspace/overview-page')).OverviewPage),
  page('services', async () => (await import('@/features/services/services-page')).ServicesPage),
  page(
    'services/:id',
    async () => (await import('@/features/services/service-detail')).ServiceDetailPage,
  ),
  page('routes', async () => (await import('@/features/routes/routes-page')).RoutesPage),
  page(
    'consumers',
    async () => (await import('@/features/consumers/consumers-page')).ConsumersPage,
  ),
  page(
    'consumers/:id',
    async () => (await import('@/features/consumers/consumer-detail')).ConsumerDetailPage,
  ),
  page('plans', async () => (await import('@/features/plans/plans-page')).PlansPage),
  page('apis', async () => (await import('@/features/apis/apis-page')).ApisPage),
  page('apis/:id', async () => (await import('@/features/apis/api-detail')).ApiDetailPage),
  page('apps', async () => (await import('@/features/apps/apps-page')).AppsPage),
  page('members', async () => (await import('@/features/members/members-page')).MembersPage),
  page('activity', async () => (await import('@/features/audit/activity-page')).ActivityPage),
  page('logs', async () => (await import('@/features/logs/logs-page')).LogsPage),
  page('settings', async () => (await import('@/features/workspace/settings-page')).SettingsPage),
]

/** The Platform Admin's pages under `/platform` (PLAN.md section 8). */
const platformPages: RouteObject[] = [
  { index: true, element: <Navigate to="workspaces" replace /> },
  page(
    'workspaces',
    async () => (await import('@/features/platform/workspaces-page')).PlatformWorkspacesPage,
  ),
  page('users', async () => (await import('@/features/platform/users-page')).PlatformUsersPage),
  page(
    'developers',
    async () => (await import('@/features/platform/developers-page')).PlatformDevelopersPage,
  ),
  page(
    'activity',
    async () => (await import('@/features/platform/activity-page')).PlatformActivityPage,
  ),
]

/**
 * The app's pages (PLAN.md section 8). Areas:
 * - account: login, my workspaces, account (members' GoTrue)
 * - dashboard: `/w/:slug/...` (workspace members, by role)
 * - platform: `/platform/...` (the Platform Admin, with MFA)
 */
export const routes: RouteObject[] = [
  {
    element: <MemberRoot />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/forgot-password', element: <ForgotPasswordPage /> },
      { path: '/set-password', element: <SetPasswordPage /> },
      {
        element: <RequireMember />,
        children: [
          {
            element: <MemberLayout />,
            children: [
              { path: '/', element: <WorkspacesPage /> },
              { path: '/account', element: <AccountPage /> },
            ],
          },
          {
            path: '/w/:slug',
            element: <WorkspaceLayout />,
            children: dashboardPages,
          },
          { path: '/platform', element: <PlatformLayout />, children: platformPages },
        ],
      },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
]
