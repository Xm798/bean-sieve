import { source } from '@/lib/source';
import { createFromSource } from 'fumadocs-core/search/server';

export const revalidate = false;

// The default `multilingual` tokenizer segments Chinese text; a fixed language such as 'english' would drop CJK terms.
export const { staticGET: GET } = createFromSource(source);
