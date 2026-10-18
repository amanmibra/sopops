import * as React from "react"
import { Dialog as SheetPrimitive } from "radix-ui"
import { cn } from "@/lib/utils"

/** A side panel over the page (a dialog that slides in from the right), for drill-down details. */
function Sheet(props: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />
}

function SheetContent({ className, children, ...props }: React.ComponentProps<typeof SheetPrimitive.Content>) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        className={cn(
          "bg-background fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l shadow-xl sm:max-w-[640px] data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:animate-in data-[state=open]:slide-in-from-right",
          className,
        )}
        {...props}
      >
        {children}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  )
}

const SheetTitle = SheetPrimitive.Title
const SheetDescription = SheetPrimitive.Description
const SheetClose = SheetPrimitive.Close

export { Sheet, SheetContent, SheetTitle, SheetDescription, SheetClose }
