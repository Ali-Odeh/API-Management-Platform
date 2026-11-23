import { zodResolver } from '@hookform/resolvers/zod'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useFieldArray, useForm, type Control } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { SwitchField, TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { Button } from '@/components/ui/button'
import { Form, FormControl, FormField, FormItem, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { showServerError } from '@/lib/forms'
import type { Service, ServiceCreate, ServiceUpdate } from './queries'

const NAME = /^[a-z0-9_-]+$/
const int = (min: number, max: number) => z.coerce.number().int().min(min).max(max)

const settingsSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Enter a name')
    .max(100)
    .regex(NAME, 'Lowercase letters, digits, - and _ only'),
  timeout_ms: int(100, 60_000),
  hc_path: z
    .string()
    .trim()
    .regex(/^\/\S*$/, 'A path starting with /'),
  hc_interval_s: int(1, 300),
  hc_unhealthy_threshold: int(1, 10),
  hc_healthy_threshold: int(1, 10),
  passive_failures: int(1, 100),
  passive_eject_s: int(1, 3600),
  is_active: z.boolean(),
})

const targetSchema = z.object({
  url: z.string().trim().url('A full URL, like http://orders:9000'),
  weight: int(1, 1000),
})

const createSchema = settingsSchema.extend({
  targets: z.array(targetSchema).min(1).max(50),
})

type SettingsValues = z.input<typeof settingsSchema>
type CreateValues = z.input<typeof createSchema>

const DEFAULTS: CreateValues = {
  name: '',
  timeout_ms: 5000,
  hc_path: '/health',
  hc_interval_s: 10,
  hc_unhealthy_threshold: 3,
  hc_healthy_threshold: 2,
  passive_failures: 5,
  passive_eject_s: 30,
  is_active: true,
  targets: [{ url: '', weight: 100 }],
}

/** The settings both the create and the edit form show. */
function SettingsFields({ control }: { control: Control<SettingsValues> }) {
  const [showHealth, setShowHealth] = useState(false)
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField control={control} name="name" label="Name" placeholder="orders" />
        <TextField
          control={control}
          name="timeout_ms"
          label="Timeout (ms)"
          type="number"
          description="100 to 60,000"
        />
      </div>
      <SwitchField
        control={control}
        name="is_active"
        label="Active"
        description="An inactive service's routes get no traffic."
      />
      <Button
        type="button"
        variant="link"
        className="justify-self-start px-0"
        onClick={() => {
          setShowHealth(!showHealth)
        }}
      >
        {showHealth ? 'Hide' : 'Show'} health checks
      </Button>
      {showHealth && (
        <div className="grid gap-4 rounded-md border p-3 sm:grid-cols-2">
          <TextField control={control} name="hc_path" label="Health check path" />
          <TextField control={control} name="hc_interval_s" label="Every (s)" type="number" />
          <TextField
            control={control}
            name="hc_unhealthy_threshold"
            label="Unhealthy after failures"
            type="number"
          />
          <TextField
            control={control}
            name="hc_healthy_threshold"
            label="Healthy after successes"
            type="number"
          />
          <TextField
            control={control}
            name="passive_failures"
            label="Eject after failed requests"
            type="number"
          />
          <TextField control={control} name="passive_eject_s" label="Eject for (s)" type="number" />
        </div>
      )}
    </>
  )
}

function TargetsFields({ form }: { form: ReturnType<typeof useForm<CreateValues>> }) {
  const { fields, append, remove } = useFieldArray({ control: form.control, name: 'targets' })
  return (
    <div className="grid gap-2">
      <Label>Targets</Label>
      <p className="text-muted-foreground text-sm">
        Upstream URLs the gateway balances requests across, by weight (1 to 1,000).
      </p>
      {fields.map((field, index) => (
        <div key={field.id} className="flex items-start gap-2">
          <FormField
            control={form.control}
            name={`targets.${index}.url`}
            render={({ field: input }) => (
              <FormItem className="flex-1">
                <FormControl>
                  <Input
                    placeholder="http://orders:9000"
                    aria-label={`Target ${index + 1} URL`}
                    {...input}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name={`targets.${index}.weight`}
            render={({ field: input }) => (
              <FormItem className="w-24">
                <FormControl>
                  <Input
                    type="number"
                    aria-label={`Target ${index + 1} weight`}
                    {...input}
                    value={input.value}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Remove target ${index + 1}`}
            disabled={fields.length === 1}
            onClick={() => {
              remove(index)
            }}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="justify-self-start"
        disabled={fields.length >= 50}
        onClick={() => {
          append({ url: '', weight: 100 })
        }}
      >
        <Plus /> Add target
      </Button>
    </div>
  )
}

/** Creates a service with its first targets. */
export function CreateServiceDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (body: ServiceCreate) => Promise<Service>
}) {
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: DEFAULTS,
  })
  const [error, setError] = useState<string | null>(null)

  const submit = form.handleSubmit(async (raw) => {
    const values = createSchema.parse(raw)
    setError(null)
    try {
      const service = await onCreate(values)
      toast.success(`Service ${service.name} created`)
      form.reset(DEFAULTS)
      onOpenChange(false)
    } catch (e) {
      setError(showServerError(form, e))
    }
  })

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New service"
      description="A backend the gateway forwards requests to."
      submitLabel="Create service"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
      wide
    >
      <Form {...form}>
        {/* The create form's values are the settings plus targets. */}
        <SettingsFields control={form.control as unknown as Control<SettingsValues>} />
        <TargetsFields form={form} />
      </Form>
    </FormDialog>
  )
}

/** Edits a service's settings (its targets are edited in the targets table). */
export function EditServiceDialog({
  service,
  open,
  onOpenChange,
  onSave,
}: {
  service: Service
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (body: ServiceUpdate) => Promise<Service>
}) {
  const form = useForm<SettingsValues>({ resolver: zodResolver(settingsSchema), values: service })
  const [error, setError] = useState<string | null>(null)

  const submit = form.handleSubmit(async (raw) => {
    const parsed = settingsSchema.parse(raw)
    setError(null)
    try {
      await onSave(parsed)
      toast.success('Service saved')
      onOpenChange(false)
    } catch (e) {
      setError(showServerError(form, e))
    }
  })

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Edit ${service.name}`}
      submitLabel="Save"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
      wide
    >
      <Form {...form}>
        <SettingsFields control={form.control} />
      </Form>
    </FormDialog>
  )
}
