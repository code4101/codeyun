import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuPacketCapture',
  canonicalPath: '/fanxiu/packet-capture',
  component: () => import('./page.vue'),
}

export default page
