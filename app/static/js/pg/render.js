// DOM rendering. All text goes through textContent: scraped product data is untrusted.
export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") Object.assign(n.style, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u)) ? u : null;
export const fmtTime = (iso) => { try { return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); } catch { return "unknown"; } };
export const money = (v, cur) => {
  if (v == null) return "n/a";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "INR", maximumFractionDigits: v % 1 ? 2 : 0 }).format(v); }
  catch { return `${cur || ""} ${v}`; }
};
const LABEL = { verified: "Verified at source", reported: "Reported by search · not confirmed", unavailable: "Unavailable", unretrieved: "Could not retrieve" };
export const badge = (status) => el("span", { class: `badge ${status}`, title: LABEL[status] }, LABEL[status] || status);
const initials = (s) => s.replace(/[^A-Za-z0-9& ]/g, "").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();

export function avatar(label, color) { return el("span", { class: "avatar", style: { background: color || "#3b4cca" }, "aria-hidden": "true" }, initials(label)); }

export function renderDesks(root, agents) {
  root.replaceChildren(
    el("div", { class: "desk sup" }, el("div", { class: "desk-head" }, avatar("NEXUS", "#1d2340"),
      el("div", {}, el("div", { class: "desk-name" }, "NEXUS"), el("div", { class: "desk-role" }, "Research coordinator"))),
      el("div", { class: "desk-step", id: "sup-step" }, agents.supStep || "Ready.")),
    ...Object.values(agents.map).map(a => {
      const finishedState = a.state === "done" || a.state === "blocked" || a.state === "error";
      const pct = finishedState ? 100 : (a.progress && a.progress.total ? Math.min(100, Math.round(100 * a.progress.done / a.progress.total)) : 0);
      return el("div", { class: `desk ${a.selected ? "" : "off"} ${a.state === "researching" ? "working" : ""}`, "data-agent": a.key },
        el("div", { class: "desk-head" }, avatar(a.label, a.avatar),
          el("div", {}, el("div", { class: "desk-name" }, a.label), el("div", { class: "desk-role" }, a.role))),
        el("span", { class: `pill ${a.state}` }, a.selected ? a.stateLabel : "Not needed"),
        el("div", { class: "desk-step" }, a.selected ? (a.step || a.reason || "") : a.reason),
        a.selected ? el("div", { class: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, "aria-label": `${a.label} progress` }, el("i", { style: { width: pct + "%" } })) : null,
        a.selected && a.found != null ? el("div", { class: "desk-role" }, `${a.found} listing(s)`) : null);
    }));
}

export function renderBoard(root, tasks) {
  for (const ul of root.querySelectorAll("ul")) ul.replaceChildren();
  for (const t of Object.values(tasks)) {
    const col = root.querySelector(`[data-col="${t.col}"]`);
    if (col) col.append(el("li", {}, el("b", {}, t.label), ` · ${t.note || ""}`));
  }
}

function offerCard(o, rec, runId) {
  const img = o.image && safeUrl(o.image.value) && o.image.status !== "unretrieved"
    ? el("img", { src: o.image.value, alt: o.title, loading: "lazy", referrerpolicy: "no-referrer", title: LABEL[o.image.status] })
    : el("div", { class: "noimg" }, "No verified image");
  const link = safeUrl(o.url);
  return el("article", { class: `card ${rec ? "best" : ""}` },
    rec ? el("div", {}, ...rec.map(r => el("span", { class: "badge verified" }, r))) : null,
    el("div", { class: "offer" }, img, el("div", {},
      el("div", {}, el("strong", {}, o.title)),
      el("div", { class: "desk-role" }, o.platform_label + (o.match_type === "alternative" ? " · close alternative" : "")),
      el("div", { class: "price" }, money(o.price.value, o.currency), " ", badge(o.price.status)),
      el("div", {}, o.rating.value ? `★ ${o.rating.value}/5` + (o.review_count.value ? ` (${Number(o.review_count.value).toLocaleString()} reviews)` : "") : "No rating available", " ", o.rating.value ? badge(o.rating.status) : null),
      el("div", {}, "Stock: ", o.availability.value ? o.availability.value.replace("_", " ") : "not confirmed", " ", badge(o.availability.status)),
      o.delivery.value ? el("div", { class: "desk-role" }, `Delivery (estimate, not retailer-confirmed): ${o.delivery.value}`) : null,
      link ? el("div", {}, el("a", { href: link, target: "_blank", rel: "noopener noreferrer nofollow" }, "Open product page ↗"), " ", badge(o.link.status)) : el("div", { class: "desk-role" }, "No direct link available"),
      o.trust && o.trust.level ? el("div", { class: "desk-role" }, `Site trust: ${o.trust.level}. ${(o.trust.reasons || []).join(" ")}`) : null)),
    o.pros.length ? el("ul", { class: "small-list pros" }, el("li", { class: "desk-role" }, "Advantages"), o.pros.map(p => el("li", {}, p))) : null,
    o.cons.length ? el("ul", { class: "small-list cons" }, el("li", { class: "desk-role" }, "Cautions"), o.cons.map(p => el("li", {}, p))) : null,
    Object.keys(o.specs || {}).length ? el("details", {}, el("summary", {}, "Specifications (from retailer page)"), el("ul", { class: "small-list" }, Object.entries(o.specs).slice(0, 12).map(([k, v]) => el("li", {}, `${k}: ${v}`)))) : null,
    (o.review_snippets || []).length ? el("details", {}, el("summary", {}, "Review excerpts from the page"), o.review_snippets.slice(0, 3).map(r => el("p", { class: "desk-role" }, `“${r.text}”`))) : null,
    o.flags.length ? el("div", { class: "desk-role" }, o.flags.map(f => el("div", {}, "⚠ " + f.detail))) : null,
    el("div", { class: "desk-role" }, `Retrieved ${fmtTime(o.price.retrieved_at)} · evidence score ${o.score ?? "–"}/100`));
}

export function renderReport(root, report, runId, handlers) {
  const byId = Object.fromEntries(report.offers.map(o => [o.offer_id, o]));
  const recFor = {};
  for (const [k, r] of Object.entries(report.recommendations || {})) (recFor[r.offer_id] ||= []).push(k.replace("_", " "));
  const kids = [];
  kids.push(el("div", { class: "card" }, el("strong", {}, report.product), el("p", {}, report.summary.text),
    el("div", { class: "desk-role" }, report.freshness.newest ? `Data retrieved ${fmtTime(report.freshness.oldest)} – ${fmtTime(report.freshness.newest)}` : "No data was retrieved."),
    report.location ? el("div", { class: "desk-role" }, `Location used (estimate only): ${report.location.label}`) : null,
    el("div", { class: "exports", role: "group", "aria-label": "Report documents" },
      el("a", { class: "btn", href: `/api/runs/${runId}/export?format=pdf`, download: "" }, "PDF"),
      el("button", { class: "btn", type: "button", onclick: handlers.share }, "Share PDF"))));
  if (Object.keys(report.recommendations || {}).length) {
    kids.push(el("div", {}, el("h3", {}, "Recommendations and why"),
      Object.entries(report.recommendations).map(([k, r]) => el("p", {}, el("strong", {}, k.replace("_", " ") + ": "), `${byId[r.offer_id]?.title.slice(0, 70) || ""}. ${r.reason}`))));
  }
  if (report.offers.length) {
    const rows = report.offers.map(o => el("tr", {},
      el("td", { "data-label": "Platform" }, o.platform_label), el("td", { "data-label": "Product" }, o.title.slice(0, 60)),
      el("td", { "data-label": "Price" }, money(o.price.value, o.currency), " ", badge(o.price.status)),
      el("td", { "data-label": "Rating" }, o.rating.value ? `${o.rating.value}/5` : "n/a"),
      el("td", { "data-label": "Stock" }, o.availability.value ? o.availability.value.replace("_", " ") : "unconfirmed")));
    kids.push(el("div", {}, el("h3", {}, "Comparison across platforms"), el("div", { class: "tablewrap" },
      el("table", { class: "cmp" }, el("thead", {}, el("tr", {}, ["Platform", "Product", "Price", "Rating", "Stock"].map(h => el("th", {}, h)))), el("tbody", {}, rows)))));
    kids.push(el("div", {}, el("h3", {}, `Products (${report.offers.length})`), ...report.offers.slice(0, 12).map(o => offerCard(o, recFor[o.offer_id], runId))));
  } else {
    kids.push(el("div", { class: "card" }, "No listing passed verification. See what each agent could and could not do below."));
  }
  if (report.unavailable.length) kids.push(el("div", {}, el("h3", {}, "Unavailable, not found, or could not be verified"),
    el("ul", { class: "small-list" }, report.unavailable.map(u => el("li", {}, el("strong", {}, u.platform), ({ out_of_stock: " (out of stock): ", not_found: " (no listing found): ", could_not_access: " (could not access): " })[u.kind], u.reason)))));
  if (report.excluded.length) kids.push(el("details", {}, el("summary", {}, `Excluded by verification (${report.excluded.length})`),
    el("ul", { class: "small-list" }, report.excluded.slice(0, 40).map(x => el("li", {}, `${x.platform}: ${x.title.slice(0, 60)} — ${x.reason}`)))));
  kids.push(el("details", {}, el("summary", {}, `Sources and retrieval times (${report.sources.length})`),
    el("ul", { class: "small-list" }, report.sources.map(s => el("li", {}, `${s.platform} · ${s.kind.replace("_", " ")} · `, safeUrl(s.url) ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer nofollow" }, new URL(s.url).hostname) : "", ` · ${fmtTime(s.retrieved_at)}`)))));
  if (report.limitations.length) kids.push(el("div", { class: "desk-role" }, el("strong", {}, "Limitations: "), report.limitations.join(" ")));
  root.replaceChildren(...kids);
}

export function logLine(root, ev) {
  const who = ev.agent === "supervisor" ? "NEXUS" : ev.agent || "system";
  root.prepend(el("li", {}, el("b", {}, `${who}: `), ev.message, el("span", { class: "desk-role" }, `  ${new Date(ev.at).toLocaleTimeString()}`)));
  while (root.children.length > 400) root.lastChild.remove();
}

// ---------------------------------------------------------------- Source verification panel
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
const FIELDS = [["price", "Price"], ["availability", "Stock"], ["rating", "Rating"], ["review_count", "Review count"],
  ["image", "Image"], ["link", "Product link"], ["delivery", "Delivery"]];

export function renderEvidence(root, report) {
  const kids = [];
  const counts = { verified: 0, reported: 0, unavailable: 0, unretrieved: 0 };
  for (const o of report.offers) for (const [k] of FIELDS) { const st = o[k]?.status; if (st in counts && (o[k].value != null || st !== "unretrieved")) counts[st]++; }
  kids.push(el("div", { class: "card" }, el("strong", {}, "Evidence at a glance"),
    el("p", {}, `${counts.verified} facts verified at source · ${counts.reported} reported by search only · ${counts.unretrieved} could not be retrieved.`),
    el("div", {}, Object.entries(report.evidence_legend || {}).map(([k, v]) => el("div", { class: "desk-role" }, badge(k), " ", v)))));

  const conflicts = report.offers.flatMap(o => o.flags.map(f => ({ o, f })));
  kids.push(el("div", {}, el("h3", {}, `Conflicts and cautions (${conflicts.length})`),
    conflicts.length ? conflicts.map(({ o, f }) => el("div", { class: "conflict" }, el("strong", {}, `${o.platform_label}: `), f.detail, el("span", { class: "desk-role" }, ` (${f.code})`)))
      : el("p", { class: "desk-role" }, "No conflicting or suspicious data was found in the listings that passed verification.")));

  kids.push(el("h3", {}, "Evidence per listing"));
  report.offers.forEach((o, i) => {
    const rows = FIELDS.map(([k, label]) => {
      const e = o[k] || {};
      const shown = e.value == null ? "—" : (k === "price" ? money(e.value, o.currency) : k === "image" || k === "link" ? host(String(e.value)) : String(e.value));
      const src = safeUrl(e.source_url);
      return el("tr", {}, el("td", {}, label), el("td", {}, shown), el("td", {}, badge(e.status || "unretrieved")),
        el("td", {}, src ? el("a", { href: src, target: "_blank", rel: "noopener noreferrer nofollow" }, host(src)) : "—"),
        el("td", {}, e.retrieved_at ? fmtTime(e.retrieved_at) : "—"), el("td", {}, e.note || ""));
    });
    kids.push(el("details", { class: "evgroup", open: i === 0 }, el("summary", {}, `${o.platform_label} · ${o.title.slice(0, 70)}`),
      el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Fact", "Value", "Status", "Source", "Retrieved", "Note"].map(h => el("th", {}, h)))), el("tbody", {}, rows))),
      el("p", { class: "desk-role" }, `Score ${o.score}/100 · weights ${Object.entries(o.score_parts.weights || {}).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(", ")}`)));
  });
  if (!report.offers.length) kids.push(el("p", { class: "desk-role" }, "No listing passed verification."));

  kids.push(el("div", {}, el("h3", {}, "What each agent actually did"),
    el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Agent", "Outcome", "Pages opened", "Read OK", "Limits"].map(h => el("th", {}, h)))),
      el("tbody", {}, report.agents.map(a => el("tr", {}, el("td", {}, a.label), el("td", {}, a.status.replace("_", " ")), el("td", {}, String(a.pages_attempted)), el("td", {}, String(a.pages_verified)), el("td", {}, (a.limitations || []).join(" ") || "—"))))))));

  kids.push(el("details", { class: "evgroup" }, el("summary", {}, `Excluded during NEXUS's review (${report.excluded.length})`),
    el("ul", { class: "small-list" }, report.excluded.map(x => el("li", {}, `${x.platform}: ${x.title.slice(0, 60)} — ${x.reason}`)))));
  kids.push(el("details", { class: "evgroup" }, el("summary", {}, `Verification checks (${report.verification_log.length})`),
    el("ul", { class: "small-list" }, report.verification_log.map(v => el("li", {}, `${v.ok ? "✓" : "✗"} ${v.message}`)))));
  kids.push(el("div", {}, el("h3", {}, "Assumptions and limitations"),
    el("ul", { class: "small-list" }, (report.limitations.length ? report.limitations : ["None recorded."]).map(l => el("li", {}, l)))));
  root.replaceChildren(...kids);
}
