import { source } from '@/lib/source';
import { createFromSource } from 'fumadocs-core/search/server';

// Static export: the index is pre-rendered once at build time and searched in
// the browser (components/search.tsx).
export const revalidate = false;

export const { staticGET: GET } = createFromSource(source, {
  // https://docs.orama.com/docs/orama-js/supported-languages
  language: 'english',
});
