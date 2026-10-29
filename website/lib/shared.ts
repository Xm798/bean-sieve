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
