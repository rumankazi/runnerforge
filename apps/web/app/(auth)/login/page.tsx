import { Wordmark } from '@/components/brand/wordmark';
import { Button } from '@/components/ui/button';

export const metadata = { title: 'Sign in — RunnerForge' };

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="flex w-full max-w-sm flex-col items-center gap-6 rounded-md bg-surface-panel p-8 machined">
        <Wordmark />
        <p className="text-center text-sm text-secondary">
          Self-hosted GitHub Actions runners on GCP. Zero idle cost.
        </p>
        {/* Google OAuth lands here */}
        <Button variant="primary" className="w-full" disabled>
          Continue with Google
        </Button>
        <p className="font-mono text-2xs text-muted">AUTH NOT WIRED — PROTOTYPE</p>
      </div>
    </main>
  );
}
