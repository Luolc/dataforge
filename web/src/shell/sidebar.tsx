// The site's sidebar: fixed at the left from the lg breakpoint, and below it
// a drawer over the page that a button in the breadcrumb's bar opens.
import { PanelLeft } from "lucide-react";
import { Dialog } from "radix-ui";
import {
  createContext,
  type MouseEvent,
  type ReactNode,
  use,
  useState,
} from "react";

const SidebarContext = createContext<string | null>(null);

const nav = "flex w-sidebar flex-col gap-0.5 px-2 py-inset";

/**
 * children, the sidebar's entries, beside the page; page is the Outlet whose
 * frame holds the button that opens the drawer.
 */
export function Sidebar({
  label,
  children,
  page,
}: {
  label: string;
  children: ReactNode;
  page: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // A link followed in the drawer closes it, as the page it opens is shown.
  const followed = (event: MouseEvent) => {
    if (event.target instanceof Element && event.target.closest("a"))
      setOpen(false);
  };
  return (
    <SidebarContext value={label}>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <nav
          aria-label={label}
          className={`fixed inset-y-0 left-0 hidden lg:flex ${nav}`}
        >
          {children}
        </nav>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 lg:hidden" />
          <Dialog.Content
            aria-describedby={undefined}
            onClick={followed}
            className="fixed inset-y-0 left-0 overflow-y-auto border-r border-line bg-app text-fg lg:hidden"
          >
            <Dialog.Title className="sr-only">{label}</Dialog.Title>
            <nav aria-label={label} className={nav}>
              {children}
            </nav>
          </Dialog.Content>
        </Dialog.Portal>
        {page}
      </Dialog.Root>
    </SidebarContext>
  );
}

/** The button that opens the drawer, below the lg breakpoint; none outside a Sidebar. */
export function SidebarButton() {
  const label = use(SidebarContext);
  if (label === null) return null;
  return (
    <Dialog.Trigger
      aria-label={`Open the ${label.toLowerCase()} menu`}
      className="mr-1 grid target flex-none place-items-center rounded-row hover:bg-hover lg:hidden"
    >
      <PanelLeft className="size-icon-md text-icon" aria-hidden />
    </Dialog.Trigger>
  );
}
