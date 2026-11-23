import type { RouteObject } from 'react-router'
import { AccountPage } from '@/features/account/account-page'
import { ForgotPasswordPage, LoginPage, SetPasswordPage } from '@/features/auth/pages'
import { WorkspacesPage } from '@/features/me/workspaces-page'
import { WorkspaceHome } from '@/features/workspace/home'
import { WorkspaceLayout } from '@/features/workspace/layout'
import { RequireMember } from './guards'
import { MemberLayout, MemberRoot } from './member-layout'
import { NotFoundPage, RouteErrorPage } from './not-found'

/**
 * The app's pages (PLAN.md section 8). Areas:
 * - account: login, my workspaces, account (members' GoTrue)
 * - dashboard: `/w/:slug/...` (workspace members, by role)
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
            children: [{ index: true, element: <WorkspaceHome /> }],
          },
        ],
      },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
]
