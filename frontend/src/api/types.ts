import type { components } from './schema'

/** The API's schemas by name, e.g. `Schema<'ServiceOut'>`. Generated from openapi.json. */
export type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]

export type Role = Schema<'Role'>
export type User = Schema<'UserOut'>
export type MyWorkspace = Schema<'MyWorkspaceOut'>
export type WorkspaceDetail = Schema<'WorkspaceDetail'>
export type WorkspaceStatus = Schema<'WorkspaceStatus'>
