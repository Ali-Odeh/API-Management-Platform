# User guide

A short guide to using the platform: who does what, and how the pieces fit together.

## What it is

An **API gateway with a control panel**. Companies (called **workspaces**) put their APIs behind the gateway. The gateway checks every call: is the key valid, is the caller over their limit, where should the call go. People manage all of this from the app.

The app has three areas:

| Area | Address | For |
| --- | --- | --- |
| **Dashboard** | http://localhost:5173 | People who work in a workspace |
| **Platform** | http://localhost:5173/platform | The Platform Admin, who runs the whole platform |
| **Developer portal** | http://localhost:5173/portal | Outside developers who want to use the APIs |

API calls go to the gateway: `http://<workspace>.localhost:8081/...` with the header `X-API-Key: <key>`.

## The words you need

| Word | Meaning |
| --- | --- |
| **Workspace** | One company or team. Its data is never visible to another workspace. |
| **Service** | A backend server (or several, called **targets**) that answers the calls. |
| **Route** | A path, like `/v1/orders`. It says which service gets the call and whether a key is needed. |
| **Consumer** | Who calls your API: an app or a customer. It has **keys**. |
| **Plan** | Limits for a consumer: requests per minute and per month. |
| **API** | A group of routes with documentation, shown to developers on the portal. |
| **App** | A developer's request to use one API on one plan. Once approved, it gets keys. |

## Who can do what

| | Platform Admin | Owner | API Admin | Viewer | Developer |
| --- | :-: | :-: | :-: | :-: | :-: |
| See a workspace's pages | ✅ all | ✅ | ✅ | ✅ | — |
| Services, routes, consumers, keys, plans | ✅ | ✅ | ✅ | 👁 read | — |
| Publish APIs, approve apps | ✅ | ✅ | ✅ | 👁 read | — |
| Activity log | ✅ | ✅ | ✅ | — | — |
| Members, rename, delete the workspace | ✅ | ✅ | — | — | — |
| Create, suspend and restore workspaces, set limits | ✅ | — | — | — | — |
| Disable people, block developers | ✅ | — | — | — | — |
| Browse APIs, create apps, get own keys | — | — | — | — | ✅ |

A button you can't use is hidden. One person can have different roles in different workspaces.

### Platform Admin
Signs in with a password **and** an authenticator code (MFA). This is required for the Platform pages.
- **Workspaces:** create one (with its first owner's email), suspend it (its APIs stop and it becomes read-only), unsuspend, delete (it can be restored for 30 days), change its limits.
- **Users:** search everyone, see their workspaces and roles, disable or enable a person.
- **Developers:** block or unblock one. A blocked developer's keys stop working everywhere.
- **Activity:** every change on the platform, filterable by workspace.

### Owner
Everything an API Admin can do, plus:
- **Members:** add people by email (new people get an invitation), change their role, remove them. Keep at least two owners if you can.
- **Settings:** rename the workspace, or delete it (you type the workspace's slug to confirm).

### API Admin
Builds and runs the APIs:
- **Services:** add a backend and its targets, and set the health checks.
- **Routes:** connect a path to a service; choose the methods, whether a key is required, CORS and the body size limit.
- **Consumers:** add one, block or unblock it, generate keys (**shown only once**) and revoke them.
- **Plans:** set limits; choose which plans developers see and which need approval.
- **APIs:** group routes, add the OpenAPI document, publish or deprecate.
- **Apps:** approve or reject developers' apps.
- **Overview, Logs, Activity:** traffic, errors, every request, and every change.

### Viewer
Sees every page of the workspace (apart from Activity) and can change nothing.

### Developer (portal)
Signs up alone (an email confirms the account), then:
- **APIs:** browse, read the docs, and try a call from the page.
- **Create an app** on a plan. Some plans wait for approval.
- **My apps:** get keys (shown only once), see usage and the monthly quota, revoke a key, delete an app.

## How it fits together

```
Service ─▶ Route ─▶ (key needed?) ─▶ the gateway answers the call
                       ▲
Consumer ─ has ─▶ Key ─┘   Consumer is on a Plan (its limits)

API (routes + docs) ─ published ─▶ Portal ─▶ Developer creates an App
   ─▶ (approval if the plan needs it) ─▶ the App gets keys ─▶ calls the API
```

- A **route** needs a **service**. Delete a service's routes before deleting the service.
- Every **consumer** is on a **plan**. A plan's limit can't be higher than the workspace's limit.
- A developer's **app** is a consumer behind the scenes, so it shows up under Consumers too.
- Every change is recorded in **Activity**. Every call appears in **Logs** a few seconds later.

## Common tasks

**Put an API online (API Admin)**
1. Services → **New service**: give it a name and the backend URL.
2. Routes → **New route**: set the path and choose the service.
3. Consumers → **New consumer**, open it → **Generate key**, and copy the key.
4. Call it: `curl http://<workspace>.localhost:8081/<path> -H "X-API-Key: <key>"`

**Offer an API to developers (API Admin)**
1. Plans: turn on **Offered on the developer portal** for at least one plan.
2. APIs → **New API**: choose its routes, add the OpenAPI document, then **Publish**.
3. Apps: approve the apps that are waiting (only for plans that need approval).

**Give someone access (Owner)**
Members → **Add member**: enter their email and choose a role. A new person gets an email to set their password.

**Stop a caller at once**
Block the consumer (the gateway answers 403), or revoke one of its keys (401).

## Good to know
- **Keys are shown once.** Copy them right away. A lost key can't be shown again: revoke it and make a new one.
- **A suspended workspace** shows a banner: its APIs answer 403, and nothing can be changed until it's unsuspended.
- **MFA:** set it up under Account and security. The Platform pages require it.
- **Emails** (invitations, confirmations, password resets) arrive in Mailpit locally: http://localhost:8025
- **Demo accounts** for trying everything are in `.env.demo` (local only).
