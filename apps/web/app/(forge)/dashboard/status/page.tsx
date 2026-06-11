import { Panel } from '@/components/ui/panel';
import { Badge } from '@/components/ui/badge';
import { HeatDot } from '@/components/ui/status-chip';

export const metadata = { title: 'Status — RunnerForge' };

const COMPONENTS = [
  { name: 'Webhook orchestrator', detail: 'Cloud Run · europe-west4', uptime: '99.98%' },
  { name: 'Runner provisioning', detail: 'GCE · europe-west4-a', uptime: '99.95%' },
  { name: 'Orphan sweep', detail: 'Cloud Scheduler · every 10 min', uptime: '100%' },
  { name: 'GitHub webhook delivery', detail: 'github.com → /webhook', uptime: '99.99%' },
] as const;

export default function StatusPage() {
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <div className="flex items-center gap-3">
        <h1 className="stamp text-lg text-primary">SERVICE STATUS</h1>
        <Badge variant="accent">placeholder</Badge>
      </div>

      <Panel label="COMPONENTS — ALL SYSTEMS FORGED">
        <ul className="flex flex-col">
          {COMPONENTS.map((c) => (
            <li
              key={c.name}
              className="flex items-center gap-3 border-b border-hairline py-2.5 last:border-b-0"
            >
              <HeatDot status="success" />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-primary">{c.name}</div>
                <div className="font-mono text-2xs text-muted">{c.detail}</div>
              </div>
              <span className="font-mono text-xs text-secondary tnum">{c.uptime}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
