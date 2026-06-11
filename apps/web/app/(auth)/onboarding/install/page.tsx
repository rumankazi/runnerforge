import { Wordmark } from '@/components/brand/wordmark';
import { Button } from '@/components/ui/button';

export const metadata = { title: 'Install GitHub App — RunnerForge' };

const STEPS = [
  { label: 'Sign in with Google', done: true },
  { label: 'Install the RunnerForge GitHub App', done: false },
  { label: 'Pick repositories', done: false },
] as const;

export default function InstallPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="flex w-full max-w-sm flex-col gap-6 rounded-md bg-surface-panel p-8 machined">
        <Wordmark />
        <ol className="flex flex-col gap-2.5">
          {STEPS.map((step, i) => (
            <li key={step.label} className="flex items-center gap-2.5 text-sm">
              <span
                className={`flex size-5 items-center justify-center rounded-full font-mono text-2xs ${
                  step.done
                    ? 'bg-status-success-bg text-status-success'
                    : 'border border-hairline text-muted'
                }`}
              >
                {i + 1}
              </span>
              <span className={step.done ? 'text-muted line-through' : 'text-primary'}>
                {step.label}
              </span>
            </li>
          ))}
        </ol>
        <Button variant="primary" className="w-full" disabled>
          Install GitHub App
        </Button>
        <p className="text-center font-mono text-2xs text-muted">
          INSTALL FLOW NOT WIRED — PROTOTYPE
        </p>
      </div>
    </main>
  );
}
