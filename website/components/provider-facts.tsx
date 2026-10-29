import Link from 'next/link';
import type { ReactNode } from 'react';
import providers from '@/generated/providers.json';
import { gitConfig } from '@/lib/shared';

type Category = 'payment' | 'credit' | 'debit';

interface Provider {
  id: string;
  name: string;
  category: Category;
  formats: string[];
  filenameKeywords: string[];
  filenamePattern: string | null;
  contentKeywords: string[];
  perCardStatement: boolean;
  presetRules: string[];
  example: { statements: string[] } | null;
}

const all = providers as Provider[];

const categoryTitle: Record<Category, string> = {
  payment: '支付平台',
  credit: '信用卡',
  debit: '借记卡',
};

const repoUrl = `https://github.com/${gitConfig.user}/${gitConfig.repo}/tree/${gitConfig.branch}`;

// A missing id must fail the static build rather than render an empty table.
function getProvider(id: string): Provider {
  const provider = all.find((p) => p.id === id);
  if (!provider) throw new Error(`<ProviderFacts>: unknown provider id "${id}"`);
  return provider;
}

function Codes({ items }: { items: string[] }) {
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
    <>文件名含 {anyOf(p.filenameKeywords)}（不区分大小写）</>
  ) : null;
  const byContent =
    p.contentKeywords.length > 0 ? (
      <>
        {p.formats.includes('.eml') ? '邮件主题或正文' : '文件前 500 个字符'}含{' '}
        {anyOf(p.contentKeywords)}
      </>
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
  const p = getProvider(id);
  const rows: [string, ReactNode][] = [
    ['Provider ID', <code key="id">{p.id}</code>],
    ['名称', p.name],
    ['分类', categoryTitle[p.category]],
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
      <a key="example" href={`${repoUrl}/examples/${p.id}`}>
        <code>examples/{p.id}/</code>
      </a>,
    ]);
  }

  return (
    <table>
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

export function ProviderList({ category }: { category: Category }) {
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
                <Link href={`/docs/providers/${p.id}`}>
                  <code>{p.id}</code>
                </Link>
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
