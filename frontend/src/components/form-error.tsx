import { AlertCircle } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'

/** An error for the whole form (the server refused it), under its fields. */
export function FormError({ message }: { message: string | null | undefined }) {
  if (!message) return null
  return (
    <Alert variant="destructive" role="alert">
      <AlertCircle />
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  )
}
