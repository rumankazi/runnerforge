import { Panel } from '@/components/ui/panel';
import { Badge } from '@/components/ui/badge';

export const metadata = { title: 'Admin — RunnerForge' };

const FLAGS = [
  { key: 'spot_default', label: 'Default new repos to Spot VMs', on: true },
  { key: 'xlarge_machines', label: 'Allow xlarge machine label', on: true },
  { key: 'job_suggestions', label: 'Job optimization suggestions', on: false },
  { key: 'cost_estimates', label: 'Per-run cost estimates', on: false },
] as const;

export default function AdminPage() {
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <div className="flex items-center gap-3">
        <h1 className="stamp text-lg text-primary">ADMIN</h1>
        <Badge variant="accent">placeholder</Badge>
      </div>

      <Panel label="FEATURE FLAGS">
        <ul className="flex flex-col">
          {FLAGS.map((flag) => (
            <li
              key={flag.key}
              className="flex items-center justify-between gap-3 border-b border-hairline py-2.5 last:border-b-0"
            >
              <div>
                <div className="text-sm text-primary">{flag.label}</div>
                <div className="font-mono text-2xs text-muted">{flag.key}</div>
              </div>
              {/* disabled mock switch — wiring comes with the admin backend */}
              <span
                role="switch"
                aria-checked={flag.on}
                aria-disabled
                className="relative inline-flex h-4.5 w-8 cursor-not-allowed items-center rounded-full border border-hairline bg-surface-inset opacity-60"
              >
                <span
                  className={`absolute size-3 rounded-full transition-transform ${
                    flag.on ? 'translate-x-4 bg-accent' : 'translate-x-0.5 bg-iron-500'
                  }`}
                />
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
