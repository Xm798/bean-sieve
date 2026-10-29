import Link from 'next/link';
import type { Metadata } from 'next';
import { ArrowRight, FileSearch, FileSpreadsheet, GitCompareArrows, ListPlus } from 'lucide-react';
import type { ReactNode } from 'react';
import { Logo } from '@/components/logo';
import { gitConfig } from '@/lib/shared';

export const metadata: Metadata = {
  title: { absolute: 'Bean-Sieve：Beancount 账单导入与对账' },
};

const features: { icon: ReactNode; title: string; body: string }[] = [
  {
    icon: <FileSearch />,
    title: '解析',
    body: '支付宝、微信支付、银行信用卡/借记卡等账单，统一成同一种交易格式。',
  },
  {
    icon: <GitCompareArrows />,
    title: '对账',
    body: '与已有 Beancount 账本比对，识别已经手动记过的交易。',
  },
  {
    icon: <ListPlus />,
    title: '生成',
    body: '只输出缺失的待录入条目，用规则把交易映射到账户。',
  },
  {
    icon: <FileSpreadsheet />,
    title: '导出',
    body: '不对账也能用：把各类账单统一导出为 CSV/XLSX。',
  },
];

export default function HomePage() {
  return (
    <main className="relative flex flex-1 flex-col overflow-hidden">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 -top-40 h-[480px] bg-[radial-gradient(ellipse_at_top,var(--color-fd-primary)_0%,transparent_65%)] opacity-15 dark:opacity-25"
      />

      <section className="relative mx-auto flex w-full max-w-5xl flex-col items-center px-6 pt-20 pb-14 text-center md:pt-28">
        <Logo className="mb-6 size-14 text-fd-primary" />
        <h1 className="text-4xl font-semibold tracking-tight md:text-6xl">
          对账之后，
          <span className="text-fd-primary">只补缺的</span>
        </h1>
        <p className="mt-5 max-w-2xl text-base text-fd-muted-foreground md:text-lg">
          Bean-Sieve 是基于规则的 Beancount 账单导入与对账工具。它把账单和已有账本对账，只为账本里缺失的交易生成条目，避免重复入账。
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link
            href="/docs/quick-start"
            className="inline-flex items-center gap-2 rounded-full bg-fd-primary px-5 py-2.5 text-sm font-medium text-fd-primary-foreground transition-opacity hover:opacity-90"
          >
            快速开始
            <ArrowRight className="size-4" />
          </Link>
          <Link
            href="/docs/installation"
            className="inline-flex items-center rounded-full border bg-fd-background px-5 py-2.5 text-sm font-medium transition-colors hover:bg-fd-accent"
          >
            安装
          </Link>
          <a
            href={`https://github.com/${gitConfig.user}/${gitConfig.repo}`}
            className="inline-flex items-center rounded-full border bg-fd-background px-5 py-2.5 text-sm font-medium transition-colors hover:bg-fd-accent"
          >
            GitHub
          </a>
        </div>

        <div className="mt-12 w-full max-w-2xl overflow-hidden rounded-xl border bg-fd-card text-left shadow-lg">
          <div className="flex items-center gap-1.5 border-b px-4 py-2.5">
            <span className="size-2.5 rounded-full bg-fd-muted-foreground/30" />
            <span className="size-2.5 rounded-full bg-fd-muted-foreground/30" />
            <span className="size-2.5 rounded-full bg-fd-muted-foreground/30" />
          </div>
          <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed">
            <code>
              <span className="text-fd-muted-foreground"># 安装</span>
              {'\n'}
              <span className="text-fd-primary">$</span> uv tool install beancount-sieve
              {'\n\n'}
              <span className="text-fd-muted-foreground"># 对账，生成缺失条目</span>
              {'\n'}
              <span className="text-fd-primary">$</span> bean-sieve reconcile data/statement/*.csv -l books/ -o pending.bean
            </code>
          </pre>
        </div>
      </section>

      <section className="relative mx-auto grid w-full max-w-5xl gap-4 px-6 pb-24 sm:grid-cols-2 lg:grid-cols-4">
        {features.map((f) => (
          <div key={f.title} className="rounded-xl border bg-fd-card p-5 transition-colors hover:border-fd-primary/50">
            <div className="mb-3 inline-flex rounded-lg bg-fd-primary/10 p-2 text-fd-primary [&_svg]:size-5">
              {f.icon}
            </div>
            <h2 className="mb-1.5 font-semibold">{f.title}</h2>
            <p className="text-sm text-fd-muted-foreground">{f.body}</p>
          </div>
        ))}
      </section>
    </main>
  );
}
