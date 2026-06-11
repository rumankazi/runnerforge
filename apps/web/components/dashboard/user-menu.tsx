'use client';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

/** Placeholder until Google OAuth lands. */
export function UserMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-2 rounded-sm px-1.5 py-1 hover:bg-surface-hover">
        <span
          aria-hidden
          className="flex size-6 items-center justify-center rounded-full bg-ember-600/20 font-mono text-2xs text-accent-text"
        >
          RK
        </span>
        <span className="max-w-28 truncate font-mono text-2xs text-secondary">
          kaziruman@gmail.com
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>SIGNED IN (MOCK)</DropdownMenuLabel>
        <DropdownMenuItem disabled className="opacity-50">
          Account
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled className="opacity-50">
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
