import { useId } from 'react'
import { FormError } from '@/components/form-error'
import { Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

interface FormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: React.ReactNode
  submitLabel: string
  pending: boolean
  /** The server's refusal, for the whole form. */
  error: string | null
  onSubmit: () => void
  children: React.ReactNode
  wide?: boolean
}

/**
 * A dialog around a form: the fields are the children, the dialog owns the
 * submit and cancel buttons and the form-level error.
 */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  submitLabel,
  pending,
  error,
  onSubmit,
  children,
  wide = false,
}: FormDialogProps) {
  const formId = useId()
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!pending) onOpenChange(next)
      }}
    >
      <DialogContent className={cn('max-h-[90svh] overflow-y-auto', wide && 'sm:max-w-2xl')}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form
          id={formId}
          className="grid gap-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit()
          }}
        >
          {children}
          <FormError message={error} />
        </form>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => {
              onOpenChange(false)
            }}
          >
            Cancel
          </Button>
          <Button type="submit" form={formId} disabled={pending}>
            {pending && <Spinner />}
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
