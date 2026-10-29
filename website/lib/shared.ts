import { createGetUrl } from 'fumadocs-core/source';

export const appName = 'Bean-Sieve';

export const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

const gitConfig = {
  user: 'Xm798',
  repo: 'bean-sieve',
  branch: 'master',
};

export const repoUrl = `https://github.com/${gitConfig.user}/${gitConfig.repo}`;

export function repoPathUrl(kind: 'blob' | 'tree', path: string): string {
  return `${repoUrl}/${kind}/${gitConfig.branch}/${path}`;
}

export const pypiUrl = 'https://pypi.org/project/beancount-sieve/';

const getContentUrl = createGetUrl('/llms.mdx/docs');

// Each page's Markdown is a file of its own: the static export has no rewrites to serve it at /docs/<slug>.md.
export function getPageMarkdownUrl(page: { slugs: string[] }) {
  const segments = [...page.slugs, 'content.md'];
  return { segments, url: getContentUrl(segments) };
}
