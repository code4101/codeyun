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

export const privateRoutes = privateModules.flatMap((module) => module.routes ?? []);
export const privateMenuSections = privateModules.flatMap((module) => module.menuSections ?? []);

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
