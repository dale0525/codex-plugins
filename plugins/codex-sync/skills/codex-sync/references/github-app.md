# GitHub App setup

The official plugin bundles the public client ID for [dale0525-codex-sync](https://github.com/apps/dale0525-codex-sync). Users install that App with access to selected repositories and complete Device Flow on each device. A client secret or private key is not required.

Create a separate GitHub App only when maintaining a fork or self-hosted distribution:

1. Register a GitHub App in GitHub developer settings.
2. Enable Device Flow.
3. Grant repository Contents access:
   - Read-only for pull-only synchronization.
   - Read and write only when `publish` is required.
4. Install the App with **Only select repositories** and select the private configuration repository plus any private marketplace repositories.
5. Copy the App client ID. It is public; do not generate or distribute a client secret or private key.
6. Pass the client ID to `setup --github-client-id` or set `CODEX_SYNC_GITHUB_CLIENT_ID` for the setup invocation. This overrides the bundled App.

Verify the installation on GitHub's App installation page: the configuration repository must appear under selected repositories and the effective Contents permission must match the intended read-only or read/write mode. After local login, `sync` is the functional read-access check; `publish` remains separately approval-gated and is the functional write-access check.

Device authorization presents a browser URL and one-time code. The resulting user token is limited by the intersection of the App permissions, selected repositories, and the user's own access. Expiring user tokens are refreshed with the rotated refresh token when GitHub provides one.

For agent-driven setup, use `login --no-browser`. The engine flushes the URL and code before polling GitHub, allowing the caller to relay them without restarting or redirecting the authorization process.

Organization installations may require administrator approval. A `403` or `404` for a private repository can mean that the App is not installed for that repository even when browser login succeeded.
