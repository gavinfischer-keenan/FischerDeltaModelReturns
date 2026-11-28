// What every reading page shares (#89): the region it shows (?region=, or the
// deployment's default), where that region's published files are, escaping,
// number formatting and tables. Links between the pages keep the region, and
// a link written with {region} in it points at that region's own files.
(() => {
  // The region a page opens on when its address names none: this
  // deployment's first region (as ui/src/ui/App.svelte's default).
  const DEFAULT_REGION = "sf-bay-delta";
  const q = new URLSearchParams(location.search);
  const region = q.get("region") || DEFAULT_REGION;
  /** The URL of a folder of the region's published files ("current", "nightly", "experiments", or ""). */
  const data = (sub) => new URL(`data/${region}/${sub ? sub + "/" : ""}`, location.href).href;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const fmt = (v) =>
    typeof v === "number"
      ? Number.isInteger(v) ? v.toLocaleString("en-US") : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-US") : v.toPrecision(4).replace(/\.?0+$/, "")
      : v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  /** rows: flat objects; cols: [key, heading] pairs (or keys); rowClass(r): a class, or true for "bad". */
  function table(rows, cols, rowClass) {
    if (!rows || !rows.length) return "<p class='note'>(none)</p>";
    cols = (cols || [...new Set(rows.flatMap((r) => Object.keys(r)))]).map((c) => (Array.isArray(c) ? c : [c, c]));
    const cls = (r) => { const c = rowClass ? rowClass(r) : ""; return c === true ? "bad" : c || ""; };
    const head = cols.map(([, h]) => `<th class="${typeof rows[0][cols[0][0]] === "number" ? "" : "l"}">${esc(h)}</th>`).join("");
    const body = rows.map((r) => `<tr class="${cls(r)}">` + cols.map(([k]) => `<td class="${typeof r[k] === "number" ? "" : "l"}">${esc(fmt(r[k]))}</td>`).join("") + "</tr>").join("");
    return `<table><tr>${head}</tr>${body}</table>`;
  }
  /** Say a region publishes none of this page's subject, in place of the page's own description of it. */
  function none(what) {
    const about = document.getElementById("about");
    if (about) about.hidden = true;
    return `<p>This region publishes no ${esc(what)}.</p>`;
  }
  // Links: the pages keep the region; {region} in a link is filled in.
  addEventListener("DOMContentLoaded", () => {
    for (const a of document.querySelectorAll("a[href]")) {
      const h = a.getAttribute("href");
      if (h.includes("{region}")) a.setAttribute("href", h.replaceAll("{region}", region));
      else if (/^[a-z]+\.html(#.*)?$/.test(h) && q.get("region")) {
        const [page, hash] = h.split("#");
        a.setAttribute("href", `${page}?region=${encodeURIComponent(region)}${hash ? "#" + hash : ""}`);
      }
    }
  });
  window.pages = { q, region, data, esc, fmt, table, none };
})();
