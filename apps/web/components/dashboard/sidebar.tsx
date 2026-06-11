'use client';

import {
  Activity,
  BookOpen,
  ChevronsUpDown,
  Flag,
  Flame,
  HeartPulse,
  Settings,
  TrendingUp,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/cn';
import { Wordmark } from '@/components/brand/wordmark';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { MOCK_REPOS } from '@/lib/data/generators';
import { UserMenu } from './user-menu';

const NAV_SECTIONS = [
  {
    label: 'OPERATE',
    items: [
      { href: '/dashboard', label: 'Live', icon: Flame, exact: true },
      { href: '/dashboard/trends', label: 'Trends', icon: TrendingUp },
    ],
  },
  {
    label: 'CONFIGURE',
    items: [
      { href: '/dashboard/settings', label: 'Settings', icon: Settings },
      { href: '/dashboard/admin', label: 'Admin', icon: Flag },
    ],
  },
  {
    label: 'SYSTEM',
    items: [
      { href: '/dashboard/status', label: 'Status', icon: HeartPulse },
      { href: '/docs', label: 'Docs ↗', icon: BookOpen },
    ],
  },
] as const;

function RepoSwitcher() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="mx-3 flex h-9 items-center gap-2 rounded-sm border border-hairline bg-surface-inset px-2.5 text-left hover:bg-surface-hover">
        <Activity size={13} strokeWidth={1.75} absoluteStrokeWidth className="shrink-0 text-muted" />
        <span className="flex-1 truncate font-mono text-xs text-primary">
          rumankazi / runnerforge
        </span>
        <ChevronsUpDown size={12} strokeWidth={1.75} absoluteStrokeWidth className="shrink-0 text-muted" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="font-mono text-xs">
        <DropdownMenuLabel>REPOSITORIES</DropdownMenuLabel>
        {MOCK_REPOS.map((repo) => (
          <DropdownMenuItem key={repo} className="text-xs">
            {repo}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 hidden h-screen w-[232px] shrink-0 flex-col border-r border-hairline bg-surface-panel md:flex">
      <div className="flex h-14 items-center px-4">
        <Link
          href="/dashboard"
          className="focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <Wordmark />
        </Link>
      </div>

      <RepoSwitcher />

      <nav className="mt-4 flex flex-1 flex-col gap-5 overflow-y-auto px-3">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="flex flex-col gap-0.5">
            <div className="stamp px-2 pb-1.5 text-2xs text-muted">{section.label}</div>
            {section.items.map((item) => {
              const active =
                'exact' in item && item.exact
                  ? pathname === item.href
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'flex h-8 items-center gap-2.5 rounded-sm px-2 text-sm transition-colors duration-150',
                    'focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus',
                    active
                      ? 'bg-surface-hover text-primary shadow-[inset_2px_0_0_var(--accent)]'
                      : 'text-secondary hover:bg-surface-hover hover:text-primary',
                  )}
                >
                  <item.icon
                    size={15}
                    strokeWidth={1.75}
                    absoluteStrokeWidth
                    className={active ? 'text-accent-text' : 'text-muted'}
                  />
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="flex items-center justify-between border-t border-hairline px-3 py-2.5">
        <UserMenu />
        <ThemeToggle />
      </div>
    </aside>
  );
}
