import Link from 'next/link';
import { Wordmark } from '@/components/brand/wordmark';

export default function HomePage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 text-center">
      <Wordmark className="scale-150" />
      <p className="max-w-md text-sm text-secondary">
        Self-hosted GitHub Actions runners on GCP. Ephemeral VMs per job, zero idle cost,
        full transparency from webhook to teardown.
      </p>
      <nav className="flex flex-wrap items-center justify-center gap-4 font-mono text-xs">
        <Link href="/dashboard" className="text-accent-text underline underline-offset-4">
          /dashboard
        </Link>
        <Link href="/docs" className="text-secondary underline underline-offset-4">
          /docs
        </Link>
        <Link href="/styleguide" className="text-secondary underline underline-offset-4">
          /styleguide
        </Link>
      </nav>
    </div>
  );
}
