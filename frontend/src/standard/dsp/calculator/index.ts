import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'DspCalculator',
  canonicalPath: '/dsp/calculator',
  component: () => import('./page.vue'),
}

export default page
