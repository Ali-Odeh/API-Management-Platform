import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/errors'
import type { Schema } from '@/api/types'
import { FormDialog } from '@/components/form-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatNumber } from '@/lib/format'
import { LIMIT_LABELS } from '@/lib/limits'
import { limitChanges, toDraft, type Draft } from './limits'
import type { PlatformWorkspace } from './queries'

/**
 * The Platform Admin sets a workspace's own limits. An empty field means
 * the platform default (shown as the placeholder).
 */
export function LimitsDialog({
  workspace,
  onClose,
  onSave,
}: {
  workspace: PlatformWorkspace
  onClose: () => void
  onSave: (body: Schema<'LimitsUpdate'>) => Promise<PlatformWorkspace>
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(workspace))
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function submit() {
    const changes = limitChanges(workspace, draft)
    if ('error' in changes) {
      setError(changes.error)
      return
    }
    if (Object.keys(changes.body).length === 0) {
      onClose()
      return
    }
    setPending(true)
    setError(null)
    try {
      await onSave(changes.body)
      toast.success(`Limits of ${workspace.name} saved`)
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={`Limits of ${workspace.name}`}
      description="Leave a field empty to use the platform's default (shown greyed). Lowering a quota removes nothing; it only refuses new objects."
      submitLabel="Save limits"
      pending={pending}
      error={error}
      onSubmit={() => void submit()}
      wide
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {LIMIT_LABELS.map(([name, label]) => (
          <div key={name} className="grid gap-2">
            <Label htmlFor={`limit-${name}`}>{label}</Label>
            <Input
              id={`limit-${name}`}
              inputMode="numeric"
              // The effective value when nothing of its own is set: the default.
              placeholder={
                workspace.limit_overrides[name] == null
                  ? `${formatNumber(workspace.limits[name])} (default)`
                  : 'Platform default'
              }
              value={draft[name]}
              onChange={(e) => {
                setDraft({ ...draft, [name]: e.target.value })
              }}
            />
          </div>
        ))}
      </div>
    </FormDialog>
  )
}
