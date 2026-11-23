import { Menu } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { SidebarNav, type SidebarLink } from './sidebar'

/**
 * A page frame: the top bar, and a sidebar that becomes a slide-in menu on
 * small screens. `topBar` gets the menu button to place.
 */
export function Shell({
  topBar,
  sidebar,
  children,
}: {
  topBar: (menuButton: React.ReactNode) => React.ReactNode
  sidebar?: { title?: string; links: SidebarLink[] }
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const menuButton = sidebar && (
    <Button
      variant="ghost"
      size="icon"
      className="md:hidden"
      aria-label="Open menu"
      onClick={() => {
        setOpen(true)
      }}
    >
      <Menu />
    </Button>
  )
  return (
    <div className="flex min-h-svh flex-col">
      {topBar(menuButton)}
      <div className="flex flex-1">
        {sidebar && (
          <>
            <aside className="bg-sidebar sticky top-14 hidden h-[calc(100svh-3.5rem)] w-56 shrink-0 overflow-y-auto border-r md:block">
              <SidebarNav {...sidebar} />
            </aside>
            <Sheet open={open} onOpenChange={setOpen}>
              <SheetContent side="left" className="w-64 p-0">
                <SheetTitle className="sr-only">Menu</SheetTitle>
                <SidebarNav
                  {...sidebar}
                  onNavigate={() => {
                    setOpen(false)
                  }}
                />
              </SheetContent>
            </Sheet>
          </>
        )}
        <main className="min-w-0 flex-1 p-4 md:p-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  )
}
