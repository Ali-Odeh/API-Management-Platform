import { zodResolver } from '@hookform/resolvers/zod'
import { FileCheck2, Plus } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'
import { StatusBadge } from '@/components/badges'
import { DataTable, Pager } from '@/components/data-table'
import { TextAreaField, TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Form } from '@/components/ui/form'
import { useWorkspace } from '@/features/workspace/context'
import { PAGE_SIZE } from '@/features/workspace/keys'
import { showServerError } from '@/lib/forms'
import { useApiMutations, useApis, type ApiDetail, type ApiSummary } from './queries'

/** `/w/:slug/apis`: the APIs this workspace offers on the developer portal. */
export function ApisPage() {
  const { slug } = useWorkspace()
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const apis = useApis(slug, page)
  const { create } = useApiMutations(slug)
  const [creating, setCreating] = useState(false)

  return (
    <>
      <PageHeader
        title="APIs"
        description="Group routes into APIs with docs, and publish them on the developer portal."
        actions={
          <PermissionButton
            permission="apis:write"
            onClick={() => {
              setCreating(true)
            }}
          >
            <Plus /> New API
          </PermissionButton>
        }
      />
      <DataTable<ApiSummary>
        rows={apis.data?.items}
        isPending={apis.isPending}
        error={apis.error}
        onRetry={() => void apis.refetch()}
        rowKey={(a) => a.id}
        onRowClick={(a) => void navigate(`/w/${slug}/apis/${a.id}`)}
        rowLabel={(a) => `Open API ${a.name}`}
        empty={{
          title: 'No APIs yet',
          description:
            'An API is a set of routes with an OpenAPI document that developers can find.',
        }}
        columns={[
          {
            header: 'Name',
            cell: (a) => (
              <span className="flex flex-col">
                <span className="font-medium">{a.name}</span>
                <span className="text-muted-foreground font-mono text-xs">{a.slug}</span>
              </span>
            ),
          },
          { header: 'Routes', cell: (a) => a.routes.length },
          {
            header: 'Docs',
            cell: (a) =>
              a.has_openapi ? (
                <FileCheck2 aria-label="Has an OpenAPI document" className="size-4" />
              ) : (
                '—'
              ),
          },
          { header: 'Status', cell: (a) => <StatusBadge status={a.status} /> },
        ]}
      />
      {apis.data && (
        <Pager page={page} pageSize={PAGE_SIZE} total={apis.data.total} onPage={setPage} />
      )}
      {creating && (
        <CreateApiDialog
          onClose={() => {
            setCreating(false)
          }}
          onCreate={async (body) => {
            const created = await create.mutateAsync(body)
            void navigate(`/w/${slug}/apis/${created.id}`)
            return created
          }}
        />
      )}
    </>
  )
}

const schema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(100),
  slug: z
    .string()
    .trim()
    .min(1, 'Enter a slug')
    .max(100)
    .regex(/^[a-z0-9_-]+$/, 'Lowercase letters, digits, - and _ only'),
  description: z.string().max(2000),
})
type Values = z.infer<typeof schema>

function CreateApiDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (body: Values) => Promise<ApiDetail>
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', slug: '', description: '' },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      const created = await onCreate(values)
      toast.success(`API ${created.name} created as a draft`)
      onClose()
    } catch (e) {
      setError(showServerError(form, e))
    }
  })
  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="New API"
      description="It starts as a draft: add routes and docs, then publish it."
      submitLabel="Create API"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="Name" placeholder="Orders API" />
        <TextField
          control={form.control}
          name="slug"
          label="Slug"
          placeholder="orders"
          description="Part of its portal URL; it never changes."
        />
        <TextAreaField control={form.control} name="description" label="Description" rows={3} />
      </Form>
    </FormDialog>
  )
}
