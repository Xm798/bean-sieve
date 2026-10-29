import { fileURLToPath } from 'node:url';
import { createMDX } from 'fumadocs-mdx/next';

// GitHub Pages serves the project site under /<repo>.
const basePath = '/bean-sieve';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  basePath,
  // Emits <page>/index.html: Next also writes a <page>/ directory of RSC payloads, which would shadow <page>.html on GitHub Pages.
  trailingSlash: true,
  reactStrictMode: true,
  // Provider pages embed files from the repo's examples/ with <include>, which Turbopack only reads inside its root.
  turbopack: {
    root: fileURLToPath(new URL('..', import.meta.url)),
  },
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default withMDX(config);
