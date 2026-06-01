# External Access — portfolio.example.com

The web client now calls the API **same-origin** (`/api/...`), proxied to the
backend by the web container's nginx. This makes external HTTPS access work the
same as LAN access and removes the "insecure password field" warning.

## What's already done (in-repo)
- `web_client/nginx.conf`: `/api/` proxies to `portf_backend_dev:8000`.
- Web client `baseURL` is now `''` (same origin); override with
  `window.PORTF_API_BASE` if ever needed.
- Username/password login (Phase 3) gates access.

## Host steps to expose as portfolio.example.com

### 1. IONOS DNS
Add a record pointing the subdomain at your home connection (same as the other
`*.example.com` hosts):
- `pfm` → A record to your public IP (or CNAME to your existing DDNS hostname).

### 2. nginx-proxy-manager (already running on :80/:443)
Add a new **Proxy Host**:
- Domain: `portfolio.example.com`
- Scheme: `http`
- Forward host/port: `portf_web` and `80`
  (NPM and portf_web must share a docker network — add portf_web to the
  `nginx-proxy-manager` network, or use the host IP + published port `8080`.)
- Enable **Websockets**, **Block Common Exploits**.
- SSL tab: request a Let's Encrypt cert, force HTTPS (same as your other hosts).

Because the web container proxies `/api` internally, NPM only needs to forward
the single web host — no separate API route required.

### 3. Verify
- `https://portfolio.example.com` → login screen over HTTPS (no password warning).
- Sign in with your username/password.

## Security checklist before exposing
- [x] Username/password login required (Phase 3).
- [ ] Use a strong password (≥12 chars).
- [ ] Consider NPM Access List (basic auth) as a second layer.
- [ ] `SERVER_API_KEY` stays server-side; it is only returned after a valid
      password login.
- [ ] Optionally restrict the public view (Phase 5) and keep the full app behind login.
