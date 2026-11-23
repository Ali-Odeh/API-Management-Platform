import type { Control, FieldPath, FieldValues } from 'react-hook-form'
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'

interface FieldProps<T extends FieldValues> {
  control: Control<T>
  name: FieldPath<T>
  label: string
  description?: React.ReactNode
}

/** A labelled text (or number) input with its validation message. */
export function TextField<T extends FieldValues>({
  control,
  name,
  label,
  description,
  ...input
}: FieldProps<T> & Omit<React.ComponentProps<typeof Input>, 'name'>) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input {...input} {...field} value={field.value ?? ''} />
          </FormControl>
          {description && <FormDescription>{description}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function TextAreaField<T extends FieldValues>({
  control,
  name,
  label,
  description,
  ...input
}: FieldProps<T> & Omit<React.ComponentProps<typeof Textarea>, 'name'>) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Textarea {...input} {...field} value={field.value ?? ''} />
          </FormControl>
          {description && <FormDescription>{description}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

/** An on/off setting with its explanation beside it. */
export function SwitchField<T extends FieldValues>({
  control,
  name,
  label,
  description,
}: FieldProps<T>) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className="flex items-start justify-between gap-4 rounded-md border p-3">
          <div className="grid gap-1">
            <FormLabel>{label}</FormLabel>
            {description && <FormDescription>{description}</FormDescription>}
            <FormMessage />
          </div>
          <FormControl>
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </FormControl>
        </FormItem>
      )}
    />
  )
}
