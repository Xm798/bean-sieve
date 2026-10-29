import { docsLlms, llmsSource } from '@/lib/source';
import { getPageMarkdownUrl } from '@/lib/shared';
import { notFound } from 'next/navigation';

export const revalidate = false;

export async function GET(_req: Request, { params }: RouteContext<'/llms.mdx/docs/[[...slug]]'>) {
  const { slug } = await params;
  // Drops the "content.md" file name appended by getPageMarkdownUrl.
  const page = llmsSource.getPage(slug?.slice(0, -1) ?? []);
  if (!page) notFound();

  return new Response(await docsLlms.page(page), {
    headers: {
      'Content-Type': 'text/markdown; charset=utf-8',
    },
  });
}

export function generateStaticParams() {
  return llmsSource.getPages().map((page) => ({ slug: getPageMarkdownUrl(page).segments }));
}
