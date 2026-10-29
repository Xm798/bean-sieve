import { llms, loader } from 'fumadocs-core/source';
import { lucideIconsPlugin } from 'fumadocs-core/source/plugins/lucide-icons';
import { defineDocs } from 'fumadocs-mdx/macro';
import { metaSchema, pageSchema } from 'fumadocs-core/source/schema';
import { getMarkdownComponents } from '@/components/mdx-markdown';
import { basePath } from '@/lib/shared';

const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: pageSchema,
    postprocess: {
      // 'function' renders JSX through the components passed to getText(), so <ProviderFacts> becomes its table.
      includeProcessedMarkdown: { output: 'function' },
    },
  },
  meta: {
    schema: metaSchema,
  },
});

export const source = loader({
  baseUrl: '/docs',
  source: docs.toFumadocsSource(),
  plugins: [lucideIconsPlugin()],
});

// Next adds basePath to links it renders, but the Markdown exports are read outside the site and need the
// published URLs.
export const llmsSource = loader({
  baseUrl: `${basePath}/docs`,
  source: docs.toFumadocsSource(),
  plugins: [lucideIconsPlugin()],
});

type LlmsPage = NonNullable<ReturnType<typeof llmsSource.getPage>>;

// Markdown links stay as authored (`./alipay.mdx`, `/docs/...`), so they are resolved here to published URLs.
function publishedLinks(markdown: string, page: LlmsPage): string {
  return markdown.replace(/\]\(((?:\.{1,2})?\/[^)\s]*)\)/g, (_, href: string) => {
    const resolved = llmsSource.resolveHref(href, page);
    const url =
      resolved.startsWith('/') && !resolved.startsWith(`${basePath}/`) ? basePath + resolved : resolved;
    return `](${url})`;
  });
}

export const docsLlms = llms(llmsSource, {
  renderPage: async (page) => {
    const body = await page.data.getText('processed', { components: getMarkdownComponents() });
    return `# ${page.data.title} (${page.url})\n\n${publishedLinks(body, page)}`;
  },
});
