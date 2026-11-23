import { zodResolver } from '@hookform/resolvers/zod'
import { AlertTriangle, UserPlus, UserX } from 'lucide-react'
import { useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { errorMessage } from '@/api/errors'
import type { Role } from '@/api/types'
import { RoleBadge, StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable } from '@/components/data-table'
import { TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useMe } from '@/features/me/queries'
import { useWorkspace } from '@/features/workspace/context'
import { formatDate } from '@/lib/format'
import { showServerError } from '@/lib/forms'
import { ROLE_LABELS } from '@/lib/permissions'
import { useMemberMutations, useMembers, type Member } from './queries'

const ROLES: Role[] = ['owner', 'api_admin', 'viewer']

const ROLE_HELP: Record<Role, string> = {
  owner: 'Everything, including members and deleting the workspace.',
  api_admin: 'Services, routes, consumers, keys, plans, APIs and apps; the activity log.',
  viewer: 'Sees every page, changes nothing.',
}

function RoleSelect({
  value,
  onChange,
  disabled,
  label,
}: {
  value: Role
  onChange: (role: Role) => void
  disabled?: boolean
  label: string
}) {
  return (
    <Select
      value={value}
      disabled={disabled}
      onValueChange={(v) => {
        onChange(v as Role)
      }}
    >
      <SelectTrigger className="w-36" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((role) => (
          <SelectItem key={role} value={role}>
            {ROLE_LABELS[role]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** `/w/:slug/members`: who can work in this workspace, with which role. */
export function MembersPage() {
  const { slug, can } = useWorkspace()
  const { data: me } = useMe()
  const members = useMembers(slug)
  const mutations = useMemberMutations(slug)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Member | null>(null)
  const canManage = can('members:write') === 'allowed'
  const owners = members.data?.filter((m) => m.role === 'owner' && m.is_active) ?? []

  async function changeRole(member: Member, role: Role) {
    try {
      await mutations.changeRole.mutateAsync({ userId: member.user_id, role })
      toast.success(`${member.email} is now ${ROLE_LABELS[role]}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <>
      <PageHeader
        title="Members"
        description="People who work in this workspace. New people get an invitation email."
        actions={
          <PermissionButton
            permission="members:write"
            onClick={() => {
              setAdding(true)
            }}
          >
            <UserPlus /> Add member
          </PermissionButton>
        }
      />
      {members.data && owners.length === 1 && (
        <Alert className="border-warning/50 bg-warning/10 mb-4">
          <AlertTriangle />
          <AlertDescription>
            {owners[0]?.email} is the only owner. Add a second owner so the workspace isn't stuck if
            they leave or lose access.
          </AlertDescription>
        </Alert>
      )}
      <DataTable<Member>
        rows={members.data}
        isPending={members.isPending}
        error={members.error}
        onRetry={() => void members.refetch()}
        rowKey={(m) => m.user_id}
        empty={{ title: 'No members' }}
        columns={[
          {
            header: 'Email',
            cell: (m) => (
              <span className="font-medium">
                {m.email}
                {m.user_id === me?.id && <span className="text-muted-foreground"> (you)</span>}
              </span>
            ),
          },
          {
            header: 'Role',
            cell: (m) =>
              canManage ? (
                <RoleSelect
                  value={m.role}
                  label={`Role of ${m.email}`}
                  // An owner can't change their own role (another owner can).
                  disabled={m.user_id === me?.id}
                  onChange={(role) => void changeRole(m, role)}
                />
              ) : (
                <RoleBadge role={m.role} />
              ),
          },
          {
            header: 'Status',
            cell: (m) => <StatusBadge status={m.is_active ? 'active' : 'disabled'} />,
          },
          { header: 'Since', cell: (m) => formatDate(m.created_at) },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (m) =>
              m.user_id === me?.id ? null : (
                <PermissionButton
                  permission="members:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${m.email}`}
                  onClick={() => {
                    setRemoving(m)
                  }}
                >
                  <UserX />
                </PermissionButton>
              ),
          },
        ]}
      />
      {adding && (
        <AddMemberDialog
          onClose={() => {
            setAdding(false)
          }}
          onAdd={(body) => mutations.add.mutateAsync(body)}
        />
      )}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null)
        }}
        title={`Remove ${removing?.email ?? ''}?`}
        description="They lose access to this workspace at once."
        confirmLabel="Remove member"
        destructive
        onConfirm={async () => {
          if (!removing) return
          await mutations.remove.mutateAsync(removing.user_id)
          toast.success(`${removing.email} removed`)
        }}
      />
    </>
  )
}

const schema = z.object({
  email: z.string().trim().email('Enter a valid email'),
  role: z.enum(['owner', 'api_admin', 'viewer']),
})
type Values = z.infer<typeof schema>

function AddMemberDialog({
  onClose,
  onAdd,
}: {
  onClose: () => void
  onAdd: (body: Values) => Promise<{ email: string; invited: boolean }>
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', role: 'viewer' },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      const added = await onAdd(values)
      toast.success(
        added.invited
          ? `Invitation sent to ${added.email}`
          : `${added.email} can now use this workspace`,
      )
      onClose()
    } catch (e) {
      setError(showServerError(form, e))
    }
  })
  const role = useWatch({ control: form.control, name: 'role' })

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Add a member"
      description="Someone without an account gets an email to set their password."
      submitLabel="Add member"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="email" label="Email" type="email" />
        <FormField
          control={form.control}
          name="role"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Role</FormLabel>
              <FormControl>
                <RoleSelect value={field.value} onChange={field.onChange} label="Role" />
              </FormControl>
              <FormDescription>{ROLE_HELP[role]}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </Form>
    </FormDialog>
  )
}
