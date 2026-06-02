import { GithubInfo } from '@/components/github-info';
import { baseOptions } from '@/lib/layout.shared';
import { source } from '@/lib/source';
import { DocsLayout, DocsLayoutProps } from 'fumadocs-ui/layouts/docs';

function docsOptions(): DocsLayoutProps {
  return {
    ...baseOptions(),
    tree: source.getPageTree(),
    links: [
      {
        type: 'custom',
        children: <GithubInfo owner="rumankazi" repo="runnerforge" />,
      },
    ],
  };
}

export default function Layout({ children }: LayoutProps<'/docs'>) {
  return (
    <DocsLayout tree={source.getPageTree()} {...baseOptions()}>
      {children}
    </DocsLayout>
  );
}
