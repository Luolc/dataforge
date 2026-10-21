// The site's sidebar: fixed at the left from the lg breakpoint, and below it
// a drawer over the page that a button in the breadcrumb's bar opens.
import { PanelLeft } from "lucide-react";
import { Dialog } from "radix-ui";
import {
  createContext,
  type MouseEvent,
  type ReactNode,
  use,
  useId,
  useMemo,
  useState,
} from "react";

/** The sidebar's label, and the ID of the box its drawer opens in. */
const SidebarContext = createContext<{
  label: string;
  drawerId: string;
} | null>(null);

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
  // The drawer opens in a box that is always in the page, so that the
  // button names it by aria-controls when the drawer is closed too.
  const drawerId = useId();
  const sidebar = useMemo(() => ({ label, drawerId }), [label, drawerId]);
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  // A link followed in the drawer closes it, as the page it opens is shown.
  const followed = (event: MouseEvent) => {
    if (event.target instanceof Element && event.target.closest("a"))
      setOpen(false);
  };
  return (
    <SidebarContext value={sidebar}>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <nav
          aria-label={label}
          className={`fixed inset-y-0 left-0 hidden lg:flex ${nav}`}
        >
          {children}
        </nav>
        {page}
        {/* After the page, so that the drawer is drawn over it. */}
        <div id={drawerId} ref={setBox} className="lg:hidden" />
        <Dialog.Portal container={box}>
          <Dialog.Overlay className="fixed inset-0 lg:hidden" />
          <Dialog.Content
            aria-describedby={undefined}
            onClick={followed}
            className="fixed inset-y-0 left-0 overflow-y-auto border-r border-line bg-app text-fg lg:hidden"
          >
            <Dialog.Title className="sr-only">{label}</Dialog.Title>
            {/* As tall as the drawer, so an entry pushed to the foot, such as
                the share site's Sign out, sits at its foot. */}
            <nav aria-label={label} className={`min-h-full ${nav}`}>
              {children}
            </nav>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </SidebarContext>
  );
}

/** The button that opens the drawer, below the lg breakpoint; none outside a Sidebar. */
export function SidebarButton() {
  const sidebar = use(SidebarContext);
  if (sidebar === null) return null;
  return (
    <Dialog.Trigger
      aria-label={`Open the ${sidebar.label.toLowerCase()} menu`}
      aria-controls={sidebar.drawerId}
      className="mr-1 grid target flex-none place-items-center rounded-row hover:bg-hover lg:hidden"
    >
      <PanelLeft className="size-icon-md text-icon" aria-hidden />
    </Dialog.Trigger>
  );
}
