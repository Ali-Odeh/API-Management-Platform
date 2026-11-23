import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
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
import { planLabel, type Plan } from '@/features/plans/queries'
import { showServerError } from '@/lib/forms'
import type { Consumer } from './queries'

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Enter a name')
    .max(100)
    .regex(/^[a-z0-9_-]+$/, 'Lowercase letters, digits, - and _ only'),
  email: z.string().trim().email('Enter a valid email'),
  plan_id: z.string(),
})
type Values = z.infer<typeof schema>

/** Creates a consumer, or edits one when `consumer` is given. */
export function ConsumerDialog({
  consumer,
  plans,
  onClose,
  onSave,
}: {
  consumer?: Consumer
  plans: Plan[]
  onClose: () => void
  onSave: (values: { name: string; email: string; plan_id?: string }) => Promise<Consumer>
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: consumer?.name ?? '',
      email: consumer?.email ?? '',
      plan_id: consumer?.plan_id ?? '',
    },
  })
  const [error, setError] = useState<string | null>(null)

  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      const saved = await onSave({ ...values, plan_id: values.plan_id || undefined })
      toast.success(`Consumer ${saved.name} ${consumer ? 'saved' : 'created'}`)
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
      title={consumer ? `Edit ${consumer.name}` : 'New consumer'}
      description="An app or customer that calls your APIs with its own keys."
      submitLabel={consumer ? 'Save' : 'Create consumer'}
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="Name" placeholder="mobile-app" />
        <TextField control={form.control} name="email" label="Contact email" type="email" />
        <FormField
          control={form.control}
          name="plan_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Plan</FormLabel>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="The default plan" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {plans.map((plan) => (
                    <SelectItem key={plan.id} value={plan.id}>
                      {planLabel(plan)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormDescription>Sets the consumer's rate limit and monthly quota.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </Form>
    </FormDialog>
  )
}
