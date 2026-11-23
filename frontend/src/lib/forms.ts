import type { FieldValues, Path, UseFormReturn } from 'react-hook-form'
import { ApiError, errorMessage } from '@/api/errors'

/**
 * Shows a refused save on the form: a 422's field errors go under their
 * fields when the form has them; the rest is returned for the whole form.
 */
export function showServerError<T extends FieldValues>(
  form: UseFormReturn<T>,
  error: unknown,
): string | null {
  if (error instanceof ApiError && Object.keys(error.fields).length > 0) {
    const known = Object.keys(form.getValues())
    const leftover: string[] = []
    for (const [field, message] of Object.entries(error.fields)) {
      const root = field.split('.')[0] ?? field
      if (known.includes(root)) form.setError(field as Path<T>, { message })
      else leftover.push(`${field}: ${message}`)
    }
    return leftover.length ? leftover.join('; ') : null
  }
  return errorMessage(error)
}

/** `''` from an empty number input becomes `undefined`, other text a number. */
export function optionalNumber(value: unknown): number | undefined {
  if (value === '' || value === null || value === undefined) return undefined
  return Number(value)
}
