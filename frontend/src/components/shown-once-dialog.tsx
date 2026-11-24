import { KeyRound } from 'lucide-react'
import { CopyButton } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

/** The new key, shown once: it is stored only as a hash. */
export function ShownOnceDialog({
  created,
  onClose,
}: {
  created: { key: string; name: string } | null
  onClose: () => void
}) {
  return (
    <Dialog
      open={created !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-5" /> Copy your key now
          </DialogTitle>
          <DialogDescription>
            This is the only time {created?.name ? `“${created.name}”` : 'the key'} is shown. It is
            stored only as a hash, so it can't be shown again.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <code
            className="bg-muted flex-1 rounded-md p-2 font-mono text-sm break-all"
            aria-label="New API key"
          >
            {created?.key}
          </code>
          {created && <CopyButton value={created.key} />}
        </div>
        <DialogFooter>
          <Button onClick={onClose}>I copied it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
