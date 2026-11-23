import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm, type Control, type FieldPath } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { SwitchField, TextAreaField, TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { Checkbox } from '@/components/ui/checkbox'
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
import { Separator } from '@/components/ui/separator'
import type { Service } from '@/features/services/queries'
import { showServerError } from '@/lib/forms'
import { HTTP_METHODS } from '@/lib/http'
import type { Route, RouteCreate } from './queries'

/** One item per line or comma, without blanks. */
function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

const schema = z
  .object({
    path_prefix: z.string().trim().min(1, 'Enter a path').max(255).regex(/^\//, 'Starts with /'),
    service_id: z.string().min(1, 'Choose a service'),
    methods: z.array(z.string()).min(1, 'Choose at least one method'),
    strip_prefix: z.boolean(),
    auth_required: z.boolean(),
    is_active: z.boolean(),
    max_body_bytes: z.union([z.literal(''), z.coerce.number().int().min(1)]),
    cors_origins: z.string(),
    cors_methods: z.array(z.string()),
    cors_headers: z.string(),
    cors_credentials: z.boolean(),
    cors_max_age: z.coerce.number().int().min(0).max(86_400),
  })
  .refine((v) => !(v.cors_credentials && splitList(v.cors_origins).includes('*')), {
    path: ['cors_credentials'],
    message: 'Browsers refuse credentials with the origin *',
  })

type Values = z.input<typeof schema>

function toValues(route: Route | undefined, serviceId: string | undefined): Values {
  return {
    path_prefix: route?.path_prefix ?? '',
    service_id: route?.service_id ?? serviceId ?? '',
    methods: route?.methods ?? ['GET'],
    strip_prefix: route?.strip_prefix ?? false,
    auth_required: route?.auth_required ?? true,
    is_active: route?.is_active ?? true,
    max_body_bytes: route?.max_body_bytes ?? '',
    cors_origins: (route?.cors.origins ?? []).join('\n'),
    cors_methods: route?.cors.methods ?? [],
    cors_headers: (route?.cors.headers ?? []).join(', '),
    cors_credentials: route?.cors.credentials ?? false,
    cors_max_age: route?.cors.max_age ?? 600,
  }
}

function toBody(values: z.output<typeof schema>): RouteCreate & { max_body_bytes: number | null } {
  return {
    path_prefix: values.path_prefix,
    service_id: values.service_id,
    methods: values.methods,
    strip_prefix: values.strip_prefix,
    auth_required: values.auth_required,
    is_active: values.is_active,
    // Empty: the platform's default limit (null puts an edited route back on it).
    max_body_bytes: values.max_body_bytes === '' ? null : values.max_body_bytes,
    cors: {
      origins: splitList(values.cors_origins),
      methods: values.cors_methods,
      headers: splitList(values.cors_headers),
      credentials: values.cors_credentials,
      max_age: values.cors_max_age,
    },
  }
}

function MethodsField({
  control,
  name,
  label,
  description,
}: {
  control: Control<Values>
  name: FieldPath<Values>
  label: string
  description?: string
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => {
        const selected = field.value as string[]
        return (
          <FormItem>
            <FormLabel>{label}</FormLabel>
            <div className="flex flex-wrap gap-3" role="group" aria-label={label}>
              {HTTP_METHODS.map((method) => (
                <label key={method} className="flex items-center gap-1.5 font-mono text-xs">
                  <Checkbox
                    checked={selected.includes(method)}
                    onCheckedChange={(on) => {
                      field.onChange(
                        on ? [...selected, method] : selected.filter((m) => m !== method),
                      )
                    }}
                  />
                  {method}
                </label>
              ))}
            </div>
            {description && <FormDescription>{description}</FormDescription>}
            <FormMessage />
          </FormItem>
        )
      }}
    />
  )
}

/** Creates a route, or edits one when `route` is given. */
export function RouteDialog({
  open,
  onOpenChange,
  route,
  services,
  serviceId,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  route?: Route
  services: Service[]
  /** Preselects the service (on a service's page). */
  serviceId?: string
  onSave: (body: ReturnType<typeof toBody>) => Promise<Route>
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: toValues(route, serviceId),
  })
  const [error, setError] = useState<string | null>(null)

  const submit = form.handleSubmit(async (raw) => {
    setError(null)
    try {
      const saved = await onSave(toBody(schema.parse(raw)))
      toast.success(`Route ${saved.path_prefix} ${route ? 'saved' : 'created'}`)
      onOpenChange(false)
    } catch (e) {
      setError(showServerError(form, e))
    }
  })

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={route ? `Edit ${route.path_prefix}` : 'New route'}
      description="Requests whose path starts with the prefix go to the service."
      submitLabel={route ? 'Save' : 'Create route'}
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
      wide
    >
      <Form {...form}>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            control={form.control}
            name="path_prefix"
            label="Path prefix"
            placeholder="/v1/orders"
          />
          <FormField
            control={form.control}
            name="service_id"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Service</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Choose a service" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {services.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <MethodsField control={form.control} name="methods" label="Methods" />
        <div className="grid gap-3 sm:grid-cols-3">
          <SwitchField
            control={form.control}
            name="auth_required"
            label="API key required"
            description="Callers send a consumer's key."
          />
          <SwitchField
            control={form.control}
            name="strip_prefix"
            label="Strip prefix"
            description="The service gets the path without it."
          />
          <SwitchField control={form.control} name="is_active" label="Active" />
        </div>
        <TextField
          control={form.control}
          name="max_body_bytes"
          label="Body size limit (bytes)"
          type="number"
          placeholder="Platform default"
          description="Empty: the platform's default limit."
        />
        <Separator />
        <div className="grid gap-1">
          <p className="text-sm font-medium">CORS</p>
          <p className="text-muted-foreground text-sm">
            Lets web pages on other origins call this route from a browser. No origins: CORS off.
          </p>
        </div>
        <TextAreaField
          control={form.control}
          name="cors_origins"
          label="Allowed origins"
          placeholder={'https://app.example.com\n(or * for any)'}
          description="One per line: scheme, host and port, or * alone."
          rows={3}
        />
        <MethodsField
          control={form.control}
          name="cors_methods"
          label="Allowed methods"
          description="None checked: the route's own methods."
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            control={form.control}
            name="cors_headers"
            label="Allowed headers"
            placeholder="Content-Type, X-Api-Key"
          />
          <TextField
            control={form.control}
            name="cors_max_age"
            label="Preflight cache (s)"
            type="number"
          />
        </div>
        <SwitchField
          control={form.control}
          name="cors_credentials"
          label="Allow credentials"
          description="Cookies and auth headers from the browser; never with *."
        />
      </Form>
    </FormDialog>
  )
}
