import { asMarkdown, md } from 'fumadocs-core/server';
import { Children, isValidElement, type ReactNode } from 'react';
import { getMDXComponents } from '@/components/mdx';

// Markdown forms for the layout components the docs use: without one, the export keeps them as JSX tags, under the
// minified component names of a production build. Each calls asMarkdown() to opt in.

function Steps({ children }: { children?: ReactNode }) {
  asMarkdown();
  const steps = Children.toArray(children).filter(isValidElement);
  return Promise.all(
    steps.map(async (step, i) => {
      const marker = `${i + 1}. `;
      return marker + (await md.indent(marker.length)`${step}`).trimStart();
    }),
  ).then((items) => `\n${items.join('\n')}\n`);
}

function Step({ children }: { children?: ReactNode }) {
  asMarkdown();
  return md`${children}`;
}

function Tabs({ children }: { children?: ReactNode }) {
  asMarkdown();
  return md`${children}`;
}

function Tab({ value, children }: { value?: string; children?: ReactNode }) {
  asMarkdown();
  return value ? md`**${value}**\n\n${children}` : md`${children}`;
}

function Cards({ children }: { children?: ReactNode }) {
  asMarkdown();
  return md`\n${children}\n`;
}

async function Card({
  title,
  href,
  children,
}: {
  title?: ReactNode;
  href?: string;
  children?: ReactNode;
}) {
  asMarkdown();
  const text = (await md`${children}`).trim().replace(/\s*\n\s*/g, ' ');
  return md`- [${title}](${href ?? ''})：${text}\n`;
}

async function Callout({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  asMarkdown();
  const text = (await md`${children}`).trim();
  return title ? md.linePrefix('> ')`**${title}**\n\n${text}` : md.linePrefix('> ')`${text}`;
}

export function getMarkdownComponents() {
  return getMDXComponents({ Steps, Step, Tabs, Tab, Cards, Card, Callout });
}
