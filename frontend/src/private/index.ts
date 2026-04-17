import type { RouteRecordRaw } from 'vue-router';

export interface PrivateMenuItem {
  key: string;
  title: string;
  path: string;
  requiresAuth?: boolean;
  requiresAdmin?: boolean;
}

export interface PrivateMenuSection {
  key: string;
  title: string;
  items: PrivateMenuItem[];
}

export interface PrivateFrontendModule {
  routes?: RouteRecordRaw[];
  menuSections?: PrivateMenuSection[];
}

const privateModuleFiles = import.meta.glob<{ default?: PrivateFrontendModule }>(
  './modules/*/index.ts',
  { eager: true },
);

const privateModules = Object.values(privateModuleFiles)
  .map((file) => file.default)
  .filter((module): module is PrivateFrontendModule => Boolean(module));

function normalizePrivateRoute(route: RouteRecordRaw): RouteRecordRaw {
  const meta = {
    ...(route.meta ?? {}),
  };
  const requiresAdmin = meta.requiresAdmin ?? false;
  const requiresAuth = meta.requiresAuth ?? true;

  return {
    ...route,
    meta: {
      ...meta,
      requiresAuth,
      requiresAdmin,
    },
    children: route.children?.map((child) => normalizePrivateRoute(child)),
  };
}

function normalizePrivateMenuItem(item: PrivateMenuItem): PrivateMenuItem {
  const requiresAdmin = item.requiresAdmin ?? false;
  const requiresAuth = item.requiresAuth ?? true;
  return {
    ...item,
    requiresAuth,
    requiresAdmin,
  };
}

function normalizePrivateMenuSection(section: PrivateMenuSection): PrivateMenuSection {
  return {
    ...section,
    items: section.items.map((item) => normalizePrivateMenuItem(item)),
  };
}

export const privateRoutes = privateModules.flatMap((module) =>
  (module.routes ?? []).map((route) => normalizePrivateRoute(route)),
);
export const privateMenuSections = privateModules.flatMap((module) =>
  (module.menuSections ?? []).map((section) => normalizePrivateMenuSection(section)),
);

function pathMatches(menuPath: string, currentPath: string): boolean {
  return currentPath === menuPath || currentPath.startsWith(`${menuPath}/`);
}

export function findPrivateMenuIndex(currentPath: string): string | null {
  for (const section of privateMenuSections) {
    const matchedItem = section.items.find((item) => pathMatches(item.path, currentPath));
    if (matchedItem) {
      return matchedItem.path;
    }
  }
  return null;
}

export function getDefaultPrivateOpeneds(currentPath: string): string[] {
  return privateMenuSections
    .filter((section) => section.items.some((item) => pathMatches(item.path, currentPath)))
    .map((section) => section.key);
}

export function isPrivateMenuItemVisible(
  item: PrivateMenuItem,
  isAuthenticated: boolean,
  isAdmin: boolean,
): boolean {
  if (item.requiresAdmin) {
    return isAdmin;
  }
  if (item.requiresAuth) {
    return isAuthenticated;
  }
  return true;
}
