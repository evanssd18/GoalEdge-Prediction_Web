/**
 * GoalEdge AI — admin panel.
 *
 * A separate module from app.js for one reason: everything here runs only for an
 * authenticated administrator, and keeping it apart makes that boundary visible
 * rather than scattered through the public application code.
 *
 * The panel is reached at `#/admin` and gated twice:
 *
 *   1. The UI refuses to render unless `isAdmin` is true, so a signed-in
 *      non-admin never sees controls they cannot use.
 *   2. Every call it makes goes to an admin-gated endpoint, so the UI check is a
 *      courtesy and the server check is the actual security boundary. A reader
 *      who forces the view open gets 403s, not data.
 *
 * It deliberately does NOT import from app.js (that would be circular); the
 * caller passes in the handful of helpers it needs.
 */

/**
 * Build the admin panel.
 *
 * @param {object} deps
 * @param {Function} deps.api      - the shared fetch wrapper, already auth-aware
 * @param {Function} deps.esc      - HTML escaper
 * @param {Function} deps.icon     - inline SVG icon helper
 * @param {Function} deps.toast    - transient message
 * @param {Function} deps.confirm  - promise-returning confirm dialog
 * @param {Function} deps.applySiteDesign - push a design to the live document
 */
export function createAdminPanel(deps) {
  const { api, esc, icon, toast, confirm, applySiteDesign } = deps;

  // ---------------------------------------------------------------- state
  const admin = {
    tab: "overview",
    stats: null,
    users: null,
    usersQuery: { q: "", status: "", role: "", sort: "newest", page: 1 },
    audit: null,
    design: null,      // as stored on the server
    draft: null,       // what the editor is currently showing
    templates: null,
    loading: false,
  };

  //: Tabs, in the order an operator usually wants them.
  const TABS = [
    ["overview", "Overview", "insights"],
    ["appearance", "Appearance", "palette"],
    ["users", "Users", "groups"],
    ["maintenance", "Maintenance", "build"],
    ["audit", "Audit log", "history"],
  ];

  // ---------------------------------------------------------------- helpers

  const isDirty = () =>
    admin.design && admin.draft
      ? JSON.stringify(admin.design) !== JSON.stringify(admin.draft)
      : false;

  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString([], {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fmtAgo(iso) {
    if (!iso) return "never";
    const diff = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(diff)) return "never";
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  // ---------------------------------------------------------------- data

  async function loadAll() {
    admin.loading = true;
    try {
      const [stats, users, templates, design, audit] = await Promise.all([
        api("/admin/stats"),
        api(`/admin/users?${new URLSearchParams(cleanQuery(admin.usersQuery))}`),
        api("/site/design/templates"),
        api("/site/design"),
        api("/admin/audit?limit=40"),
      ]);
      admin.stats = stats;
      admin.users = users;
      admin.templates = templates;
      admin.design = design;
      // The draft starts as a copy of the server state; edits mutate the draft so
      // "unsaved changes" is a real comparison rather than a flag that can drift.
      admin.draft = admin.draft && isDirty() ? admin.draft : structuredClone(design);
      admin.audit = audit;
    } finally {
      admin.loading = false;
    }
  }

  function cleanQuery(q) {
    const out = { sort: q.sort, page: String(q.page) };
    if (q.q) out.q = q.q;
    if (q.status) out.status = q.status;
    if (q.role) out.role = q.role;
    return out;
  }

  async function reloadUsers() {
    admin.users = await api(
      `/admin/users?${new URLSearchParams(cleanQuery(admin.usersQuery))}`
    );
    render();
  }

  async function reloadAudit() {
    admin.audit = await api("/admin/audit?limit=40");
    render();
  }

  // ---------------------------------------------------------------- actions

  async function act(fn, label) {
    try {
      const result = await fn();
      toast(result?.detail || label);
      return result;
    } catch (err) {
      toast(err.message || "That did not work");
      return null;
    }
  }

  async function userAction(user, action, body) {
    const run = () =>
      api(`/admin/users/${user.id}/${action}`, {
        method: "POST",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    const result = await act(run, "Done");
    if (result) {
      await reloadUsers();
      await reloadAudit();
    }
  }

  async function setRole(user, patch) {
    const run = () =>
      api(`/admin/users/${user.id}/role`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
    const result = await act(run, "Role updated");
    if (result) {
      await reloadUsers();
      await reloadAudit();
    }
  }

  async function deleteUser(user) {
    const ok = await confirm({
      title: `Delete ${user.username}?`,
      body: "Their favourites are removed with them. Tracked tips are public record and are kept. This cannot be undone.",
      confirmLabel: "Delete user",
      danger: true,
    });
    if (!ok) return;
    const result = await act(
      () => api(`/admin/users/${user.id}`, { method: "DELETE" }),
      "User deleted"
    );
    if (result) {
      await reloadUsers();
      await reloadAudit();
    }
  }

  async function saveDesign() {
    const result = await act(
      () =>
        api("/admin/site/design", {
          method: "PUT",
          body: JSON.stringify(admin.draft),
        }),
      "Design saved"
    );
    if (result) {
      admin.design = result;
      admin.draft = structuredClone(result);
      applySiteDesign(result);
      try {
        localStorage.setItem("ge_design", JSON.stringify(result));
      } catch { /* private mode */ }
      await reloadAudit();
    }
    render();
  }

  async function resetDesign() {
    const ok = await confirm({
      title: "Reset the design?",
      body: "Every colour, the hover style and the layout go back to the shipped defaults. Visitors see it immediately.",
      confirmLabel: "Reset to defaults",
      danger: true,
    });
    if (!ok) return;
    const result = await act(
      () => api("/admin/site/design/reset", { method: "POST" }),
      "Design reset"
    );
    if (result) {
      admin.design = result;
      admin.draft = structuredClone(result);
      applySiteDesign(result);
      try {
        localStorage.setItem("ge_design", JSON.stringify(result));
      } catch { /* private mode */ }
      await reloadAudit();
    }
    render();
  }

  // A live preview: the draft is applied to the real document as it is edited,
  // so the operator judges the actual page rather than a thumbnail. Reverting is
  // what the "Discard" button is for.
  function previewDraft() {
    applySiteDesign(admin.draft);
  }

  function discardDraft() {
    admin.draft = structuredClone(admin.design);
    applySiteDesign(admin.design);
    render();
  }

  // ---------------------------------------------------------------- render

  function shell() {
    const tabs = TABS.map(
      ([key, label, ico]) => `
      <button class="admin-tab ${admin.tab === key ? "active" : ""}" data-admin-tab="${key}">
        ${icon(ico, "mi-sm")} ${esc(label)}
      </button>`
    ).join("");

    return `
      <div class="page-head">
        <h1>${icon("shield_person", "mi")} Admin</h1>
        <p>Manage the site, its appearance and its users.</p>
      </div>
      <div class="admin-tabs">${tabs}</div>
      <div id="admin-body"></div>`;
  }

  function overviewTab() {
    const s = admin.stats || {};
    const kpi = (label, value, sub) => `
      <div class="admin-kpi">
        <div class="kpi-label">${esc(label)}</div>
        <div class="kpi-value">${esc(String(value ?? "—"))}</div>
        ${sub ? `<div class="kpi-sub">${esc(sub)}</div>` : ""}
      </div>`;

    const byDay = s.signups_by_day || [];
    const peak = Math.max(1, ...byDay.map((d) => d.count));
    const spark = byDay
      .map(
        (d) => `
      <div title="${esc(d.date)}: ${d.count}"
           style="flex:1;display:flex;align-items:flex-end;height:44px">
        <div style="width:100%;background:var(--accent);border-radius:2px 2px 0 0;
                    height:${Math.round((d.count / peak) * 100)}%;min-height:2px"></div>
      </div>`
      )
      .join("");

    return `
      <div class="admin-grid">
        ${kpi("Total users", s.total_users, `${s.new_today ?? 0} joined today`)}
        ${kpi("Online now", s.online_now, "seen in the last 5 minutes")}
        ${kpi("Active", s.active_users, `${s.blocked_users ?? 0} blocked · ${s.kicked_users ?? 0} kicked`)}
        ${kpi("Administrators", s.admins)}
        ${kpi("New (7 days)", s.new_7d)}
        ${kpi("New (30 days)", s.new_30d)}
        ${kpi("Tracked tips", s.total_tracked_tips, "public tip record")}
      </div>

      <div class="card" style="margin-top:16px">
        <h3>Signups, last 14 days</h3>
        <div style="display:flex;gap:3px;align-items:flex-end">${spark}</div>
        <div style="display:flex;justify-content:space-between;font-size:11.5px;
                    color:var(--text-faint);margin-top:6px">
          <span>${esc(byDay[0]?.date || "")}</span>
          <span>${esc(byDay[byDay.length - 1]?.date || "")}</span>
        </div>
      </div>`;
  }

  function appearanceTab() {
    if (!admin.templates) return `<div class="loading">Loading editor…</div>`;
    const t = admin.templates;
    const d = admin.draft;

    const tokenGroups = t.token_groups
      .map(
        (g) => `
      <div class="card" style="margin-bottom:14px">
        <h3>${esc(g.label)}</h3>
        ${g.note ? `<p style="font-size:12.5px;color:var(--text-dim);margin:-6px 0 10px">${esc(g.note)}</p>` : ""}
        ${g.tokens
            .map(
              (tok) => `
          <div class="token-row">
            <span class="token-label">
              ${esc(tok.label)}
              <span class="token-code">--${esc(tok.key)}</span>
            </span>
            <input class="token-text" type="text"
                   data-token="${esc(tok.key)}"
                   value="${esc(d.colours[tok.key] || tok.default)}"
                   spellcheck="false" aria-label="${esc(tok.label)} value">
            <input class="token-swatch" type="color"
                   data-token-swatch="${esc(tok.key)}"
                   value="${esc(toHexForPicker(d.colours[tok.key] || tok.default))}"
                   aria-label="${esc(tok.label)} colour picker">
          </div>`
            )
            .join("")}
      </div>`
      )
      .join("");

    const templateSection = (title, note, items, field) => `
      <div class="card" style="margin-bottom:14px">
        <h3>${esc(title)}</h3>
        <p style="font-size:12.5px;color:var(--text-dim);margin:-6px 0 10px">${esc(note)}</p>
        <div class="template-grid">
          ${items
        .map(
          (item) => `
            <button class="template-card ${d[field] === item.key ? "selected" : ""}"
                    data-template-field="${esc(field)}" data-template-key="${esc(item.key)}">
              <span class="tpl-label">
                ${d[field] === item.key ? icon("check_circle", "mi-sm") : icon("circle", "mi-sm")}
                ${esc(item.label)}
              </span>
              ${item.note ? `<span class="tpl-note">${esc(item.note)}</span>` : ""}
            </button>`
        )
        .join("")}
        </div>
      </div>`;

    const dirty = isDirty();
    const changed = admin.design?.changed_from_default ?? 0;

    // `admin-with-savebar` reserves room for the fixed bar at the foot of the
    // page, so the last card is not trapped underneath it.
    return `
      <div class="admin-with-savebar">
      <p style="font-size:12.5px;color:var(--text-dim);margin-top:0">
        ${changed === 0
        ? "The site is using the shipped design."
        : `${changed} setting${changed === 1 ? "" : "s"} differ from the shipped design.`}
        ${admin.design?.updated_by
        ? ` Last changed by ${esc(admin.design.updated_by)} on ${esc(fmtDate(admin.design.updated_at))}.`
        : ""}
      </p>
      <p style="font-size:12.5px;color:var(--text-dim)">
        Edits preview live on this page. Nothing is visible to visitors until you save.
      </p>

      ${tokenGroups}
      ${templateSection("Card hover", "What happens when a reader points at a fixture.",
          t.hover_templates, "hover_template")}
      ${templateSection("Hover strength", "How far the chosen effect goes.",
            t.hover_intensities.map((h) => ({ ...h, note: "" })), "hover_intensity")}
      ${templateSection("Layout", "How the page is arranged.", t.layout_templates, "layout")}
      ${templateSection("Corners", "Card and control rounding.", t.radius_templates, "radius")}

      </div>
      <div class="admin-savebar">
        <button class="btn btn-primary" id="admin-save-design" ${dirty ? "" : "disabled"}>
          ${icon("save", "mi-sm")} Save design
        </button>
        <button class="btn btn-ghost" id="admin-discard-design" ${dirty ? "" : "disabled"}>
          Discard changes
        </button>
        <button class="btn btn-ghost" id="admin-reset-design">Reset to defaults</button>
        ${dirty ? `<span class="dirty-note">${icon("info", "mi-sm")} Unsaved — visitors still see the saved design.</span>` : ""}
      </div>`;
  }

  function usersTab() {
    const u = admin.users;
    if (!u) return `<div class="loading">Loading users…</div>`;
    const c = u.counts || {};
    const q = admin.usersQuery;

    const rows = u.items
      .map((user) => {
        const pill = `<span class="status-pill ${esc(user.status)}">${esc(user.status)}</span>`;
        return `
        <tr>
          <td>
            <div style="font-weight:600">${esc(user.username)}
              ${user.is_admin ? `<span class="badge badge-edge">admin</span>` : ""}
              ${user.is_premium ? `<span class="badge">premium</span>` : ""}
            </div>
            <div style="font-size:11.5px;color:var(--text-faint)">${esc(user.email)}</div>
          </td>
          <td>${pill}</td>
          <td>
            <span class="${user.is_online ? "" : "muted"}">
              <span class="online-dot ${user.is_online ? "" : "off"}"></span>
              ${user.is_online ? "online" : esc(fmtAgo(user.last_seen_at))}
            </span>
          </td>
          <td>${esc(String(user.login_count ?? 0))}</td>
          <td style="font-size:12px;color:var(--text-dim)">${esc(fmtDate(user.created_at))}</td>
          <td style="white-space:nowrap;text-align:right">
            <button class="btn btn-sm btn-ghost" data-u-block="${user.id}"
                    title="Block">${user.status === "blocked" ? "Unblock" : "Block"}</button>
            <button class="btn btn-sm btn-ghost" data-u-role="${user.id}"
                    title="Toggle admin">${user.is_admin ? "Demote" : "Promote"}</button>
            <button class="btn btn-sm btn-ghost" data-u-del="${user.id}"
                    title="Delete" style="color:var(--danger)">Delete</button>
          </td>
        </tr>`;
      })
      .join("");

    return `
      <div class="admin-grid" style="margin-bottom:14px">
        <div class="admin-kpi"><div class="kpi-label">Users</div>
          <div class="kpi-value">${esc(String(c.total ?? 0))}</div></div>
        <div class="admin-kpi"><div class="kpi-label">Online</div>
          <div class="kpi-value">${esc(String(c.online ?? 0))}</div></div>
        <div class="admin-kpi"><div class="kpi-label">Blocked</div>
          <div class="kpi-value">${esc(String(c.blocked ?? 0))}</div></div>
        <div class="admin-kpi"><div class="kpi-label">Admins</div>
          <div class="kpi-value">${esc(String(c.admins ?? 0))}</div></div>
      </div>

      <div class="card">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
          <input id="u-search" class="input" placeholder="Search email or username…"
                 value="${esc(q.q)}" style="flex:1;min-width:200px">
          <select id="u-status" class="input">
            <option value="">All statuses</option>
            <option value="active" ${q.status === "active" ? "selected" : ""}>Active</option>
            <option value="blocked" ${q.status === "blocked" ? "selected" : ""}>Blocked</option>
            <option value="kicked" ${q.status === "kicked" ? "selected" : ""}>Kicked</option>
          </select>
          <select id="u-role" class="input">
            <option value="">All roles</option>
            <option value="admin" ${q.role === "admin" ? "selected" : ""}>Admins</option>
            <option value="premium" ${q.role === "premium" ? "selected" : ""}>Premium</option>
            <option value="user" ${q.role === "user" ? "selected" : ""}>Regular</option>
          </select>
          <select id="u-sort" class="input">
            <option value="newest" ${q.sort === "newest" ? "selected" : ""}>Newest</option>
            <option value="oldest" ${q.sort === "oldest" ? "selected" : ""}>Oldest</option>
            <option value="username" ${q.sort === "username" ? "selected" : ""}>Username</option>
            <option value="last_seen" ${q.sort === "last_seen" ? "selected" : ""}>Last seen</option>
            <option value="logins" ${q.sort === "logins" ? "selected" : ""}>Logins</option>
          </select>
        </div>

        <div style="overflow-x:auto">
          <table class="admin-table">
            <thead>
              <tr>
                <th>User</th><th>Status</th><th>Last seen</th>
                <th>Logins</th><th>Joined</th><th></th>
              </tr>
            </thead>
            <tbody>${rows || `<tr><td colspan="6" class="muted">No users match.</td></tr>`}</tbody>
          </table>
        </div>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px">
          <span style="font-size:12.5px;color:var(--text-dim)">
            ${esc(String(u.total))} user${u.total === 1 ? "" : "s"} · page ${esc(String(u.page))} of ${esc(String(u.pages))}
          </span>
          <span>
            <button class="btn btn-sm btn-ghost" id="u-prev" ${u.page <= 1 ? "disabled" : ""}>Previous</button>
            <button class="btn btn-sm btn-ghost" id="u-next" ${u.page >= u.pages ? "disabled" : ""}>Next</button>
          </span>
        </div>
      </div>`;
  }

  function maintenanceTab() {
    const ROUTES = [
      ["refresh", "Refresh fixtures", "Pull the latest fixtures and results from the feed now.", false],
      ["sync", "Sync one day", "Re-fetch one day of fixtures. Safe to repeat.", false],
      ["settle", "Settle tips", "Grade tracked tips against finished matches.", false],
      ["seed", "Reseed demo data", "Regenerates the demo dataset. Overwrites existing rows.", true],
    ];
    return `
      <div class="card">
        <h3>Maintenance</h3>
        <p style="font-size:12.5px;color:var(--text-dim);margin:-6px 0 12px">
          These run against the live database. Each is recorded in the audit log.
        </p>
        ${ROUTES.map(
      ([key, label, note, danger]) => `
          <div class="token-row">
            <span class="token-label">
              ${esc(label)}
              <span class="token-code">${esc(note)}</span>
            </span>
            <button class="btn btn-sm ${danger ? "" : "btn-primary"}"
                    ${danger ? 'style="color:var(--danger)"' : ""}
                    data-maint="${key}">Run</button>
          </div>`
    ).join("")}
      </div>
      <div class="card" style="margin-top:14px">
        <h3>Result</h3>
        <pre id="maint-out" style="font-size:12px;white-space:pre-wrap;margin:0;color:var(--text-dim)">Nothing run yet.</pre>
      </div>`;
  }

  function auditTab() {
    const rows = (admin.audit || [])
      .map(
        (a) => `
      <tr class="audit-row">
        <td style="white-space:nowrap;color:var(--text-dim)">${esc(fmtDate(a.at))}</td>
        <td>${esc(a.actor)}</td>
        <td><span class="audit-action">${esc(a.action)}</span></td>
        <td>${esc(a.target || "—")}</td>
        <td style="color:var(--text-dim)">${esc(a.detail || "")}</td>
      </tr>`
      )
      .join("");
    return `
      <div class="card">
        <h3>Audit log</h3>
        <p style="font-size:12.5px;color:var(--text-dim);margin:-6px 0 12px">
          Newest first. Append-only: nothing in the panel edits or removes a row here.
        </p>
        <div style="overflow-x:auto">
          <table class="admin-table">
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
            <tbody>${rows || `<tr><td colspan="5" class="muted">Nothing recorded yet.</td></tr>`}</tbody>
          </table>
        </div>
        <button class="btn btn-sm btn-ghost" id="audit-refresh" style="margin-top:10px">Refresh</button>
      </div>`;
  }

  function body() {
    if (admin.tab === "overview") return overviewTab();
    if (admin.tab === "appearance") return appearanceTab();
    if (admin.tab === "users") return usersTab();
    if (admin.tab === "maintenance") return maintenanceTab();
    return auditTab();
  }

  /** A `#rrggbb` for the native colour input, which accepts nothing else. */
  function toHexForPicker(value) {
    const v = (value || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(v)) return v;
    if (/^#[0-9a-fA-F]{3}$/.test(v)) {
      return `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
    }
    if (/^#[0-9a-fA-F]{8}$/.test(v)) return v.slice(0, 7);
    // A named or functional colour cannot be shown in the picker; the swatch
    // shows the nearest default rather than silently overwriting the value the
    // operator typed, which is still what gets saved.
    return "#000";
  }

  // ---------------------------------------------------------------- events

  function wire(root) {
    root.querySelectorAll("[data-admin-tab]").forEach((el) =>
      el.addEventListener("click", () => {
        if (isDirty() && admin.tab === "appearance") {
          // Leaving the editor with unsaved changes reverts the preview, so the
          // operator is never looking at a page that does not match the saved
          // design without knowing it.
          admin.draft = structuredClone(admin.design);
          applySiteDesign(admin.design);
        }
        admin.tab = el.dataset.adminTab;
        render();
      })
    );

    // --- appearance ---
    root.querySelectorAll("[data-token]").forEach((el) =>
      el.addEventListener("input", () => {
        const key = el.dataset.token;
        const value = el.value.trim();
        const ok = /^(#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/]+\)|[a-zA-Z]{3,30})$/.test(value);
        el.classList.toggle("invalid", !ok);
        if (!ok) return;
        admin.draft.colours[key] = value;
        const swatch = root.querySelector(`[data-token-swatch="${key}"]`);
        if (swatch) swatch.value = toHexForPicker(value);
        previewDraft();
        syncSavebar();
      })
    );

    root.querySelectorAll("[data-token-swatch]").forEach((el) =>
      el.addEventListener("input", () => {
        const key = el.dataset.tokenSwatch;
        const value = el.value;
        admin.draft.colours[key] = value;
        const text = root.querySelector(`[data-token="${key}"]`);
        if (text) {
          text.value = value;
          text.classList.remove("invalid");
        }
        previewDraft();
        syncSavebar();
      })
    );

    root.querySelectorAll("[data-template-field]").forEach((el) =>
      el.addEventListener("click", () => {
        const field = el.dataset.templateField;
        admin.draft[field] = el.dataset.templateKey;
        previewDraft();
        render();
      })
    );

    const save = root.querySelector("#admin-save-design");
    if (save) save.addEventListener("click", saveDesign);
    const discard = root.querySelector("#admin-discard-design");
    if (discard) discard.addEventListener("click", discardDraft);
    const reset = root.querySelector("#admin-reset-design");
    if (reset) reset.addEventListener("click", resetDesign);

    // --- users ---
    const search = root.querySelector("#u-search");
    if (search) {
      let timer = null;
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          admin.usersQuery.q = search.value.trim();
          admin.usersQuery.page = 1;
          reloadUsers();
        }, 320);
      });
    }
    for (const [id, field] of [["#u-status", "status"], ["#u-role", "role"], ["#u-sort", "sort"]]) {
      const el = root.querySelector(id);
      if (el)
        el.addEventListener("change", () => {
          admin.usersQuery[field] = el.value;
          admin.usersQuery.page = 1;
          reloadUsers();
        });
    }
    const prev = root.querySelector("#u-prev");
    if (prev)
      prev.addEventListener("click", () => {
        admin.usersQuery.page = Math.max(1, admin.usersQuery.page - 1);
        reloadUsers();
      });
    const next = root.querySelector("#u-next");
    if (next)
      next.addEventListener("click", () => {
        admin.usersQuery.page += 1;
        reloadUsers();
      });

    const find = (id) => (admin.users?.items || []).find((u) => String(u.id) === id);
    root.querySelectorAll("[data-u-block]").forEach((el) =>
      el.addEventListener("click", () => {
        const user = find(el.dataset.uBlock);
        if (!user) return;
        userAction(user, user.status === "blocked" ? "unblock" : "block");
      })
    );
    root.querySelectorAll("[data-u-role]").forEach((el) =>
      el.addEventListener("click", () => {
        const user = find(el.dataset.uRole);
        if (user) setRole(user, { is_admin: !user.is_admin });
      })
    );
    root.querySelectorAll("[data-u-del]").forEach((el) =>
      el.addEventListener("click", () => {
        const user = find(el.dataset.uDel);
        if (user) deleteUser(user);
      })
    );

    // --- maintenance ---
    root.querySelectorAll("[data-maint]").forEach((el) =>
      el.addEventListener("click", async () => {
        const key = el.dataset.maint;
        const out = root.querySelector("#maint-out");
        if (key === "seed") {
          const ok = await confirm({
            title: "Reseed the demo data?",
            body: "This regenerates fixtures, teams and competitions. Existing rows are overwritten.",
            confirmLabel: "Reseed",
            danger: true,
          });
          if (!ok) return;
        }
        if (out) out.textContent = `Running ${key}…`;
        el.disabled = true;
        try {
          const path =
            key === "refresh" ? "/admin/refresh/run"
              : key === "seed" ? "/admin/seed?force=true"
                : key === "settle" ? "/admin/settle"
                  : "/admin/sync";
          const result = await api(path, key === "settle" ? {} : { method: "POST" });
          if (out) out.textContent = JSON.stringify(result, null, 2);
          toast(`${key} finished`);
          reloadAudit();
        } catch (err) {
          if (out) out.textContent = `Failed: ${err.message}`;
          toast(err.message, "error");
        } finally {
          el.disabled = false;
        }
      })
    );

    const auditRefresh = root.querySelector("#audit-refresh");
    if (auditRefresh) auditRefresh.addEventListener("click", reloadAudit);
  }

  /** Enable/disable the save bar without re-rendering the whole editor, which
   *  would steal focus from the colour field being typed into. */
  function syncSavebar() {
    const bar = document.getElementById("admin-save-design");
    const disc = document.getElementById("admin-discard-design");
    const dirty = isDirty();
    if (bar) bar.disabled = !dirty;
    if (disc) disc.disabled = !dirty;
  }

  function render() {
    const root = document.getElementById("view");
    if (!root) return;
    root.innerHTML = shell();
    root.querySelector("#admin-body").innerHTML = body();
    wire(root);
  }

  return {
    async open() {
      const root = document.getElementById("view");
      if (root) root.innerHTML = `<div class="loading"><div class="spinner"></div>Loading the panel…</div>`;
      try {
        await loadAll();
      } catch (err) {
        if (root) {
          root.innerHTML = `
            <div class="page-head"><h1>Admin</h1></div>
            <div class="card">
              <p style="margin:0">${icon("lock", "mi")} ${esc(err.message)}</p>
              <p style="font-size:12.5px;color:var(--text-dim)">
                The panel is only available to administrators.
              </p>
            </div>`;
        }
        return;
      }
      render();
    },
  };
}
