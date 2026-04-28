import type { AppPageDefinition } from '@/router/pageRegistryTypes';

const page: AppPageDefinition = {
  routeName: 'ClusterCodexSessions',
  canonicalPath: '/cluster/codex',
  component: () => import('./page.vue'),
  requiresAuth: true,
};

export default page;
