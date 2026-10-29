'use client';
import SearchDialog from '@/components/search';
import { translations } from '@/lib/layout.shared';
import { i18nProvider } from 'fumadocs-ui/i18n';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { type ReactNode } from 'react';

export function Provider({ children }: { children: ReactNode }) {
  return (
    <RootProvider search={{ SearchDialog }} i18n={i18nProvider(translations)}>
      {children}
    </RootProvider>
  );
}
