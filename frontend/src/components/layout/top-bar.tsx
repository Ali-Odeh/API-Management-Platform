import { Check, ChevronsUpDown, LogOut, Monitor, Moon, Shield, Sun, User } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { RoleBadge } from '@/components/badges'
import { Logo } from '@/components/logo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useMe, useMyWorkspaces } from '@/features/me/queries'
import { useMemberAuth } from '@/lib/auth/member'
import { useTheme, type Theme } from '@/lib/theme'

export interface CurrentWorkspace {
  slug: string
  name: string
  role: React.ComponentProps<typeof RoleBadge>['role'] | null
}

/** The bar on every member page: home, workspace switcher, Platform, theme, account. */
export function TopBar({
  current,
  leading,
}: {
  current?: CurrentWorkspace
  /** The mobile menu button, when the page has a sidebar. */
  leading?: React.ReactNode
}) {
  const { data: me } = useMe()
  return (
    <header className="bg-background/95 supports-[backdrop-filter]:bg-background/70 sticky top-0 z-30 flex h-14 items-center gap-2 border-b px-4 backdrop-blur">
      {leading}
      <Link to="/" aria-label="My workspaces" className="mr-2">
        <Logo className="hidden sm:flex" />
        <Logo className="sm:hidden" label="" />
      </Link>
      <WorkspaceSwitcher current={current} />
      {current?.role ? (
        <RoleBadge role={current.role} />
      ) : current && me?.is_platform_admin ? (
        <Badge variant="outline">Platform Admin</Badge>
      ) : null}
      <div className="ml-auto flex items-center gap-1">
        {me?.is_platform_admin && (
          <Button asChild variant="ghost" size="sm">
            <Link to="/platform">
              <Shield />
              <span className="hidden sm:inline">Platform</span>
            </Link>
          </Button>
        )}
        <ThemeMenu />
        <UserMenu />
      </div>
    </header>
  )
}

function WorkspaceSwitcher({ current }: { current: CurrentWorkspace | undefined }) {
  const { data: workspaces } = useMyWorkspaces()
  const navigate = useNavigate()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="max-w-56 justify-between gap-2">
          <span className="truncate">{current?.name ?? 'Select a workspace'}</span>
          <ChevronsUpDown className="opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>My workspaces</DropdownMenuLabel>
        {workspaces?.map((ws) => (
          <DropdownMenuItem
            key={ws.slug}
            onSelect={() => void navigate(`/w/${ws.slug}`)}
            className="justify-between"
          >
            <span className="truncate">{ws.name}</span>
            {ws.slug === current?.slug && <Check />}
          </DropdownMenuItem>
        ))}
        {workspaces?.length === 0 && (
          <DropdownMenuItem disabled>You are not a member of any workspace</DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void navigate('/')}>All my workspaces</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

const THEMES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

export function ThemeMenu() {
  const { theme, resolved, setTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          {resolved === 'dark' ? <Moon /> : <Sun />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={theme}
          onValueChange={(value) => {
            setTheme(value as Theme)
          }}
        >
          {THEMES.map(({ value, label, icon: Icon }) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <Icon /> {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function UserMenu() {
  const auth = useMemberAuth()
  const navigate = useNavigate()
  const email = auth.session?.user.email ?? ''
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="Account menu" className="gap-2">
          <User />
          <span className="hidden max-w-48 truncate md:inline">{email}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="truncate font-normal">{email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void navigate('/account')}>
          <User /> Account and security
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void auth.signOut()}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
