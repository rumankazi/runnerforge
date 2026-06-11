import { ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/ui/panel';
import { HeatDot } from '@/components/ui/status-chip';

export const metadata = { title: 'Settings — RunnerForge' };

const PERMISSIONS = [
  { scope: 'actions', access: 'read' },
  { scope: 'administration', access: 'write' },
  { scope: 'metadata', access: 'read' },
] as const;

export default function SettingsPage() {
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <h1 className="stamp text-lg text-primary">SETTINGS</h1>

      <Panel
        label="GITHUB APP INSTALLATION"
        actions={
          <Button variant="secondary" size="sm" disabled>
            Configure on GitHub
            <ExternalLink size={12} strokeWidth={1.75} absoluteStrokeWidth />
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm text-primary">
            <HeatDot status="success" />
            Installed for <span className="font-mono text-xs">rumankazi</span>
            <Badge>installation_id 87654321</Badge>
          </div>
          <p className="text-xs text-muted">
            Mock state — the real install flow (Google OAuth → repo selection → GitHub App
            install) lands with the auth phase.
          </p>
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-hairline">
                <th className="stamp py-1.5 pr-4 font-normal text-2xs text-muted">PERMISSION</th>
                <th className="stamp py-1.5 font-normal text-2xs text-muted">ACCESS</th>
              </tr>
            </thead>
            <tbody>
              {PERMISSIONS.map((p) => (
                <tr key={p.scope} className="border-b border-hairline last:border-b-0">
                  <td className="py-1.5 pr-4 text-secondary">{p.scope}</td>
                  <td className="py-1.5 text-muted">{p.access}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel label="REPOSITORIES">
        <p className="text-xs text-muted">
          Repository access selection appears here once the GitHub App flow is wired.
        </p>
      </Panel>
    </div>
  );
}
