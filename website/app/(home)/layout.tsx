import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { BookOpen } from 'lucide-react';
import { baseOptions } from '@/lib/layout.shared';

export default function Layout({ children }: LayoutProps<'/'>) {
  const base = baseOptions();
  return (
    <HomeLayout
      {...base}
      links={[{ text: '文档', url: '/docs', icon: <BookOpen />, active: 'nested-url' }, ...(base.links ?? [])]}
    >
      {children}
    </HomeLayout>
  );
}
