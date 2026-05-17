import { AlertTriangle, BookOpen, type LucideIcon, Wrench } from 'lucide-react';

type RecordType = 'adr' | 'troubleshooting' | 'incident';

type AdrStatus = 'proposed' | 'accepted' | 'superseded' | 'deprecated';
type TroubleshootingStatus = 'open' | 'resolved' | 'workaround';
type IncidentStatus = 'investigating' | 'mitigated' | 'resolved' | 'postmortem';
type Severity = 'sev1' | 'sev2' | 'sev3' | 'sev4';

type RecordStatus = AdrStatus | TroubleshootingStatus | IncidentStatus;

interface RecordProps {
  type: RecordType;
  id: string;
  status?: RecordStatus;
  date?: string;
  severity?: Severity;
}

const TYPE_META: { [K in RecordType]: { label: string; icon: LucideIcon; accent: string } } = {
  adr: { label: 'ADR', icon: BookOpen, accent: 'border-l-blue-500' },
  troubleshooting: { label: 'Troubleshooting', icon: Wrench, accent: 'border-l-amber-500' },
  incident: { label: 'Incident', icon: AlertTriangle, accent: 'border-l-red-500' },
};

const STATUS_TONE: { [K in RecordStatus]: string } = {
  proposed: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  accepted: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  superseded: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-300',
  deprecated: 'bg-red-500/10 text-red-700 dark:text-red-300',
  open: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  workaround: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  resolved: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  investigating: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  mitigated: 'bg-blue-500/10 text-blue-700 dark:text-blue-300',
  postmortem: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-300',
};

const SEVERITY_TONE: { [K in Severity]: string } = {
  sev1: 'bg-red-500/15 text-red-700 dark:text-red-300',
  sev2: 'bg-orange-500/15 text-orange-700 dark:text-orange-300',
  sev3: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  sev4: 'bg-zinc-500/15 text-zinc-700 dark:text-zinc-300',
};

export function Record({ type, id, status, date, severity }: RecordProps) {
  const meta = TYPE_META[type];
  const Icon = meta.icon;
  const hasRightSide = severity || status || date;

  return (
    <div className={`mb-1 mt-8 flex flex-wrap items-center gap-2 border-l-2 pl-3 ${meta.accent}`}>
      <Icon size={14} className="shrink-0 text-muted-foreground" />
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {meta.label} {id}
      </span>
      {hasRightSide ? (
        <span className="ml-auto flex items-center gap-2">
          {severity ? (
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${SEVERITY_TONE[severity]}`}
            >
              {severity}
            </span>
          ) : null}
          {status ? (
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${STATUS_TONE[status]}`}
            >
              {status}
            </span>
          ) : null}
          {date ? <span className="text-xs text-muted-foreground">{date}</span> : null}
        </span>
      ) : null}
    </div>
  );
}
