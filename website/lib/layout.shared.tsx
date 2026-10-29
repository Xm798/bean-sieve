import { defineTranslations } from 'fumadocs-core/i18n';
import { uiTranslations } from 'fumadocs-ui/i18n';
import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { zhCN } from '@fumadocs/language/zh-cn';
import { Package } from 'lucide-react';
import { Logo } from '@/components/logo';
import { appName, pypiUrl, repoUrl } from './shared';

export const translations = defineTranslations().extend(uiTranslations()).preset(zhCN());

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <Logo className="size-6 text-fd-primary" />
          <span className="font-semibold tracking-tight">{appName}</span>
        </>
      ),
    },
    githubUrl: repoUrl,
    links: [
      {
        type: 'icon',
        label: 'PyPI',
        text: 'PyPI',
        icon: <Package />,
        url: pypiUrl,
        external: true,
      },
    ],
  };
}
