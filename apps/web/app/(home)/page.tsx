import Link from 'next/link';

const links = [
  { href: '/docs/guides/getting-started', title: 'Getting started', text: 'How the beta works and how to request access.' },
  { href: '/docs/guides/runner-labels', title: 'Runner labels', text: 'Pick a VM size and Spot with runs-on.' },
  { href: '/docs/design/trust', title: 'Trust & permissions', text: 'What the GitHub App can access and what we store.' },
];

export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-16 text-center">
      <div className="max-w-2xl">
        <p className="mb-3 text-sm font-medium text-fd-muted-foreground">Private beta</p>
        <h1 className="mb-4 text-3xl font-bold sm:text-4xl">RunnerForge</h1>
        <p className="text-fd-muted-foreground">
          On-demand, ephemeral GitHub Actions runners on Google Compute Engine. Each job gets its own fresh VM,
          sized by your <code>runs-on</code> labels, and the VM is deleted when the job finishes.
        </p>
      </div>
      <div className="grid w-full max-w-3xl gap-4 sm:grid-cols-3">
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-lg border p-4 text-left transition-colors hover:bg-fd-accent"
          >
            <p className="mb-1 font-medium">{link.title}</p>
            <p className="text-sm text-fd-muted-foreground">{link.text}</p>
          </Link>
        ))}
      </div>
      <Link href="/docs" className="text-sm font-medium underline">
        Read the docs
      </Link>
    </main>
  );
}
