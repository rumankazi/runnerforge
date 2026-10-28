import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  // Plain static files, served from Cloudflare Workers static assets
  // (wrangler.jsonc). No server: every route must be pre-rendered.
  output: 'export',
};

export default withMDX(config);
