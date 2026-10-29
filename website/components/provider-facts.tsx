import { asMarkdown } from 'fumadocs-core/server';
import Link from 'next/link';
import type { ReactNode } from 'react';
import siteData from '@/generated/providers.json';
import { basePath, repoPathUrl } from '@/lib/shared';

interface Provider {
  id: string;
  name: string;
  category: string;
  formats: string[];
  filenameKeywords: string[];
  filenamePattern: string | null;
  contentKeywords: string[];
  // header: the first `contentHeaderChars` characters; eml: decoded subject and
  // body; pdf: the first page's text; eml-pdf: either, by the file's extension;
  // custom: a provider-specific reader (PDF, XLS, parsed HTML), which may check
  // the content without any `contentKeywords`.
  contentScope: 'header' | 'eml' | 'pdf' | 'eml-pdf' | 'custom';
  perCardStatement: boolean;
  presetRules: string[];
  example: { statements: string[] } | null;
}

interface SiteData {
  categories: { key: string; title: string }[];
  detection: { contentHeaderChars: number; filenameKeywordsIgnoreCase: boolean };
  providers: Provider[];
}

const { categories, detection: matching, providers: all } = siteData as SiteData;

function categoryTitle(key: string): string {
  const category = categories.find((c) => c.key === key);
  if (!category) throw new Error(`unknown provider category "${key}"`);
  return category.title;
}

const contentScopeLabel: Record<Provider['contentScope'], string> = {
  header: `文件前 ${matching.contentHeaderChars} 个字符`,
  eml: '邮件主题或正文',
  pdf: 'PDF 第一页的文字',
  'eml-pdf': '邮件主题或正文、PDF 第一页的文字',
  custom: '文件内容',
};

// A missing id must fail the static build rather than render an empty table.
function getProvider(id: string): Provider {
  const provider = all.find((p) => p.id === id);
  if (!provider) throw new Error(`<ProviderFacts>: unknown provider id "${id}"`);
  return provider;
}

function Codes({ items }: { items: string[] }) {
  // Nested components opt in on their own; the Markdown export keeps the rest as JSX tags.
  asMarkdown();
  return (
    <>
      {items.map((item, i) => (
        <span key={item}>
          {i > 0 && '、'}
          <code>{item}</code>
        </span>
      ))}
    </>
  );
}

function anyOf(items: string[]) {
  return (
    <>
      <Codes items={items} />
      {items.length > 1 && ' 之一'}
    </>
  );
}

function detection(p: Provider): ReactNode {
  const ext = anyOf(p.formats);
  const byName = p.filenamePattern ? (
    <>
      文件名匹配正则 <code>{p.filenamePattern}</code>
    </>
  ) : p.filenameKeywords.length > 0 ? (
    <>
      文件名含 {anyOf(p.filenameKeywords)}
      {matching.filenameKeywordsIgnoreCase && '（不区分大小写）'}
    </>
  ) : null;
  const byContent =
    p.contentKeywords.length > 0 ? (
      <>
        {contentScopeLabel[p.contentScope]}含{' '}
        {anyOf(p.contentKeywords)}
      </>
    ) : p.contentScope === 'custom' ? (
      '按文件内容识别（规则见下文）'
    ) : null;

  if (!byName && !byContent) return <>扩展名为 {ext} 即可</>;
  return (
    <>
      扩展名为 {ext}，且{byName}
      {byName && byContent && '，或'}
      {byContent}
    </>
  );
}

export function ProviderFacts({ id }: { id: string }) {
  const markdown = asMarkdown();
  const p = getProvider(id);
  const rows: [string, ReactNode][] = [
    ['Provider ID', <code key="id">{p.id}</code>],
    ['名称', p.name],
    ['分类', categoryTitle(p.category)],
    ['文件格式', <Codes key="formats" items={p.formats} />],
    ['自动识别', detection(p)],
  ];
  if (p.category !== 'payment') {
    rows.push([
      '账单范围',
      p.perCardStatement ? '每张卡单独一份账单' : '一份账单包含名下所有卡',
    ]);
  }
  rows.push([
    '预置规则',
    p.presetRules.length > 0 ? p.presetRules.join('、') : '无',
  ]);
  if (p.example) {
    rows.push([
      '示例',
      <a key="example" href={repoPathUrl('tree', `examples/${p.id}`)}>
        <code>examples/{p.id}/</code>
      </a>,
    ]);
  }

  return (
    <table>
      {/* A Markdown table needs a header row, which the HTML table leaves out. */}
      {markdown && (
        <thead>
          <tr>
            <th>项目</th>
            <th>说明</th>
          </tr>
        </thead>
      )}
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <th scope="row" className="whitespace-nowrap text-start">
              {label}
            </th>
            <td>{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ProviderList({ category }: { category: string }) {
  categoryTitle(category);
  // next/link is a client component, which the Markdown export would keep as a JSX tag.
  const markdown = asMarkdown();
  return (
    <table>
      <thead>
        <tr>
          <th>Provider ID</th>
          <th>名称</th>
          <th>文件格式</th>
        </tr>
      </thead>
      <tbody>
        {all
          .filter((p) => p.category === category)
          .map((p) => (
            <tr key={p.id}>
              <td>
                {markdown ? (
                  <a href={`${basePath}/docs/providers/${p.id}`}>
                    <code>{p.id}</code>
                  </a>
                ) : (
                  <Link href={`/docs/providers/${p.id}`}>
                    <code>{p.id}</code>
                  </Link>
                )}
              </td>
              <td>{p.name}</td>
              <td>
                <Codes items={p.formats} />
              </td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}
